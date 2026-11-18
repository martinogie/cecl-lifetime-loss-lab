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

FEATURES = fx.FEATURE_NAMES  # the default list (models trained before the era feature use it)
SIMPLICITY_ORDER = ("logistic", "ebm", "lightgbm")  # ties go to the simpler family (ADR-009 C1)


class Family(Protocol):
    name: str

    def fit(
        self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray, eval_set: Any = None
    ) -> Family: ...
    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray: ...
    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray: ...
    def params(self) -> dict[str, Any]: ...


def matrix(X: dict[str, np.ndarray], features: tuple[str, ...] = FEATURES) -> np.ndarray:
    return np.column_stack([np.asarray(X[f], dtype=float) for f in features])


def feature_list(model: Any) -> tuple[str, ...]:
    """A model's features (models saved before 17 Nov 2026 have no list: the 19 table features)."""
    return tuple(getattr(model, "features", FEATURES))


def _directions(features: tuple[str, ...], attr: str) -> list[int]:
    known = {f.name: getattr(f, attr) for f in fx.model_features("buckets")}
    return [known[f] for f in features]


def _combine(p_default: np.ndarray, p_prepay: np.ndarray) -> np.ndarray:
    total = p_default + p_prepay
    scale = np.where(total > 1.0, 1.0 / total, 1.0)
    pd_, pp = p_default * scale, p_prepay * scale
    return np.column_stack([np.clip(1.0 - pd_ - pp, 0.0, 1.0), pd_, pp])  # rounding can dip below 0


# ---------------------------------------------------------------------------- logistic benchmark


class LogisticScorecard:
    name = "logistic"

    def __init__(self, C: float = 1.0, max_iter: int = 500, features: tuple[str, ...] = FEATURES) -> None:
        self.C, self.max_iter = C, max_iter
        self.features = tuple(features)
        self.bins: list[FeatureBins] = []
        self.columns: list[str] = []
        self.model: Any = None
        self.baseline: np.ndarray | None = None

    def fit(
        self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray, eval_set: Any = None
    ) -> LogisticScorecard:
        from sklearn.linear_model import LogisticRegression

        default = (outcome == fx.DEFAULT).astype(int)
        direction = dict(zip(self.features, _directions(self.features, "default_direction"), strict=True))
        self.bins = [fit_bins(f, X[f], default, weight, direction[f]) for f in self.features]
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
        out = np.zeros((len(design), len(feature_list(self))))
        start = 0
        for k, b in enumerate(self.bins):
            width = b.n_bins  # bins 1..n-1 plus the missing column
            out[:, k] = per_column[:, start : start + width].sum(axis=1)
            start += width
        return out

    def params(self) -> dict[str, Any]:
        return {"family": self.name, "C": self.C, "max_iter": self.max_iter, "features": list(feature_list(self)),
                "bins": {b.name: b.edges for b in self.bins}}  # fmt: skip


# ---------------------------------------------------------------------------- LightGBM


def monotone(cause: str, features: tuple[str, ...] = FEATURES) -> list[int]:
    return _directions(features, "default_direction" if cause == "default" else "prepay_direction")


class LightGBMPair:
    name = "lightgbm"

    def __init__(self, n_estimators: int = 400, learning_rate: float = 0.05, num_leaves: int = 31,
                 min_child_weight: float = 50.0, seed: int = 7, features: tuple[str, ...] = FEATURES) -> None:  # fmt: skip
        self.settings: dict[str, Any] = {"n_estimators": n_estimators, "learning_rate": learning_rate,
                                         "num_leaves": num_leaves, "min_child_weight": min_child_weight,
                                         "random_state": seed, "verbose": -1}  # fmt: skip
        self.features = tuple(features)
        self.models: dict[str, Any] = {}
        self.rounds: dict[str, Any] = {}  # per cause: training and validation log loss at every round

    def fit(self, X: dict[str, np.ndarray], outcome: np.ndarray, weight: np.ndarray,
            eval_set: tuple[dict[str, np.ndarray], np.ndarray, np.ndarray] | None = None) -> LightGBMPair:  # fmt: skip
        """`eval_set` = (validation inputs, outcomes, weights): only *recorded* round by round (the
        training curve); it never changes the number of rounds (that would tune on validation)."""
        import lightgbm as lgb

        feats = feature_list(self)
        m = matrix(X, feats)
        for cause, code in (("default", fx.DEFAULT), ("prepay", fx.PREPAY)):
            model = lgb.LGBMClassifier(objective="binary", monotone_constraints=monotone(cause, feats),
                                       **self.settings)  # fmt: skip
            y = (outcome == code).astype(int)
            if eval_set is None:
                model.fit(m, y, sample_weight=weight)
            else:
                vx, vy, vw = eval_set
                model.fit(m, y, sample_weight=weight, eval_set=[(m, y), (matrix(vx, feats), (vy == code).astype(int))],
                          eval_sample_weight=[weight, vw], eval_names=["training", "validation"],
                          eval_metric="binary_logloss")  # fmt: skip
                curve = model.evals_result_
                valid = list(curve["validation"]["binary_logloss"])
                self.rounds[cause] = {"training": list(curve["training"]["binary_logloss"]), "validation": valid,
                                      "best_round": int(np.argmin(valid)) + 1, "best_validation": float(min(valid))}  # fmt: skip
            self.models[cause] = model
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        m = matrix(X, feature_list(self))
        return _combine(
            self.models["default"].predict_proba(m)[:, 1], self.models["prepay"].predict_proba(m)[:, 1]
        )

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """TreeSHAP values of the default model (log-odds), one column per feature."""
        feats = feature_list(self)
        contrib = self.models["default"].predict(matrix(X, feats), pred_contrib=True)
        return np.asarray(contrib)[:, : len(feats)]  # the last column is the expected value

    def params(self) -> dict[str, Any]:
        feats = feature_list(self)
        return {"family": self.name, **self.settings, "features": list(feats),
                "monotone_default": monotone("default", feats), "monotone_prepay": monotone("prepay", feats)}  # fmt: skip


# ---------------------------------------------------------------------------- EBM


INNER_STOP_FIRST = "201401"  # the inner split used for tuning: fit up to 2013, judge (and stop) on 2014-2016
BAG_TRAIN_SHARE = 0.85  # each bag trains on a random 85% of the 1999-2013 rows, so the bags differ


def time_bags(periods: np.ndarray, outer_bags: int, seed: int) -> np.ndarray:
    """EBM bag definitions (rows x bags): +1 = train, -1 = validation (early stopping), 0 = left out.
    Every 2014-2016 row is the stopping set in every bag (time-based, like the tuning split); each
    bag trains on its own random 85% of the 1999-2013 rows."""
    rng = np.random.default_rng(seed)
    late = periods >= INNER_STOP_FIRST
    bags = np.zeros((len(periods), outer_bags), dtype=np.int8)
    for b in range(outer_bags):
        chosen = (~late) & (rng.uniform(size=len(periods)) < BAG_TRAIN_SHARE)
        bags[chosen, b] = 1
        bags[late, b] = -1
    return bags


class EBMPair:
    name = "ebm"

    def __init__(self, interactions: int = 5, outer_bags: int = 4, max_rounds: int = 2000, seed: int = 7,
                 features: tuple[str, ...] = FEATURES, time_stopping: bool = False) -> None:  # fmt: skip
        self.settings: dict[str, Any] = {"interactions": interactions, "outer_bags": outer_bags,
                                         "max_rounds": max_rounds, "random_state": seed}  # fmt: skip
        self.features = tuple(features)
        # Off by default (L-025): with time-based stopping the 2014-2016 rows are never trained on.
        self.time_stopping = time_stopping
        self.models: dict[str, Any] = {}

    def fit(
        self,
        X: dict[str, np.ndarray],
        outcome: np.ndarray,
        weight: np.ndarray,
        eval_set: Any = None,
        periods: np.ndarray | None = None,
    ) -> EBMPair:
        """With `time_stopping` and `periods` (YYYYMM per row), early stopping is time-based
        (`time_bags`: train <=2013, stop on 2014-2016, which are then never trained on). By default
        InterpretML holds out a random 15% of the training rows in each bag (L-025)."""
        from interpret.glassbox import ExplainableBoostingClassifier

        feats = feature_list(self)
        m = matrix(X, feats)
        use_time = getattr(self, "time_stopping", False) and periods is not None
        bags = None if not use_time else time_bags(np.asarray(periods), self.settings["outer_bags"],
                                                   self.settings["random_state"])  # fmt: skip
        self.early_stopping = (
            "time: train <=2013, stop on 2014-2016" if bags is not None else "random 15% per bag"
        )
        for cause, code in (("default", fx.DEFAULT), ("prepay", fx.PREPAY)):
            model = ExplainableBoostingClassifier(feature_names=list(feats), **self.settings)
            model.fit(m, (outcome == code).astype(int), sample_weight=weight, bags=bags)
            self.models[cause] = model
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        m = matrix(X, feature_list(self))
        return _combine(
            self.models["default"].predict_proba(m)[:, 1], self.models["prepay"].predict_proba(m)[:, 1]
        )

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """Each single-feature term's score in the default model (interaction terms left out)."""
        feats = feature_list(self)
        model = self.models["default"]
        scores = np.asarray(model.eval_terms(matrix(X, feats)))
        names = list(model.term_names_)
        out = np.zeros((scores.shape[0], len(feats)))
        for k, f in enumerate(feats):
            if f in names:
                out[:, k] = scores[:, names.index(f)]
        return out

    def params(self) -> dict[str, Any]:
        return {"family": self.name, **self.settings, "features": list(feature_list(self)),
                "early_stopping": getattr(self, "early_stopping", "random 15% per bag")}  # fmt: skip


FAMILIES: dict[str, type] = {"logistic": LogisticScorecard, "lightgbm": LightGBMPair, "ebm": EBMPair}


def make(name: str, **settings: Any) -> Any:
    if name not in FAMILIES:
        raise ValueError(f"unknown family {name!r}; use one of {', '.join(FAMILIES)}")
    return FAMILIES[name](**settings)
