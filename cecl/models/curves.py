"""Training curves (Phase 2, Martins 17 Nov 2026): training vs 2017-2019 validation, never the test.

- **LightGBM:** training and validation log loss at every boosting round, recorded during the fit
  (`LightGBMPair.rounds`), and the round with the lowest validation loss. That round is *reported*;
  the model keeps its fixed number of rounds unless Martins approves early stopping (choosing rounds
  on validation would be further tuning on validation).
- **Logistic and EBM:** learning curves by training-sample size (10%, 25%, 50%, 100% of the sampled
  training rows; the smaller samples re-weighted so they still describe the full training months),
  with training and validation log loss and AUC (and Gini) at each size.
- **A table for all three:** training vs validation log loss and AUC, with a plain-English verdict.

The validation years differ from the training years (the level shift ADR-010 addresses), so part of
any training-validation gap is that shift, not overfitting; the verdict says so when it applies.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from cecl import features as fx
from cecl import metrics

FRACTIONS = (0.10, 0.25, 0.50, 1.00)
TRAIN_SCORE_ROWS = 200_000  # training rows scored for the training-side measures (fixed sample)


def subsample(frame: Any, fraction: float, seed: int = 11) -> Any:
    """A random `fraction` of the rows, re-weighted by 1/fraction (unbiased for the full frame)."""
    if fraction >= 1.0:
        return frame
    out = frame.sample(frac=fraction, random_state=seed).copy()
    out["weight"] = out["weight"] / fraction
    return out


def scores(model: Any, frame: Any, inputs: Any) -> dict[str, float]:
    """Weighted log loss (default, overall) and default AUC and Gini of a model on a frame."""
    p = model.predict_proba(inputs(model, frame))
    y = frame["outcome"].to_numpy(dtype=int)
    w = frame["weight"].to_numpy(dtype=float)
    auc = metrics.auc(y == fx.DEFAULT, p[:, fx.DEFAULT], w)
    return {"log_loss_default": metrics.cause_log_loss(y, p, w, "default"), "log_loss": metrics.log_loss(y, p, w),
            "auc_default": auc, "gini_default": metrics.gini(auc)}  # fmt: skip


def train_scores(model: Any, train: Any, inputs: Any) -> dict[str, float]:
    sample = train.sample(n=min(TRAIN_SCORE_ROWS, len(train)), random_state=7)
    return scores(model, sample, inputs)


def learning_curve(fit: Any, train: Any, valid: Any, inputs: Any, fractions: tuple[float, ...] = FRACTIONS,
                   full_model: Any = None) -> list[dict[str, Any]]:  # fmt: skip
    """`fit(frame)` returns a fitted model; at 100% the already-trained `full_model` is reused."""
    rows = []
    for f in fractions:
        part = subsample(train, f)
        model = full_model if (f >= 1.0 and full_model is not None) else fit(part)
        tr, va = train_scores(model, part, inputs), scores(model, valid, inputs)
        rows.append({"fraction": f, "rows": int(len(part)),
                     **{f"train_{k}": v for k, v in tr.items()}, **{f"valid_{k}": v for k, v in va.items()}})  # fmt: skip
    return rows


def verdict(train: dict[str, float], valid: dict[str, float]) -> str:
    """Plain English, from the training vs validation default log loss and AUC."""
    ll_ratio = valid["log_loss_default"] / train["log_loss_default"]
    auc_gap = train["auc_default"] - valid["auc_default"]
    if auc_gap > 0.03 and ll_ratio > 1.25:
        return ("overfitting: clearly better on the training rows than on validation, in both ranking and "
                "probability accuracy")  # fmt: skip
    if auc_gap <= 0.01 and ll_ratio > 1.25:
        return ("no sign of overfitting in ranking (AUC holds up on validation); the higher validation log loss "
                "comes from the level shift between the periods (ADR-010), not from memorising the training rows")  # fmt: skip
    if auc_gap <= 0.01 and ll_ratio <= 1.10:
        return "generalises well: training and validation agree"
    return "mild gap between training and validation; watch it, no action on its own"


def fit_table(results: dict[str, dict[str, dict[str, float]]]) -> list[dict[str, Any]]:
    """One row per family: training vs validation log loss and AUC/Gini, and the verdict."""
    out = []
    for family, r in results.items():
        tr, va = r["train"], r["valid"]
        out.append({"family": family, **{f"train_{k}": tr[k] for k in tr}, **{f"valid_{k}": va[k] for k in va},
                    "verdict": verdict(tr, va)})  # fmt: skip
    return out


def round_figure(rounds: dict[str, Any], title: str) -> Any:
    """LightGBM: training and validation log loss by boosting round, one panel per cause."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, len(rounds), figsize=(6 * len(rounds), 4))
    for ax, (cause, r) in zip(np.atleast_1d(axes), rounds.items(), strict=True):
        ax.plot(range(1, len(r["training"]) + 1), r["training"], label="training (1999-2016)")
        ax.plot(range(1, len(r["validation"]) + 1), r["validation"], label="validation (2017-2019)")
        ax.axvline(
            r["best_round"], linestyle="--", color="grey", label=f"lowest validation: round {r['best_round']}"
        )
        ax.set_title(f"{title}: {cause}")
        ax.set_xlabel("boosting round")
        ax.set_ylabel("log loss (weighted)")
        ax.legend()
    fig.tight_layout()
    return fig


def learning_figure(rows: list[dict[str, Any]], title: str, loss: str = "log_loss_default", auc: str = "auc_default",
                    loss_label: str = "default log loss (weighted)",
                    auc_label: str = "default AUC (Gini = 2 x AUC - 1)") -> Any:  # fmt: skip
    """Learning curve: training and validation log loss and AUC by training-sample size."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    x = [r["fraction"] * 100 for r in rows]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4))
    a1.plot(x, [r[f"train_{loss}"] for r in rows], marker="o", label="training")
    a1.plot(x, [r[f"valid_{loss}"] for r in rows], marker="o", label="validation (2017-2019)")
    a1.set_xlabel("% of the sampled training rows")
    a1.set_ylabel(loss_label)
    a2.plot(x, [r[f"train_{auc}"] for r in rows], marker="o", label="training")
    a2.plot(x, [r[f"valid_{auc}"] for r in rows], marker="o", label="validation (2017-2019)")
    a2.set_xlabel("% of the sampled training rows")
    a2.set_ylabel(auc_label)
    for ax in (a1, a2):
        ax.set_title(title)
        ax.legend()
    fig.tight_layout()
    return fig


def log_rounds(mlflow: Any, rounds: dict[str, Any], prefix: str = "round") -> None:
    """Per-round training and validation log loss as MLflow metric histories (charts in the UI)."""
    from mlflow.entities import Metric

    run = mlflow.active_run()
    client = mlflow.MlflowClient()
    batch = [
        Metric(f"{prefix}_{cause}_{part}_log_loss", float(v), 0, step)
        for cause, r in rounds.items()
        for part in ("training", "validation")
        for step, v in enumerate(r[part], start=1)
    ]
    for i in range(0, len(batch), 1000):  # MLflow accepts up to 1000 metrics per call
        client.log_batch(run.info.run_id, metrics=batch[i : i + 1000])
