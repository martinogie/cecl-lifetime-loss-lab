"""Sampling error for comparing our 50,000-loan-per-year sample with Freddie's full-population figures.

Freddie's sample is a simple random sample of loans. Its summary figures (averages and shares) are
weighted by original balance (UPB), so ours are too. For a balance-weighted average
    m = sum(w * x) / sum(w)
the standard error under simple random sampling is (the usual "ratio estimator" formula)
    se = sqrt(n / (n - 1) * sum(w^2 * (x - m)^2)) / sum(w)
A share is the same calculation with x = 1 if the loan has the feature, else 0. With all weights equal
this reduces to the familiar sd / sqrt(n) and sqrt(p * (1 - p) / n).

z = (ours - Freddie's) / se says how many standard errors apart the two are. With many comparisons a
few |z| between 2 and 3 are expected by chance; |z| above 3 needs an explanation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Estimate:
    value: float
    se: float
    n: int


def weighted_mean(x: Sequence[float], w: Sequence[float] | None = None) -> Estimate:
    """Weighted average and its standard error (equal weights if w is None). Pairs with a missing x are
    dropped before calling: pass only the loans where the value is known."""
    n = len(x)
    if n == 0:
        raise ValueError("no values")
    weights = list(w) if w is not None else [1.0] * n
    if len(weights) != n:
        raise ValueError("x and w must have the same length")
    total = sum(weights)
    if total <= 0:
        raise ValueError("weights must add up to more than zero")
    m = sum(wi * xi for wi, xi in zip(weights, x, strict=True)) / total
    if n == 1:
        return Estimate(m, float("nan"), 1)
    spread = sum(wi * wi * (xi - m) ** 2 for wi, xi in zip(weights, x, strict=True))
    return Estimate(m, math.sqrt(n / (n - 1) * spread) / total, n)


def z_score(ours: Estimate, reference: float) -> float:
    if not ours.se or math.isnan(ours.se):
        return 0.0 if ours.value == reference else math.inf
    return (ours.value - reference) / ours.se


def verdict(z: float) -> str:
    a = abs(z)
    if a <= 2:
        return "agrees"
    if a <= 3:
        return "borderline"
    return "differs"
