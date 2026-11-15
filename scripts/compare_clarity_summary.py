"""Phase 1 step 6: compare our Freddie sample with Freddie's Clarity summary statistics (Release 48).

Inputs (all local, git-ignored):
- data/raw/freddie/reference/freddie_r48_origination_summary_by_vintage_all.xlsx
- data/raw/freddie/reference/freddie_r48_performance_summary_by_vintage_all_upb_pct.xlsx
  (Dataset Type = All, 49,483,298 loans; performance rates are weighted by original balance)
- bronze Parquet (data/local/bronze) and the panel (data/local/panel)

Outputs: a markdown report (aggregates only: averages, shares, z-scores) and a JSON copy of the numbers.

Vintage is the origination year in the loan ID (L-018). "Freddie's way" uses Freddie's own codes with
no stop-the-clock: Default = loan-ending codes 02, 03, 09, 15; Ever D90+ = ever reported 3+ months
late. Our ADR-002 default (from the panel) is shown alongside.

    python -m scripts.compare_clarity_summary
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cecl.sampling import Estimate, verdict, weighted_mean, z_score

REF = Path("data/raw/freddie/reference")
ORIG_XLSX = REF / "freddie_r48_origination_summary_by_vintage_all.xlsx"
PERF_XLSX = REF / "freddie_r48_performance_summary_by_vintage_all_upb_pct.xlsx"
BRONZE = Path("data/local/bronze")
PANEL = Path("data/local/panel")
REPORT = Path(
    "docs/phase1/step6_freddie_tables.md"
)  # tables only; the write-up is step6_freddie_reproduction.md
NUMBERS = Path("data/local/step6_comparison.json")
# The full side-by-side with Freddie's figures stays local: Clarity's figures are not republished.
SIDE_BY_SIDE = Path("data/local/step6_side_by_side.md")

LOANS_SQL = """
with perf as (
    select loan_id,
           max(zero_balance_code) as zbc,
           max(case when delinquency_status not in ('RA', 'XX') then cast(delinquency_status as integer) end)
               as max_late,
           bool_or(borrower_assistance_status = 'F') as ever_frb,
           bool_or(modification_flag in ('Y', 'P')) as ever_mod
    from read_parquet('{bronze}/freddie_perf/*/*.parquet')
    group by loan_id
),
panel_final as (
    select loan_id, status as adr002_final
    from read_parquet('{panel}/*/*.parquet')
    where status in ('defaulted', 'prepaid', 'censored')
)
select
    case when substr(o.loan_id, 2, 2) = '99' then 1999 else 2000 + cast(substr(o.loan_id, 2, 2) as integer) end
        as vintage,
    cast(substr(o.loan_id, 5, 1) as integer) as quarter,
    cast(o.orig_upb as double) as upb,
    cast(o.orig_interest_rate as double) as rate,
    nullif(o.orig_ltv, 999) as oltv,
    nullif(o.orig_cltv, 999) as cltv,
    nullif(o.credit_score, 9999) as fico,
    nullif(o.orig_dti, 999) as dti,
    o.occupancy_status = 'I' as investor,
    o.loan_purpose = 'C' as cash_out,
    p.zbc, p.max_late, coalesce(p.ever_frb, false) as ever_frb, coalesce(p.ever_mod, false) as ever_mod,
    f.adr002_final
from read_parquet('{bronze}/freddie_orig/*/*.parquet') o
left join perf p using (loan_id)
left join panel_final f using (loan_id)
"""

# column in the Clarity file -> (our name, how to compute it from one loan; None = not known)
CREDIT_AVERAGES = {"Rate": "rate", "OLTV": "oltv", "CLTV": "cltv", "FICO": "fico", "DTI": "dti"}
CREDIT_SHARES = {
    "Inv %": ("investor", lambda r: float(r["investor"])),
    "Cash-out %": ("cash_out", lambda r: float(r["cash_out"])),
    # Freddie divides by ALL loans: "Not Available" counts as not over 45 / not under 680 (checked in step 6)
    "DTI > 45 %": ("dti_over_45", lambda r: float(r["dti"] is not None and r["dti"] > 45)),
    "FICO < 680 %": ("fico_under_680", lambda r: float(r["fico"] is not None and r["fico"] < 680)),
}
DEFAULT_CODES = {"02", "03", "09", "15"}
PERFORMANCE = {
    "Active": ("active", lambda r: float(r["zbc"] is None)),
    "Prepay": ("prepay", lambda r: float(r["zbc"] == "01")),
    "Default": ("default_freddie_way", lambda r: float(r["zbc"] in DEFAULT_CODES)),
    "Ever D90+": ("ever_d90", lambda r: float((r["max_late"] or 0) >= 3)),
    "Ever Frb": ("ever_forbearance", lambda r: float(r["ever_frb"])),
    "Ever Mod": ("ever_modified", lambda r: float(r["ever_mod"])),
}


def read_clarity(path: Path) -> dict[str, dict[str, float]]:
    """Vintage -> {column: value} from a Clarity export (header row, then one row per vintage)."""
    import openpyxl

    ws = openpyxl.load_workbook(path, data_only=True, read_only=True).worksheets[0]
    rows = [r for r in ws.iter_rows(values_only=True) if any(v is not None for v in r)]
    header = [str(h).strip() for h in rows[0]]
    return {str(r[0]).strip(): dict(zip(header[1:], r[1:], strict=False)) for r in rows[1:]}


def estimate(rows: list[dict[str, Any]], value, weighted: bool) -> Estimate:
    pairs = [(v, r["upb"]) for r in rows if (v := value(r)) is not None]
    return weighted_mean([p[0] for p in pairs], [p[1] for p in pairs] if weighted else None)


def compare(rows: list[dict[str, Any]], ref: float, value) -> dict[str, Any]:
    w, c = estimate(rows, value, True), estimate(rows, value, False)
    z = z_score(w, ref)
    return {
        "freddie": ref,
        "ours_upb": w.value,
        "se_upb": w.se,
        "z": z,
        "verdict": verdict(z),
        "ours_count": c.value,
    }


def fmt(x: float, pct: bool) -> str:
    return f"{x:.2%}" if pct else f"{x:.2f}"


def main() -> int:
    import duckdb

    origin = read_clarity(ORIG_XLSX)
    perform = read_clarity(PERF_XLSX)
    con = duckdb.connect()
    cur = con.execute(LOANS_SQL.format(bronze=BRONZE.as_posix(), panel=PANEL.as_posix()))
    names = [d[0] for d in cur.description]
    loans = [dict(zip(names, row, strict=True)) for row in cur.fetchall()]
    by_vintage: dict[int, list[dict[str, Any]]] = {}
    for loan in loans:
        by_vintage.setdefault(loan["vintage"], []).append(loan)

    results: dict[int, dict[str, Any]] = {}
    for vintage in sorted(by_vintage):
        rows, key = by_vintage[vintage], str(vintage)
        res: dict[str, Any] = {"loans": len(rows)}
        for col, name in CREDIT_AVERAGES.items():
            res[name] = compare(
                rows, float(origin[key][col if col != "DTI" else "DTI"]), lambda r, n=name: r[n]
            )
        for col, (name, fn) in CREDIT_SHARES.items():
            res[name] = compare(rows, float(origin[key][col]), fn)
        for col, (name, fn) in PERFORMANCE.items():
            res[name] = compare(rows, float(perform[key][col]), fn)
        res["default_adr002"] = {
            "ours_upb": estimate(rows, lambda r: float(r["adr002_final"] == "defaulted"), True).value,
            "ours_count": estimate(rows, lambda r: float(r["adr002_final"] == "defaulted"), False).value,
        }
        res["freddie_loans"] = int(origin[key]["Loan Count"])
        res["freddie_avg_upb"] = float(origin[key]["Original UPB $"]) / int(origin[key]["Loan Count"])
        res["ours_avg_upb"] = sum(r["upb"] for r in rows) / len(rows)
        # The sample is drawn equally by quarter (12,500 loans each; L-019, ADR-007). Each quarter's
        # balance-weighted rate shows how much rates moved within the year.
        quarters = {}
        for q in (1, 2, 3, 4):
            qrows = [r for r in rows if r["quarter"] == q]
            if qrows:
                quarters[q] = {"loans": len(qrows), "rate": estimate(qrows, lambda r: r["rate"], True).value}
        res["quarters"] = quarters
        results[vintage] = res

    NUMBERS.parent.mkdir(parents=True, exist_ok=True)
    NUMBERS.write_text(json.dumps(results, indent=1, default=float), encoding="utf-8")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    forbearance = forbearance_tables(con)
    # newline="\n": Unix line endings on Windows too (the repo stores text files with LF)
    REPORT.write_text(render(results, public=True, extra=forbearance), encoding="utf-8", newline="\n")
    SIDE_BY_SIDE.write_text(render(results, public=False, extra=forbearance), encoding="utf-8", newline="\n")
    tally: dict[str, int] = {}
    for res in results.values():
        for v in res.values():
            if isinstance(v, dict) and "verdict" in v:
                tally[v["verdict"]] = tally.get(v["verdict"], 0) + 1
    print(f"{len(loans):,} loans compared over {len(results)} vintages; verdicts: {tally}")
    print(f"committed tables: {REPORT}; local side-by-side: {SIDE_BY_SIDE}; numbers: {NUMBERS}")
    return 0


SOURCE = (
    "Freddie Mac Clarity, Standard Dataset Summary Statistics, viewed 15 November 2026, "
    "Dataset Type = All, UPB-weighted"
)


def cell(r: dict[str, Any], pct: bool, public: bool) -> str:
    """public: ours, then (difference from Freddie, z); private: ours / Freddie (z)."""
    ours, diff = r["ours_upb"], r["ours_upb"] - r["freddie"]
    if public:
        d = f"{diff * 100:+.2f} pts" if pct else f"{diff:+.2f}"
        return f"{fmt(ours, pct)} ({d}, z {r['z']:+.1f})"
    return f"{fmt(ours, pct)} / {fmt(r['freddie'], pct)} ({r['z']:+.1f})"


FORBEARANCE_BY_MONTH_SQL = """
select period,
       count(*) as loan_months,
       count(*) filter (where borrower_assistance_status = 'F') as forbearance,
       count(*) filter (where delinquency_due_to_disaster = 'Y') as disaster,
       count(*) filter (where borrower_assistance_status = 'F' or delinquency_due_to_disaster = 'Y') as either_flag
from read_parquet('{bronze}/freddie_perf/*/*.parquet')
where period between '201901' and '202212'
group by period order by period
"""

FORBEARANCE_BY_VINTAGE_SQL = """
with flagged as (
    select loan_id,
           count(*) filter (where borrower_assistance_status = 'F') as f_months
    from read_parquet('{bronze}/freddie_perf/*/*.parquet')
    where period between '202001' and '202112'
    group by loan_id
),
loans as (
    select loan_id,
           case when substr(loan_id, 2, 2) = '99' then 1999 else 2000 + cast(substr(loan_id, 2, 2) as integer) end
               as vintage
    from read_parquet('{bronze}/freddie_orig/*/*.parquet')
)
select vintage,
       count(*) as loans,
       count(*) filter (where f_months > 0) as loans_in_forbearance,
       coalesce(sum(f_months), 0) as forbearance_months
from loans left join flagged using (loan_id)
group by vintage order by vintage
"""


def forbearance_tables(con: Any) -> list[str]:
    """Our sample only: forbearance-flagged loan-months by calendar month and by vintage (2020-2021)."""
    month = con.execute(FORBEARANCE_BY_MONTH_SQL.format(bronze=BRONZE.as_posix())).fetchall()
    vint = con.execute(FORBEARANCE_BY_VINTAGE_SQL.format(bronze=BRONZE.as_posix())).fetchall()
    peak = max(month, key=lambda r: r[2])
    total_f = sum(r[3] for r in vint)
    in_15_19 = sum(r[3] for r in vint if 2015 <= r[0] <= 2019)
    lines = [
        "### COVID forbearance in our sample: when and on which vintages",
        "",
        f"Forbearance-flagged loan-months peak in **{peak[0][:4]}-{peak[0][4:]}** ({peak[2]:,} of {peak[1]:,} active "
        f"loan-months). Of all forbearance loan-months in 2020–2021, **{in_15_19 / total_f:.0%}** fall on the "
        "2015–2019 vintages, the loans outstanding when COVID hit, which is why ADR-001's COVID full-data vintage "
        "is 2019 (optionally 2017).",
        "",
        "By calendar month (forbearance = assistance status F; disaster = delinquency due to disaster Y):",
        "",
        "| Month | Active loan-months | Forbearance | Share | Disaster flag | Either flag |",
        "|---|---|---|---|---|---|",
    ]
    for period, n, f, d, e in month:
        lines.append(f"| {period[:4]}-{period[4:]} | {n:,} | {f:,} | {f / n:.2%} | {d:,} | {e:,} |")
    lines += [
        "",
        "By vintage, during January 2020 to December 2021:",
        "",
        "| Vintage | Sample loans | Loans ever in forbearance | Share | Forbearance loan-months | Share of all 2020–2021 forbearance months |",
        "|---|---|---|---|---|---|",
    ]
    for v, loans, lf, fm in vint:
        lines.append(f"| {v} | {loans:,} | {lf:,} | {lf / loans:.2%} | {fm:,} | {fm / total_f:.1%} |")
    return lines


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx * syy) ** 0.5 if sxx and syy else 0.0


def quarter_table(results: dict[int, dict[str, Any]]) -> list[str]:
    """Our quarterly rates and the yearly gap from Freddie (a difference only), to show the quarter-mix effect."""
    rows = [
        "| Vintage | Sample loans per quarter | Q1 rate | Q2 rate | Q3 rate | Q4 rate | Within-year range | Our yearly rate minus Freddie's | z |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    spreads, gaps = [], []
    for v, r in results.items():
        qs = r["quarters"]
        rates = [qs[q]["rate"] for q in sorted(qs)]
        spread = max(rates) - min(rates)
        gap = r["rate"]["ours_upb"] - r["rate"]["freddie"]
        counts = sorted({qs[q]["loans"] for q in qs})
        cells = " | ".join(f"{qs[q]['rate']:.2f}" if q in qs else "-" for q in (1, 2, 3, 4))
        rows.append(
            f"| {v} | {'/'.join(f'{c:,}' for c in counts)} | {cells} | {spread:.2f} | {gap:+.3f} pts | {r['rate']['z']:+.1f} |"
        )
        spreads.append(spread)
        gaps.append(abs(gap))
    rows.append("")
    rows.append(
        f"Correlation between the within-year rate range and the size of our gap from Freddie: "
        f"**{pearson(spreads, gaps):+.2f}** (28 vintages). The bigger the move in rates within a year, the bigger "
        "the gap: the pattern expected when each quarter counts equally in the sample but not in the population."
    )
    return rows


def render(results: dict[int, dict[str, Any]], public: bool, extra: list[str] | None = None) -> str:
    """public=True: committed tables, our figures and the differences only (Freddie's own figures are
    not republished). public=False: the full side-by-side, kept in git-ignored data/local/."""
    how = "ours (difference from Freddie, z)" if public else "ours / Freddie (z)"
    head = [
        "| Vintage | Active | Prepay | Default | Ever D90+ | Ever forbearance | Ever modified |",
        "|---|---|---|---|---|---|---|",
    ]
    avg = ["| Vintage | Sample loans | Rate | OLTV | CLTV | FICO | DTI |", "|---|---|---|---|---|---|---|"]
    shr = ["| Vintage | Investor | Cash-out | DTI > 45 | FICO < 680 |", "|---|---|---|---|---|"]
    prf, cnt = list(head), list(head)
    if public:
        gap = [
            "| Vintage | Ours, Freddie's way: Ever D90+ | Difference from Freddie's Ever D90+ | Ours, ADR-002 default | ADR-002 minus our Ever D90+ | ADR-002 default by loan count |",
            "|---|---|---|---|---|---|",
        ]
    else:
        gap = [
            "| Vintage | Freddie Default | Freddie Ever D90+ | Ours, Freddie's way: Ever D90+ | Ours, ADR-002 default | ADR-002 default by loan count |",
            "|---|---|---|---|---|---|",
        ]
    for v, r in results.items():
        avg.append(
            f"| {v} | {r['loans']:,} | "
            + " | ".join(cell(r[n], False, public) for n in CREDIT_AVERAGES.values())
            + " |"
        )
        shr.append(
            f"| {v} | " + " | ".join(cell(r[n], True, public) for n, _ in CREDIT_SHARES.values()) + " |"
        )
        prf.append(f"| {v} | " + " | ".join(cell(r[n], True, public) for n, _ in PERFORMANCE.values()) + " |")
        cnt.append(
            f"| {v} | " + " | ".join(f"{r[n]['ours_count']:.2%}" for n, _ in PERFORMANCE.values()) + " |"
        )
        e, a = r["ever_d90"], r["default_adr002"]
        if public:
            gap.append(
                f"| {v} | {e['ours_upb']:.2%} | {(e['ours_upb'] - e['freddie']) * 100:+.2f} pts | {a['ours_upb']:.2%} | "
                f"{(a['ours_upb'] - e['ours_upb']) * 100:+.2f} pts | {a['ours_count']:.2%} |"
            )
        else:
            gap.append(
                f"| {v} | {r['default_freddie_way']['freddie']:.2%} | {e['freddie']:.2%} | {e['ours_upb']:.2%} | "
                f"{a['ours_upb']:.2%} | {a['ours_count']:.2%} |"
            )
    lines = [
        "<!-- generated by scripts/compare_clarity_summary.py; aggregates only -->",
        "",
        f"Comparison source: {SOURCE}. Clarity describes its figures as for reference only; the loan-level "
        "dataset is the official source. Our figures: the Release 48 sample, balance-weighted, before "
        "quarterly weighting (preliminary; see L-019 and ADR-007). Differences are ours minus Freddie's; "
        "z = difference / our sampling error.",
        "",
        f"### Credit profile: averages, {how}",
        "",
        *avg,
        "",
        f"### Credit profile: shares, {how}",
        "",
        *shr,
        "",
        f"### Performance, Freddie's way, {how}",
        "",
        *prf,
        "",
        "### Performance, Freddie's way: our sample by loan count (for reference; Clarity has no count option)",
        "",
        *cnt,
        "",
        "### Default: Freddie's way next to ADR-002 (balance-weighted unless stated)",
        "",
        *gap,
        "",
        "### The quarter-mix effect: interest rate (balance-weighted)",
        "",
        "The sample has exactly 12,500 loans in each origination quarter (L-019, ADR-007). When rates move "
        "within a year and lending volume shifts between quarters, a yearly average over the sample differs "
        "from Freddie's population figure. No weights are applied (ADR-007).",
        "",
        *quarter_table(results),
        "",
        *(extra or []),
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
