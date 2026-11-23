# Databricks notebook source
# MAGIC %md
# MAGIC ### 68 Quarterly close: price, weight, roll forward and explain the allowance at quarter-end R (Phase 8)
# MAGIC The prod close's pricing step, with the same code as the PC (`cecl/close.py`; the PC's
# MAGIC `scripts/build_reserve.py` stays the check). It needs:
# MAGIC - the projection books for R (`results/reserve/book_<R>_<R>_*` and `book_<R>_<P>_*`, job `reserve_projection`);
# MAGIC - the previous close at P (`results/close/<P>/`: its record, its booked frame and its priced files),
# MAGIC   so the rollforward starts from exactly what was booked;
# MAGIC - the LGD model and EAD rates (`models/phase3/`) and the overlay log uploaded by the close DAG
# MAGIC   (`governance/overlay_log.json`, the committed log).
# MAGIC Loss data comes from the prod tables, pre-filtered by Spark into small frames; DuckDB then runs the same
# MAGIC SQL as on the PC. The LGD default events are rebuilt from the prod tables (`cecl.lgd.event_sql`).
# MAGIC
# MAGIC Controls (any failure stops the close, nothing is written but the failure record): every tie-out (book
# MAGIC reserve = sum of loan reserves), the rollforward reconciles to the cent, its start equals the previous
# MAGIC close's booked end, the bridge reconciles and ties to both totals.
# MAGIC Outputs (private volume): `results/close/<R>/` priced files, booked frame and `close.json` (aggregates
# MAGIC only; the DAG copies it to the VM and it is committed through a reviewed PR).
# MAGIC
# MAGIC Educational model on Freddie Mac conforming mortgages; not an audited reserve.

# COMMAND ----------

import hashlib
import json
import os
import pickle
from pathlib import Path

for name, default in (("env", ""), ("layout", "catalogs"), ("reserve_ym", ""), ("code_version", "unknown")):
    dbutils.widgets.text(name, default)
os.environ["CECL_ENV"] = dbutils.widgets.get("env")
os.environ["CECL_DATABRICKS_LAYOUT"] = dbutils.widgets.get("layout")

import duckdb
import mlflow
import pandas as pd

from cecl import close, environment, lgd, overlays, scenarios

environment.require_airflow_env()
BRONZE, SILVER, GOLD = (environment.databricks_schema(x) for x in ("bronze", "silver", "gold"))
VOLUME = "/Volumes/" + BRONZE.replace(".", "/") + "/raw"
RES, CLOSE, MODELS = f"{VOLUME}/results/reserve", f"{VOLUME}/results/close", f"{VOLUME}/models/phase3"
OVERLAY_LOG = f"{VOLUME}/governance/overlay_log.json"
SC = scenarios.SCENARIOS
R = dbutils.widgets.get("reserve_ym").strip()
if not close.is_quarter_end(R):
    raise ValueError(f"reserve_ym must be a quarter-end month (YYYYMM), got {R!r}")
P = close.prior_quarter(R)
PRIOR = f"{CLOSE}/{P}"
needed = [
    f"{PRIOR}/close.json",
    f"{PRIOR}/booked.parquet",
    OVERLAY_LOG,
    f"{MODELS}/lgd_model.pkl",
    f"{MODELS}/ead_rates.json",
]
needed += [f"{PRIOR}/priced_{P}_{P}_{s}.parquet" for s in SC]
needed += [f"{RES}/book_{R}_{E}_{s}.parquet" for E in (R, P) for s in SC]
missing = [f for f in needed if not os.path.exists(f)]
if missing:
    raise FileNotFoundError(
        f"close {R}: missing inputs {missing} (previous close not seeded, or projection not run)"
    )
prior = json.load(open(f"{PRIOR}/close.json"))
print("close", R, "from", P, "| prior total allowance", prior["dates"][P]["total_allowance"])

# COMMAND ----------

with open(f"{MODELS}/lgd_model.pkl", "rb") as fh:
    lgd_bytes = fh.read()
lgd_model = pickle.loads(lgd_bytes)
ead_bytes = open(f"{MODELS}/ead_rates.json", "rb").read()
curtail = {int(k): v for k, v in json.loads(ead_bytes).items()}
years = {scenarios.fed_year(P), scenarios.fed_year(R)}
inp = {"lgd": lgd_model, "curtail": curtail,
       "state": spark.table(f"{SILVER}.macro_state_monthly").toPandas(),
       "national": spark.table(f"{SILVER}.macro_national_monthly").toPandas(),
       "fed": {y: spark.table(f"{SILVER}.macro_{scenarios.FED_TABLES[y]}").toPandas() for y in years},
       "long_run": spark.table(f"{SILVER}.macro_long_run_averages").toPandas()}  # fmt: skip

# Priced files are kept in a work folder and reused by a re-run while the projection file is unchanged (as the
# PC caches them): a failure after the 15-minute pricing does not cost the pricing again.
WORK = f"{CLOSE}/{R}/work"
os.makedirs(WORK, exist_ok=True)
runs = {}
for s in SC:
    runs[(P, P, s)] = pd.read_parquet(f"{PRIOR}/priced_{P}_{P}_{s}.parquet")
    for E in (R, P):
        src, cache = f"{RES}/book_{R}_{E}_{s}.parquet", f"{WORK}/priced_{R}_{E}_{s}.parquet"
        if os.path.exists(cache) and os.path.getmtime(cache) > os.path.getmtime(src):
            runs[(R, E, s)] = pd.read_parquet(cache)
        else:
            book = pd.read_parquet(src)
            close.check_settings(book, src)
            weights = close.state_weights(book if E == R else runs[(P, P, s)])  # the E book's state mix
            runs[(R, E, s)] = close.price(book, R, E, s, inp, weights)
            runs[(R, E, s)].to_parquet(cache, index=False)
        print(R, E, s, f"{runs[(R, E, s)]['total'].sum():,.2f}", flush=True)

# COMMAND ----------

# Loss data: small frames from the prod tables, then the PC's SQL through DuckDB.


def to_pandas(sdf):
    """Spark -> pandas with exact decimals as doubles: DuckDB would otherwise guess a decimal width from the
    first values and fail on larger ones (found in the Phase 8 dev check, 23 Nov 2026)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import DecimalType

    cols = [F.col(f.name).cast("double").alias(f.name) if isinstance(f.dataType, DecimalType) else F.col(f.name)
            for f in sdf.schema.fields]  # fmt: skip
    return sdf.select(*cols).toPandas()


codes = ", ".join(f"'{c}'" for c in close.LOSS_CODES)
cols = ("loan_id, period, current_upb, delinquency_status, zero_balance_code, zero_balance_date, "
        "zero_balance_removal_upb, actual_loss, mi_recoveries, non_mi_recoveries")  # fmt: skip
perf = to_pandas(spark.sql(f"""select {cols} from {BRONZE}.freddie_performance
    where zero_balance_code in ({codes})
       or (period in ('{P}', '{R}') and zero_balance_code is null
           and (delinquency_status = 'RA' or try_cast(delinquency_status as int) >= 3))"""))  # fmt: skip
spark.sql(f"select loan_id, period_ym, status, current_upb from {GOLD}.loan_panel where status = 'defaulted'"
          ).createOrReplaceTempView("cecl_close_defaults")  # fmt: skip
panel_def = to_pandas(spark.table("cecl_close_defaults"))
perf_def = to_pandas(spark.sql(f"""select p.loan_id, p.period, p.zero_balance_code, p.zero_balance_date, p.delinquency_status,
        p.actual_loss, p.zero_balance_removal_upb
    from {BRONZE}.freddie_performance p left semi join cecl_close_defaults d on p.loan_id = d.loan_id"""))  # fmt: skip
orig = to_pandas(spark.sql(f"""select loan_id, property_state, orig_upb, orig_ltv, mi_pct, credit_score, occupancy_status,
        first_payment_date from {BRONZE}.freddie_origination"""))  # fmt: skip
con = duckdb.connect()
for name, frame in (("perf_close", perf), ("perf_def", perf_def), ("panel_def", panel_def), ("orig_t", orig),
                    ("state_m", inp["state"])):  # fmt: skip
    con.register(name, frame)
HPI = "(select state, strftime(month, '%Y%m') as ym, value as hpi from state_m where variable = 'hpi')"
events = con.sql(lgd.event_sql("perf_def", "orig_t", "panel_def", HPI)).df()
con.register("events_t", events)
rel = {"perf": "perf_close", "orig": "orig_t", "events": "events_t"}
print(
    "loss rows", len(perf), "| defaulted loans", panel_def["loan_id"].nunique(), "| LGD events", len(events)
)

# COMMAND ----------

log, assessed = overlays.load(Path(OVERLAY_LOG))
frames = {s: runs[(R, R, s)] for s in SC}
modelled = close.weighted({s: float(f["total"].sum()) for s, f in frames.items()})
pipe = close.defaulted_pipeline(con, rel, R, lgd_model)
dR = close.date_block(frames, pipe, R, overlays.register(log, modelled, R), assessed)
dP = prior["dates"][P]
orig_year = con.sql(
    "select loan_id, cast(substr(first_payment_date, 1, 4) as int) as vintage_year from orig_t"
).df()
booked = close.booked_frame(frames, pipe, orig_year, dR["overlays"]["booked_total"])
booked_prior = pd.read_parquet(f"{PRIOR}/booked.parquet")

first_m, last_m = close.quarter_months(R)
wo = close.losses(con, rel, first_m, last_m, lgd_model).merge(orig_year, on="loan_id", how="left")
wo["group"] = close.group_of(wo["vintage_year"].fillna(2000))
rf = close.rollforward(booked_prior, booked, wo)
rf["write_off_events"] = int(len(wo))
rf["estimated_write_off_events"] = int(wo["estimated"].sum())
rf["note"] = ("Freddie reports losses about 3 months after the sale; write-offs not yet reported are estimated "
              "(balance removed x Phase 3 LGD) and trued up when reported")  # fmt: skip
br = close.finish_bridge(close.bridge(runs, P, R), dP, dR)

year_before = f"{int(R[:4]) - 1}12"
lev = close.losses(con, rel, "199901", year_before)
bal = to_pandas(spark.sql(f"select substr(period_ym, 1, 4) as year, sum(current_upb) / 12.0 as avg_balance from {GOLD}.loan_panel "
                f"where period_ym <= '{year_before}' group by 1")).set_index("year")["avg_balance"]  # fmt: skip
ob = con.sql("select cast(substr(first_payment_date, 1, 4) as int) as vintage_year, sum(orig_upb) as orig_balance "
             "from orig_t group by 1").df().set_index("vintage_year")["orig_balance"]  # fmt: skip
bench = close.benchmarks(lev, bal, orig_year, ob, pd.read_parquet(f"{RES}/book_{R}_{R}_baseline.parquet"), R)
wo_ytd = close.losses(con, rel, f"{R[:4]}01", R, lgd_model)
disc = close.disclosures(frames["baseline"], wo_ytd, wo.drop(columns=["vintage_year", "group"]), orig_year, R)
disc["rollforward_by_loan_group"] = {close.quarter_tag(R): rf["by_group"]}

# The live rolling backtest: every earlier booked quarter's allowance vs the losses reported since on its loans.
reported = close.losses(con, rel, "199901", R)
history, realised = [], {}
for ym in sorted(os.listdir(CLOSE)):
    rec, frame = f"{CLOSE}/{ym}/close.json", f"{CLOSE}/{ym}/booked.parquet"
    if ym < R and close.is_quarter_end(ym) and os.path.exists(rec) and os.path.exists(frame):
        total = json.load(open(rec))["dates"][ym]["total_allowance"]
        loans = set(pd.read_parquet(frame, columns=["loan_id"])["loan_id"])
        after = reported[(reported["disp_ym"] > ym) & reported["loan_id"].isin(loans)]
        history.append({"reserve_ym": ym, "total_allowance": total})
        realised[ym] = float(after["net_write_off"].sum())
adequacy = close.reserve_adequacy(history, realised, R)

# COMMAND ----------

prior_end = (prior.get("rollforward") or {}).get("total", {}).get("end")
controls = {
    "tie_outs": all(t["passes"] for t in dR["tie_outs"].values()),
    "rollforward_reconciles_to_the_cent": rf["reconciles_to_the_cent"],
    "rollforward_start_equals_previous_booked_end": abs(
        float(booked_prior["reserve"].sum()) - dP["total_allowance"]
    )
    < 0.01
    and (prior_end is None or prior_end == rf["total"]["start"]),
    "bridge_reconciles": br["reconciles"],
    "bridge_ties_to_both_totals": br["ties_to_total_allowance"],
}
controls["all_pass"] = all(controls.values())
os.makedirs(f"{CLOSE}/{R}", exist_ok=True)
if not controls["all_pass"]:
    with open(f"{CLOSE}/{R}/controls_failed.json", "w") as fh:
        json.dump(
            {"controls": controls, "rollforward": rf["total"], "bridge": br}, fh, indent=1, default=float
        )
    raise RuntimeError(f"close {R}: controls failed {[k for k, v in controls.items() if not v]}")


def delta_version(table):
    return int(spark.sql(f"describe history {table} limit 1").collect()[0]["version"])


mlflow.set_registry_uri("databricks-uc")
client = mlflow.MlflowClient()
models = {}
for step in ("current", "30", "60"):
    name = environment.registered_model(f"pd_v2_transition_{step}")
    v = client.get_model_version_by_alias(name, environment.CHALLENGER)
    models[name] = {"alias": environment.CHALLENGER, "version": str(v.version), "run_id": v.run_id}
tables = [f"{BRONZE}.freddie_performance", f"{BRONZE}.freddie_origination", f"{GOLD}.loan_panel"]
tables += [f"{SILVER}.macro_{t}" for t in ("state_monthly", "national_monthly", "long_run_averages")]
tables += [f"{SILVER}.macro_{scenarios.FED_TABLES[y]}" for y in sorted(years)]
snapshot = {"delta_versions": {t: delta_version(t) for t in tables}, "pd_models": models,
            "lgd_model_sha256": hashlib.sha256(lgd_bytes).hexdigest(), "ead_rates_sha256": hashlib.sha256(ead_bytes).hexdigest(),
            "overlay_log_sha256": hashlib.sha256(open(OVERLAY_LOG, "rb").read()).hexdigest(),
            "code_version": dbutils.widgets.get("code_version")}  # fmt: skip

res = {"note": "Aggregates only. Educational model on Freddie Mac conforming mortgages (sample book); not an "
               "audited bank reserve and not accounting advice.",
       "model": "PD/prepayment M-01 v2 @challenger (holdout failed, L-038; check bias L-039); LGD M-02; EAD M-03",
       "reserve_ym": R, "prior_ym": P, "weights": close.WEIGHTS, "uplift_sensitivity": close.UPLIFT,
       "forecast_months": close.FORECAST_MONTHS, "reversion_months": close.REVERSION_MONTHS,
       "dates": {P: dP, R: dR}, "rollforward": rf, "bridge": br, "disclosures": disc, "benchmarks": bench,
       "reserve_adequacy": adequacy, "snapshot": snapshot, "controls": controls}  # fmt: skip
for s in SC:
    for E in (R, P):
        runs[(R, E, s)].to_parquet(f"{CLOSE}/{R}/priced_{R}_{E}_{s}.parquet", index=False)
booked.to_parquet(f"{CLOSE}/{R}/booked.parquet", index=False)
with open(f"{CLOSE}/{R}/close.json", "w") as fh:
    json.dump(res, fh, indent=1, default=float)
summary = {"reserve_ym": R, "total_allowance": dR["total_allowance"], "weighted_reserve": dR["weighted_reserve"],
           "defaulted_reserve": dR["defaulted_pipeline"]["reserve"], "rollforward": rf["total"], "controls": controls,
           "snapshot": snapshot["delta_versions"]}  # fmt: skip
print(json.dumps(summary, indent=1, default=float))
dbutils.notebook.exit(json.dumps(summary, default=float))
