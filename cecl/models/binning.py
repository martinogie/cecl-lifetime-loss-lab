"""Scorecard-style bins for the logistic benchmark (Phase 2 step 3).

Each numeric feature is cut into up to 10 bins at weighted quantiles of the training rows, plus a
"missing" bin. Where the expected direction is known (cecl/features.py: more LTV, more default),
neighbouring bins whose training default rates go the wrong way are merged until the rates move in
the expected direction (pool-adjacent-violators), so the scorecard can never say, for example, that
a higher LTV is safer. Binary features keep their two values. Bins are fitted on the training
months only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

MAX_BINS = 10


@dataclass
class FeatureBins:
    name: str
    edges: list[float]  # inner cut points; bin i is (edges[i-1], edges[i]]
    direction: int
    rates: list[float] = field(default_factory=list)  # training default rate per bin (for review)

    @property
    def n_bins(self) -> int:
        return len(self.edges) + 1

    def assign(self, x: np.ndarray) -> np.ndarray:
        """Bin index per row; the missing bin is n_bins."""
        out = np.searchsorted(np.asarray(self.edges), x, side="left")
        out[np.isnan(x)] = self.n_bins
        return out


def _weighted_quantiles(x: np.ndarray, w: np.ndarray, qs: np.ndarray) -> np.ndarray:
    order = np.argsort(x)
    x, w = x[order], w[order]
    cum = np.cumsum(w) / w.sum()
    return np.interp(qs, cum, x)


def _merge_empty(
    edges: list[float], events: list[float], totals: list[float]
) -> tuple[list[float], list[float], list[float]]:
    """Merge any bin with no training rows into its neighbour (the next one, or the previous for the last)."""
    edges, events, totals = list(edges), list(events), list(totals)
    i = 0
    while i < len(totals) and len(totals) > 1:
        if totals[i] > 0:
            i += 1
            continue
        nxt = i + 1 < len(totals)  # merge into the next bin, or the previous one for the last bin
        e, t = events.pop(i), totals.pop(i)
        target = i if nxt else i - 1  # after the pop, the next bin has moved down to position i
        events[target] += e
        totals[target] += t
        edges.pop(i if nxt else i - 1)
    return edges, events, totals


def _merge_to_monotone(
    edges: list[float], events: list[float], totals: list[float], direction: int
) -> list[float]:
    """Drop cut points until default rates move with `direction` (+1 rising, -1 falling)."""
    edges, events, totals = list(edges), list(events), list(totals)
    changed = True
    while changed and direction and len(totals) > 1:
        changed = False
        for i in range(len(totals) - 1):
            a, b = events[i] / max(totals[i], 1e-12), events[i + 1] / max(totals[i + 1], 1e-12)
            if (b - a) * direction < 0:  # wrong way: merge bins i and i+1
                events[i] += events.pop(i + 1)
                totals[i] += totals.pop(i + 1)
                edges.pop(i)
                changed = True
                break
    return edges


def fit_bins(name: str, x: Any, default: Any, weight: Any, direction: int = 0, max_bins: int = MAX_BINS,
             edges: Sequence[float] | None = None) -> FeatureBins:  # fmt: skip
    """Scorecard bins for one feature: cut at weighted quantiles (or at the given `edges`), empty bins
    merged, then merged until the event rate is monotone in `direction` (0 = no direction)."""
    xv = np.asarray(x, dtype=float)
    yv = np.asarray(default, dtype=float)
    wv = np.asarray(weight, dtype=float)
    ok = ~np.isnan(xv)
    values = np.unique(xv[ok])
    cuts: list[float]
    if edges is not None:  # fixed cut points (a count whose quantiles would collapse)
        cuts = sorted(float(e) for e in edges)
    elif len(values) <= 2:  # binary or constant: one cut between the values
        cuts = [float(values[0])] if len(values) == 2 else []
    else:
        qs = np.linspace(0, 1, max_bins + 1)[1:-1]
        cuts = sorted(set(float(e) for e in _weighted_quantiles(xv[ok], wv[ok], qs)))
    bins = FeatureBins(name, cuts, direction)
    idx = bins.assign(xv)
    events = [float((wv[ok] * yv[ok])[idx[ok] == i].sum()) for i in range(bins.n_bins)]
    totals = [float(wv[ok][idx[ok] == i].sum()) for i in range(bins.n_bins)]
    cuts, events, totals = _merge_empty(cuts, events, totals)
    bins = FeatureBins(name, _merge_to_monotone(cuts, events, totals, direction), direction)
    idx = bins.assign(xv)
    bins.rates = [float((wv[ok] * yv[ok])[idx[ok] == i].sum() / max(wv[ok][idx[ok] == i].sum(), 1e-12))
                  for i in range(bins.n_bins)]  # fmt: skip
    return bins


def one_hot(all_bins: Sequence[FeatureBins], columns: dict[str, np.ndarray]) -> tuple[np.ndarray, list[str]]:
    """Design matrix: one column per bin (first bin of each feature dropped as the reference) plus
    a missing-value column per feature."""
    blocks, names = [], []
    for b in all_bins:
        idx = b.assign(np.asarray(columns[b.name], dtype=float))
        for i in range(1, b.n_bins + 1):  # bins 1..n-1 and the missing bin n
            blocks.append((idx == i).astype(np.float32))
            names.append(f"{b.name}__{'missing' if i == b.n_bins else i}")
    return np.column_stack(blocks) if blocks else np.zeros((0, 0)), names
