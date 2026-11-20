"""Phase 3: build, fit and check LGD and EAD on the local sample (DuckDB on git-ignored data/local).

    CECL_ENV=local python -m scripts.fit_lgd_ead

LGD (cecl/lgd.py): one row per ADR-002 default; fitted on defaults up to 2016, checked on 2017 onward
(the same time split as PD); a simple benchmark (average severity by LTV band, from the training
events) shown alongside; downturn table (house prices 10/20/30% lower). EAD (cecl/ead.py): monthly
partial-prepayment rates by loan age from 1999-2016 (a fixed 1-in-10 sample of loans, for memory),
then projected vs actual balances 12/24/36 months after January 2017 for loans still current.

Writes data/local/phase3/ (events, model) and docs/phase3/results.json (aggregates only: no loan rows).
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

from cecl import ead, environment, lgd

LOCAL = Path("data/local")
OUT = LOCAL / "phase3"
RESULTS = Path("docs/phase3/results.json")
PERF = f"read_parquet('{LOCAL}/bronze/freddie_perf/**/*.parquet', hive_partitioning=true)"
ORIG = f"read_parquet('{LOCAL}/bronze/freddie_orig/**/*.parquet', hive_partitioning=true)"
PANEL = f"read_parquet('{LOCAL}/panel/**/*.parquet', hive_partitioning=true)"
HPI = (f"(select state, strftime(month, '%Y%m') as ym, value as hpi "
       f"from read_parquet('{LOCAL}/silver/macro/state_monthly.parquet') where variable = 'hpi')")  # fmt: skip


def ltv_band(x: np.ndarray) -> np.ndarray:
    edges, names = [0, 60, 80, 90, 100, 120, 1e9], ["<60", "60-80", "80-90", "90-100", "100-120", "120+"]
    out = np.full(len(x), "missing", dtype=object)
    for lo, hi, n in zip(edges[:-1], edges[1:], names, strict=True):
        out[(x >= lo) & (x < hi)] = n
    return out


def main() -> int:
    environment.current_env()  # fails loudly without CECL_ENV
    import duckdb

    OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("set memory_limit='3GB'")
    res: dict = {
        "note": "Aggregates only. Educational model on Freddie Mac conforming mortgages; not an audited reserve."
    }

    # ---------------------------------------------------------------- LGD
    ev = con.sql(lgd.event_sql(PERF, ORIG, PANEL, HPI)).df()
    ev.to_parquet(OUT / "lgd_events.parquet")
    ev["default_year"] = ev["default_ym"].str[:4]
    res["lgd_defaults_by_outcome"] = {str(k): int(v) for k, v in ev["outcome"].value_counts().items()}
    resolved = ev[ev.severity.notna()]
    usable = resolved.dropna(subset=["ltv_default", "hpi_change_12"])
    res["lgd_events"] = {"defaults": int(len(ev)), "resolved": int(len(resolved)), "usable": int(len(usable)),
                         "missing_inputs": int(len(resolved) - len(usable)),
                         "gain_on_sale_counted_as_zero": int((ev.severity < 0).sum()), "capped_above_150pct": int((ev.severity > lgd.SEVERITY_CAP[1]).sum())}  # fmt: skip
    train = usable[usable.default_year <= lgd.TRAIN_LAST]
    test = usable[usable.default_year > lgd.TRAIN_LAST]
    model = lgd.TwoStepLGD().fit(train)
    with open(OUT / "lgd_model.pkl", "wb") as fh:
        pickle.dump(model, fh)
    for name, f in (("train_to_2016", train), ("check_2017_on", test)):
        groups = {"default_year": f.default_year.to_numpy(), "outcome": f.outcome.to_numpy(),
                  "ltv_band": ltv_band(f.ltv_default.to_numpy()), "judicial": f.judicial.to_numpy(),
                  "mi": np.where(f.mi_pct.to_numpy() > 0, "with MI", "no MI")}  # fmt: skip
        res[f"lgd_{name}"] = lgd.evaluate(model, f, groups)
    # benchmark: average capped severity by LTV band from the training events
    sev_tr = np.clip(train.severity.to_numpy(), *lgd.SEVERITY_CAP)
    bands_tr = ltv_band(train.ltv_default.to_numpy())
    table = {b: float((sev_tr[bands_tr == b] * train.default_upb.to_numpy()[bands_tr == b]).sum()
                      / train.default_upb.to_numpy()[bands_tr == b].sum()) for b in set(bands_tr)}  # fmt: skip
    b_te = np.array([table.get(b, float(sev_tr.mean())) for b in ltv_band(test.ltv_default.to_numpy())])
    upb_te = test.default_upb.to_numpy()
    res["lgd_benchmark_ltv_table"] = {
        "table": table,
        "check_2017_on_predicted": float((b_te * upb_te).sum() / upb_te.sum()),
    }
    res["lgd_downturn_check_events"] = lgd.downturn(model, test)
    res["lgd_downturn_all_events"] = lgd.downturn(model, usable)

    # ---------------------------------------------------------------- EAD
    sample = f"(select * from {PERF} where hash(loan_id) % 10 = 0)"
    curt = con.sql(ead.curtailment_sql(sample, "199901", "201612")).df()
    rates = {int(r.band): float(r.rate) for r in curt.itertuples()}
    res["ead_partial_prepayment_monthly"] = [{"age_band_months": f"{lo}-{hi}", "loan_months": int(r.months), "rate": float(r.rate)}
                                             for (lo, hi), r in zip(ead.AGE_BANDS, curt.itertuples(), strict=False)]  # fmt: skip
    chk = con.sql(ead.check_sql(sample, "201701")).df()
    res["ead_check_from_2017_01"] = ead.check(chk, rates)
    with open(OUT / "ead_rates.json", "w", encoding="utf-8") as fh:
        json.dump(rates, fh)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(res, indent=1, default=float), encoding="utf-8")
    print(
        json.dumps(
            {k: v for k, v in res.items() if not k.startswith("lgd_train") and not k.startswith("lgd_check")},
            indent=1,
            default=float,
        )[:4000]
    )
    for k in ("lgd_train_to_2016", "lgd_check_2017_on"):
        r = res[k]
        print(
            k,
            r["events"],
            round(r["actual_lgd"], 3),
            round(r["predicted_lgd"], 3),
            round(r["ratio"], 2),
            round(r["any_loss_actual"], 3),
            round(r["any_loss_predicted"], 3),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
