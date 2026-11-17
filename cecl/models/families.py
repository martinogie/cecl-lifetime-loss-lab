"""The three model families (Phase 2 steps 3-5), behind one small interface.

Each family predicts, for every loan-month, the probabilities of (no event, default, prepayment)
and explains each prediction by per-feature contributions to the default score (reason codes).

| Family | How | Contributions |
|---|---|---|
| `logistic` (the benchmark) | Multinomial logistic regression on scorecard bins (cecl/models/binning.py), weighted | Each feature's bin points (log-odds of default vs no event) relative to the training average |
| `lightgbm` | Two gradient-boosted models, one per cause (default vs not, prepayment vs not), each with its own monotonic constraints | LightGBM's built-in TreeSHAP (`pred_contrib`) for the default model |
| `ebm` | Two Explainable Boosting Machines, one per cause | Each term's score for the default model |

Why two models per cause for LightGBM and the EBM: LightGBM applies one monotonic constraint to every
class of a multiclass model, but the expected directions differ by cause (higher LTV, more default;
higher rate incentive, more prepayment), so each cause gets its own model with its own constraints.
The two monthly probabilities are combined as competing risks: P(no event) = 1 - P(default) -
P(prepayment) (monthly probabilities are small, so the sum stays far below 1; it is capped at 1 just
in case). ADR-009 D (one family for both causes) is about the family, so this is consistent.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np

from cecl import features as fx
from cecl.models.binning import FeatureBins, fit_bins, one_hot

FEATURES = fx.FEATURE_NAMES
SIMPLICITY_ORDER = ("logistic", "ebm", "lightgbm")  # ties go to the simpler family (ADR-009 C1)


class Family(Protocol):
    name: str

    def fit(self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray) -> Family: ...
    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray: ...
    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray: ...
    def params(self) -> dict[str, Any]: ...


def matrix(X: dict[str, np.ndarray]) -> np.ndarray:
    return np.column_stack([np.asarray(X[f], dtype=float) for f in FEATURES])


def _combine(p_default: np.ndarray, p_prepay: np.ndarray) -> np.ndarray:
    total = p_default + p_prepay
    scale = np.where(total > 1.0, 1.0 / total, 1.0)
    pd_, pp = p_default * scale, p_prepay * scale
    return np.column_stack([np.clip(1.0 - pd_ - pp, 0.0, 1.0), pd_, pp])  # rounding can dip below 0


# ---------------------------------------------------------------------------- logistic benchmark


class LogisticScorecard:
    name = "logistic"

    def __init__(self, C: float = 1.0, max_iter: int = 500) -> None:
        self.C, self.max_iter = C, max_iter
        self.bins: list[FeatureBins] = []
        self.columns: list[str] = []
        self.model: Any = None
        self.baseline: np.ndarray | None = None

    def fit(self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray) -> LogisticScorecard:
        from sklearn.linear_model import LogisticRegression

        default = (outcome == fx.DEFAULT).astype(int)
        direction = {f.name: f.default_direction for f in fx.FEATURES}
        self.bins = [fit_bins(f, X[f], default, weight, direction[f]) for f in FEATURES]
        design, self.columns = one_hot(self.bins, X)
        self.model = LogisticRegression(C=self.C, max_iter=self.max_iter)
        self.model.fit(design, outcome, sample_weight=weight)
        self.baseline = (design * weight[:, None]).sum(axis=0) / weight.sum()  # average design row
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        design, _ = one_hot(self.bins, X)
        proba = self.model.predict_proba(design)
        out = np.zeros((len(design), 3))
        out[:, self.model.classes_.astype(int)] = proba
        return out

    def _default_coef(self) -> np.ndarray:
        classes = list(self.model.classes_.astype(int))
        return self.model.coef_[classes.index(fx.DEFAULT)] - self.model.coef_[classes.index(fx.NONE)]

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """Per feature: its bins' points for default vs no event, minus the training average."""
        design, _ = one_hot(self.bins, X)
        assert self.baseline is not None
        per_column = (design - self.baseline) * self._default_coef()
        out = np.zeros((len(design), len(FEATURES)))
        start = 0
        for k, b in enumerate(self.bins):
            width = b.n_bins  # bins 1..n-1 plus the missing column
            out[:, k] = per_column[:, start : start + width].sum(axis=1)
            start += width
        return out

    def params(self) -> dict[str, Any]:
        return {"family": self.name, "C": self.C, "max_iter": self.max_iter,
                "bins": {b.name: b.edges for b in self.bins}}  # fmt: skip


# ---------------------------------------------------------------------------- LightGBM


def monotone(cause: str) -> list[int]:
    attr = "default_direction" if cause == "default" else "prepay_direction"
    return [getattr(f, attr) for f in fx.FEATURES]


class LightGBMPair:
    name = "lightgbm"

    def __init__(self, n_estimators: int = 400, learning_rate: float = 0.05, num_leaves: int = 31,
                 min_child_weight: float = 50.0, seed: int = 7) -> None:  # fmt: skip
        self.settings: dict[str, Any] = {"n_estimators": n_estimators, "learning_rate": learning_rate, "num_leaves": num_leaves,
                         "min_child_weight": min_child_weight, "random_state": seed, "verbose": -1}  # fmt: skip
        self.models: dict[str, Any] = {}

    def fit(self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray) -> LightGBMPair:
        import lightgbm as lgb

        m = matrix(X)
        for cause, code in (("default", fx.DEFAULT), ("prepay", fx.PREPAY)):
            model = lgb.LGBMClassifier(
                objective="binary", monotone_constraints=monotone(cause), **self.settings
            )
            model.fit(m, (outcome == code).astype(int), sample_weight=weight)
            self.models[cause] = model
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        m = matrix(X)
        return _combine(
            self.models["default"].predict_proba(m)[:, 1], self.models["prepay"].predict_proba(m)[:, 1]
        )

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """TreeSHAP values of the default model (log-odds), one column per feature."""
        contrib = self.models["default"].predict(matrix(X), pred_contrib=True)
        return np.asarray(contrib)[:, : len(FEATURES)]  # the last column is the expected value

    def params(self) -> dict[str, Any]:
        return {"family": self.name, **self.settings, "monotone_default": monotone("default"),
                "monotone_prepay": monotone("prepay")}  # fmt: skip


# ---------------------------------------------------------------------------- EBM


class EBMPair:
    name = "ebm"

    def __init__(
        self, interactions: int = 5, outer_bags: int = 4, max_rounds: int = 2000, seed: int = 7
    ) -> None:
        self.settings: dict[str, Any] = {"interactions": interactions, "outer_bags": outer_bags, "max_rounds": max_rounds,
                         "random_state": seed}  # fmt: skip
        self.models: dict[str, Any] = {}

    def fit(self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray) -> EBMPair:
        from interpret.glassbox import ExplainableBoostingClassifier

        m = matrix(X)
        for cause, code in (("default", fx.DEFAULT), ("prepay", fx.PREPAY)):
            model = ExplainableBoostingClassifier(feature_names=list(FEATURES), **self.settings)
            model.fit(m, (outcome == code).astype(int), sample_weight=weight)
            self.models[cause] = model
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        m = matrix(X)
        return _combine(
            self.models["default"].predict_proba(m)[:, 1], self.models["prepay"].predict_proba(m)[:, 1]
        )

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """Each single-feature term's score in the default model (interaction terms left out)."""
        model = self.models["default"]
        scores = np.asarray(model.eval_terms(matrix(X)))
        names = list(model.term_names_)
        out = np.zeros((scores.shape[0], len(FEATURES)))
        for k, f in enumerate(FEATURES):
            if f in names:
                out[:, k] = scores[:, names.index(f)]
        return out

    def params(self) -> dict[str, Any]:
        return {"family": self.name, **self.settings}


FAMILIES: dict[str, type] = {"logistic": LogisticScorecard, "lightgbm": LightGBMPair, "ebm": EBMPair}


def make(name: str, **settings: Any) -> Any:
    if name not in FAMILIES:
        raise ValueError(f"unknown family {name!r}; use one of {', '.join(FAMILIES)}")
    return FAMILIES[name](**settings)
