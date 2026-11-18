"""The transition models of the multi-state design (ADR-011; Phase 2 plan, multi-state).

One model per starting state (current, 30, 60 days late) predicts next month's state: current, 30,
60, default or prepaid (cecl/features.py `STATES`). All three families are fitted for every starting
state, each only on the loan-months that start there (ADR-011 Decision), behind one interface:

| Family | How | Reason codes (the step's adverse move, `fx.ADVERSE`) |
|---|---|---|
| `logistic` | One multinomial logistic regression on scorecard bins (cecl/models/binning.py), weighted | The bins' points for the adverse move vs staying, relative to the training average |
| `lightgbm` | One binary gradient-boosted model per destination (the "stay" destination is the remainder), each with its own monotonic constraints | TreeSHAP of the adverse move's model |
| `ebm` | One binary Explainable Boosting Machine per destination | Each term's score in the adverse move's model |

Why one binary model per destination for the tree families: the expected direction of a feature
differs by destination (higher LTV: more rolls to worse states, fewer cures; higher rate incentive:
more prepayment), and LightGBM applies one monotonic constraint to every class of a multiclass model.
The binary probabilities are combined so they add to 1: P(stay) = 1 - the others (scaled down if the
others ever add to more than 1). A destination with fewer than `MIN_MOVES` training moves (for example
current -> 60, which only happens when a month's report is skipped) gets its weighted training rate as
a constant instead of a model; this is recorded in the model's parameters.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cecl import features as fx
from cecl.models.binning import FeatureBins, fit_bins, one_hot

# Fixed scorecard cut points (whole months) for "months late in the past 12" in the current-loan
# logistic model. Its bins are otherwise cut at weighted quantiles, and about 98% of current
# loan-months have the value 0, so the quantiles collapsed to a single cut (0 vs 1+): a loan late once
# and a loan late ten times got the same roll rate (1.56x too many rolls for the first, 0.37-0.58x for
# 3+ late months on 2017-2019). Found by the 2017-2019 projection check; ADR-009 Amendment 2.
FIXED_EDGES: dict[int, dict[str, tuple[float, ...]]] = {fx.CURRENT: {"late_months_12": (0, 1, 2, 3, 5, 8)}}

FEATURES = fx.TRANSITION_FEATURES
N_STATES = len(fx.STATES)
MIN_MOVES = 50  # fewer training moves than this to a destination: a constant rate, not a model
# No move is ever given exactly zero chance (a destination unseen in training, or a tree model's
# rounding): every probability is at least this, and the row is renormalised to add to 1.
PROB_FLOOR = 1e-6
# Behavioural features whose direction is not settled once a loan is already behind: a borrower who
# is often a month late rolls on less often than one late for the first time, so for the 30 and 60
# models "months late in the past 12" is left unconstrained (on the 2009 vintage the constraint
# merged all its bins into one, removing it from the model; 17 Nov 2026).
UNCONSTRAINED_WHEN_LATE = ("late_months_12",)
SIMPLICITY_ORDER = ("logistic", "ebm", "lightgbm")  # ties go to the simpler family (ADR-009 C)


def role(start: int, dest: int) -> str:
    """How a destination relates to the starting state: stay, worse, better (a cure) or prepay."""
    if dest == start:
        return "stay"
    if dest == fx.PREPAID:
        return "prepay"
    if dest == fx.DEFAULTED or dest > start:
        return "worse"
    return "better"


def directions(start: int, dest: int, features: tuple[str, ...] = FEATURES) -> list[int]:
    """Monotonic constraints for one destination's model: a feature that raises default risk raises
    rolls to worse states and lowers cures; prepayment keeps its own directions."""
    known = {f.name: f for f in fx.model_features("buckets")}
    r = role(start, dest)
    if r == "prepay":
        out = [known[f].prepay_direction for f in features]
    else:
        sign = 1 if r == "worse" else -1
        out = [sign * known[f].default_direction for f in features]
    if start != fx.CURRENT:
        out = [0 if f in UNCONSTRAINED_WHEN_LATE else d for f, d in zip(features, out, strict=True)]
    return out


def floor(p: np.ndarray) -> np.ndarray:
    """Every probability at least PROB_FLOOR, each row renormalised to add to 1."""
    p = np.maximum(p, PROB_FLOOR)
    return p / p.sum(axis=1, keepdims=True)


def matrix(X: dict[str, np.ndarray], features: tuple[str, ...]) -> np.ndarray:
    return np.column_stack([np.asarray(X[f], dtype=float) for f in features])


def feature_list(model: Any) -> tuple[str, ...]:
    return tuple(getattr(model, "features", FEATURES))


def _check_start(start: int) -> int:
    if start not in fx.LIVE_STATES:
        raise ValueError(f"a transition model starts in current, 30 or 60, not {start!r}")
    return int(start)


# ---------------------------------------------------------------------------- logistic benchmark


class TransitionLogistic:
    name = "logistic"

    def __init__(self, start: int, C: float = 1.0, max_iter: int = 500, features: tuple[str, ...] = FEATURES) -> None:  # fmt: skip
        self.start = _check_start(start)
        self.C, self.max_iter = C, max_iter
        self.features = tuple(features)
        self.bins: list[FeatureBins] = []
        self.model: Any = None
        self.baseline: np.ndarray | None = None

    def fit(self, X: dict[str, np.ndarray], state: np.ndarray, weight: np.ndarray, eval_set: Any = None) -> TransitionLogistic:  # fmt: skip
        from sklearn.linear_model import LogisticRegression

        # Bins are cut once per feature, made monotone against any move to a worse state (the
        # credit-risk direction); the multinomial model then gives every destination its own points.
        worse = np.isin(state, [d for d in range(N_STATES) if role(self.start, d) == "worse"]).astype(int)
        dirs = directions(self.start, fx.ADVERSE[self.start], self.features)
        fixed = FIXED_EDGES.get(self.start, {})
        self.bins = [fit_bins(f, X[f], worse, weight, d, edges=fixed.get(f))
                     for f, d in zip(self.features, dirs, strict=True)]  # fmt: skip
        design, _ = one_hot(self.bins, X)
        self.model = LogisticRegression(C=self.C, max_iter=self.max_iter)
        self.model.fit(design, state, sample_weight=weight)
        self.baseline = (design * weight[:, None]).sum(axis=0) / weight.sum()
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        design, _ = one_hot(self.bins, X)
        out = np.zeros((len(design), N_STATES))
        out[:, self.model.classes_.astype(int)] = self.model.predict_proba(design)
        return floor(out)

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """Per feature: its bins' points for the adverse move vs staying, minus the training average."""
        classes = list(self.model.classes_.astype(int))
        adverse = fx.ADVERSE[self.start]
        if adverse not in classes:
            raise ValueError(f"no {fx.STATES[adverse]} moves in training: no reason codes")
        coef = self.model.coef_[classes.index(adverse)] - self.model.coef_[classes.index(self.start)]
        design, _ = one_hot(self.bins, X)
        assert self.baseline is not None
        per_column = (design - self.baseline) * coef
        out = np.zeros((len(design), len(self.features)))
        col = 0
        for k, b in enumerate(self.bins):
            out[:, k] = per_column[:, col : col + b.n_bins].sum(axis=1)
            col += b.n_bins
        return out

    def params(self) -> dict[str, Any]:
        return {"family": self.name, "start_state": fx.STATES[self.start], "C": self.C, "max_iter": self.max_iter,
                "features": list(self.features), "bins": {b.name: b.edges for b in self.bins}}  # fmt: skip


# ---------------------------------------------------------------------------- tree families


class _PerDestination:
    """Shared by LightGBM and the EBM: one binary model per destination other than "stay"."""

    name = "base"

    def __init__(self, start: int, features: tuple[str, ...]) -> None:
        self.start = _check_start(start)
        self.features = tuple(features)
        self.models: dict[int, Any] = {}
        self.constants: dict[int, float] = {}

    def destinations(self) -> list[int]:
        return [d for d in range(N_STATES) if d != self.start]

    def _fit_one(self, dest: int, m: np.ndarray, y: np.ndarray, w: np.ndarray, eval_set: Any) -> Any:
        raise NotImplementedError

    def _proba_one(self, model: Any, m: np.ndarray) -> np.ndarray:
        return np.asarray(model.predict_proba(m))[:, 1]

    def fit(
        self, X: dict[str, np.ndarray], state: np.ndarray, weight: np.ndarray, eval_set: Any = None
    ) -> Any:
        m = matrix(X, self.features)
        state = np.asarray(state, dtype=int)
        for dest in self.destinations():
            y = (state == dest).astype(int)
            if int(y.sum()) < MIN_MOVES:
                self.constants[dest] = float((weight * y).sum() / weight.sum())
                continue
            self.models[dest] = self._fit_one(dest, m, y, weight, eval_set)
        return self

    def predict_proba(self, X: dict[str, np.ndarray]) -> np.ndarray:
        m = matrix(X, self.features)
        out = np.zeros((len(m), N_STATES))
        for dest in self.destinations():
            out[:, dest] = (
                self._proba_one(self.models[dest], m) if dest in self.models else self.constants[dest]
            )
        others = out.sum(axis=1)
        scale = np.where(others > 1.0, 1.0 / np.maximum(others, 1e-300), 1.0)
        out *= scale[:, None]
        out[:, self.start] = np.clip(1.0 - out.sum(axis=1), 0.0, 1.0)  # rounding can dip below 0
        return floor(out)

    def _adverse_model(self) -> Any:
        adverse = fx.ADVERSE[self.start]
        if adverse not in self.models:
            raise ValueError(f"the {fx.STATES[adverse]} move is a constant (too few moves): no reason codes")
        return self.models[adverse]

    def base_params(self) -> dict[str, Any]:
        return {"family": self.name, "start_state": fx.STATES[self.start], "features": list(self.features),
                "modelled": [fx.STATES[d] for d in self.models],
                "constants": {fx.STATES[d]: v for d, v in self.constants.items()}}  # fmt: skip


class TransitionLightGBM(_PerDestination):
    name = "lightgbm"

    def __init__(self, start: int, n_estimators: int = 400, learning_rate: float = 0.05, num_leaves: int = 31,
                 min_child_weight: float = 50.0, seed: int = 7, features: tuple[str, ...] = FEATURES) -> None:  # fmt: skip
        super().__init__(start, features)
        self.settings: dict[str, Any] = {"n_estimators": n_estimators, "learning_rate": learning_rate,
                                         "num_leaves": num_leaves, "min_child_weight": min_child_weight,
                                         "random_state": seed, "verbose": -1}  # fmt: skip
        self.rounds: dict[str, Any] = {}  # per destination: training and validation log loss per round

    def _fit_one(self, dest: int, m: np.ndarray, y: np.ndarray, w: np.ndarray, eval_set: Any) -> Any:
        """`eval_set` = (validation inputs, states, weights) is only *recorded* round by round; it never
        changes the number of rounds (that would tune on the validation years)."""
        import lightgbm as lgb

        model = lgb.LGBMClassifier(objective="binary", monotone_constraints=directions(self.start, dest, self.features),
                                   **self.settings)  # fmt: skip
        if eval_set is None:
            return model.fit(m, y, sample_weight=w)
        vx, vs, vw = eval_set
        vy = (np.asarray(vs, dtype=int) == dest).astype(int)
        model.fit(m, y, sample_weight=w, eval_set=[(m, y), (matrix(vx, self.features), vy)],
                  eval_sample_weight=[w, vw], eval_names=["training", "validation"], eval_metric="binary_logloss")  # fmt: skip
        curve = model.evals_result_
        valid = list(curve["validation"]["binary_logloss"])
        self.rounds[fx.STATES[dest]] = {"training": list(curve["training"]["binary_logloss"]), "validation": valid,
                                        "best_round": int(np.argmin(valid)) + 1, "best_validation": float(min(valid))}  # fmt: skip
        return model

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        contrib = self._adverse_model().predict(matrix(X, self.features), pred_contrib=True)
        return np.asarray(contrib)[:, : len(self.features)]  # the last column is the expected value

    def params(self) -> dict[str, Any]:
        return (
            self.base_params()
            | self.settings
            | {
                "monotone": {
                    fx.STATES[d]: directions(self.start, d, self.features) for d in self.destinations()
                }
            }
        )


class TransitionEBM(_PerDestination):
    name = "ebm"

    def __init__(self, start: int, interactions: int = 5, outer_bags: int = 4, max_rounds: int = 2000, seed: int = 7,
                 features: tuple[str, ...] = FEATURES) -> None:  # fmt: skip
        super().__init__(start, features)
        # Default early stopping: a random 15% of the training rows per bag (L-025).
        self.settings: dict[str, Any] = {"interactions": interactions, "outer_bags": outer_bags,
                                         "max_rounds": max_rounds, "random_state": seed}  # fmt: skip

    def _fit_one(self, dest: int, m: np.ndarray, y: np.ndarray, w: np.ndarray, eval_set: Any) -> Any:
        from interpret.glassbox import ExplainableBoostingClassifier

        model = ExplainableBoostingClassifier(feature_names=list(self.features), **self.settings)
        return model.fit(m, y, sample_weight=w)

    def contributions(self, X: dict[str, np.ndarray]) -> np.ndarray:
        """Each single-feature term's score in the adverse move's model (interaction terms left out)."""
        model = self._adverse_model()
        scores = np.asarray(model.eval_terms(matrix(X, self.features)))
        names = list(model.term_names_)
        out = np.zeros((scores.shape[0], len(self.features)))
        for k, f in enumerate(self.features):
            if f in names:
                out[:, k] = scores[:, names.index(f)]
        return out

    def params(self) -> dict[str, Any]:
        return self.base_params() | self.settings | {"early_stopping": "random 15% per bag (L-025)"}


FAMILIES: dict[str, type] = {
    "logistic": TransitionLogistic,
    "lightgbm": TransitionLightGBM,
    "ebm": TransitionEBM,
}


def make(name: str, start: int, **settings: Any) -> Any:
    if name not in FAMILIES:
        raise ValueError(f"unknown family {name!r}; use one of {', '.join(FAMILIES)}")
    return FAMILIES[name](start, **settings)


def start_code(text: str | int) -> int:
    """'current', '30' or '60' (or the code) -> the state code."""
    if isinstance(text, int):
        return _check_start(text)
    if str(text) not in fx.STATES[:3]:
        raise ValueError(f"start state must be current, 30 or 60, not {text!r}")
    return fx.STATES.index(str(text))
