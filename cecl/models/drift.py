"""Simulated-history drift (diagnosis for the next model version; scoring only, L-038).

The v2 projection carries each path's memory inputs (months late in the past 12, worst state in 6 months,
months since the first missed payment, state last month) forward from its own simulated states. If the
simulated paths drift away from what really happened (for example fewer loans with a recent missed
payment), the one-month models are fed the wrong history and the projection falls behind even though each
model is calibrated one month at a time.

`SimRecorder` is passed as `observer` to `simulate.project` (it changes nothing) and summarises the live
paths each projected month; `actual_by_month` summarises the same loans' real rows from the transition
tables (weighted, because quiet months are sampled). `compare` lines them up by month.

Educational model on Freddie Mac conforming mortgages; not an audited reserve.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cecl import features as fx
from cecl.models import multistate

STATS = ("alive", "share_current", "share_30", "share_60", "late_months_12_mean", "share_late_12",
         "share_worst6_late", "share_ever_missed", "share_last_month_late", "current_with_late_12",
         "roll_current_to_30", "roll_30_to_60", "default_from_60", "cure_30_to_current")  # fmt: skip


def _summary(state: np.ndarray, late12: np.ndarray, worst6: np.ndarray, first: np.ndarray, last: np.ndarray,
             w: np.ndarray) -> dict[str, float]:  # fmt: skip
    total = w.sum()
    if total <= 0:
        return {}
    cur = state == fx.CURRENT
    return {
        "alive": float(total),
        "share_current": float(w[cur].sum() / total),
        "share_30": float(w[state == fx.LATE_30].sum() / total),
        "share_60": float(w[state == fx.LATE_60].sum() / total),
        "late_months_12_mean": float((w * late12).sum() / total),
        "share_late_12": float(w[late12 >= 1].sum() / total),
        "share_worst6_late": float(w[worst6 >= 1].sum() / total),
        "share_ever_missed": float(w[first > 0].sum() / total),
        "share_last_month_late": float(w[last >= 1].sum() / total),
        "current_with_late_12": float(w[cur & (late12 >= 1)].sum() / max(w[cur].sum(), 1e-12)),
    }


class SimRecorder:
    """Observer for `simulate.project`: per projected month, the live paths' state mix, memory inputs and
    the one-month chances the models gave them (each path counts 1 / paths)."""

    def __init__(self, paths: int) -> None:
        self.paths = paths
        self.rows: dict[int, dict[str, float]] = {}

    def __call__(self, k: int, state: np.ndarray, alive: np.ndarray, hist: dict[str, np.ndarray], loan: np.ndarray,
                 probs: dict[int, np.ndarray]) -> None:  # fmt: skip
        a = alive & (state <= fx.LATE_60)
        w = np.full(int(a.sum()), 1.0 / self.paths)
        row = _summary(state[a], hist["late_months_12"][a], hist["worst_state_6"][a],
                       hist["months_since_first_miss"][a], hist["state_last_month"][a], w)  # fmt: skip
        for name, s, dest in (("roll_current_to_30", fx.CURRENT, fx.LATE_30), ("roll_30_to_60", fx.LATE_30, fx.LATE_60),
                              ("default_from_60", fx.LATE_60, fx.DEFAULTED), ("cure_30_to_current", fx.LATE_30, fx.CURRENT)):  # fmt: skip
            P = probs.get(s)
            row[name] = float(P[:, dest].mean()) if P is not None and len(P) else float("nan")
        self.rows[k] = row


def actual_by_month(rows: Any, start_ym: str, months: int) -> dict[int, dict[str, float]]:
    """The same summaries from the loans' real transition rows (columns: period_ym, state_prev, state,
    weight, late_months_12, worst_state_6, months_since_first_miss, state_last_month)."""
    out: dict[int, dict[str, float]] = {}
    for k in range(months):
        ym = multistate.add_months(start_ym, k)
        r = rows[rows["period_ym"] == ym]
        if not len(r):
            continue
        w = r["weight"].to_numpy(dtype=float)
        sp, st = r["state_prev"].to_numpy(dtype=int), r["state"].to_numpy(dtype=int)
        row = _summary(sp, r["late_months_12"].to_numpy(dtype=float), r["worst_state_6"].to_numpy(dtype=float),
                       r["months_since_first_miss"].to_numpy(dtype=float), r["state_last_month"].to_numpy(dtype=float), w)  # fmt: skip
        for name, s, dest in (("roll_current_to_30", fx.CURRENT, fx.LATE_30), ("roll_30_to_60", fx.LATE_30, fx.LATE_60),
                              ("default_from_60", fx.LATE_60, fx.DEFAULTED), ("cure_30_to_current", fx.LATE_30, fx.CURRENT)):  # fmt: skip
            m = sp == s
            row[name] = float(w[m & (st == dest)].sum() / w[m].sum()) if w[m].sum() > 0 else float("nan")
        out[k] = row
    return out


def compare(sim: dict[int, dict[str, float]], actual: dict[int, dict[str, float]]) -> list[dict[str, Any]]:
    """Month by month: simulated, actual and simulated / actual for every statistic."""
    table = []
    for k in sorted(set(sim) & set(actual)):
        row: dict[str, Any] = {"k": k}
        for s in STATS:
            a, b = sim[k].get(s, float("nan")), actual[k].get(s, float("nan"))
            row[s] = {"sim": a, "actual": b, "ratio": a / b if b else float("nan")}
        table.append(row)
    return table


def by_year(table: list[dict[str, Any]], stats: tuple[str, ...] = STATS) -> dict[str, dict[str, float]]:
    """Average simulated / actual ratio of each statistic by projection year (months 1-12, 13-24, ...)."""
    acc: dict[str, dict[str, list[float]]] = {}
    for row in table:
        year = acc.setdefault(f"year_{row['k'] // 12 + 1}", {})
        for s in stats:
            year.setdefault(s, []).append(float(row[s]["ratio"]))
    return {y: {s: float(np.nanmean(v)) for s, v in d.items()} for y, d in acc.items()}
