"""Lock the model set for the out-of-time test: log the multi-state `selection` run (ADR-009 as amended).

    CECL_ENV=dev python -m scripts.lock_model_set --commit <full hash> --input-build <stamp> \
        --expect current=logistic,30=logistic,60=logistic

Finds the nine training runs at one commit (three families x three starting states, not the COVID
sensitivity), re-applies the selection rule (`cecl.models.selection.choose`) to each step from the runs'
own `validation.json`, refuses unless the result is the set Martins approved (`--expect`), and logs one
`selection` run naming the locked models (`run_current`, `run_30`, `run_60`) and, for information, every
model at every step. The out-of-time launcher and `scripts/register_challenger.py` read this run.
Refuses if a selection run for the same commit already exists. API calls, no compute.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from typing import Any

from cecl import tracking
from cecl.models import oot, selection

DESIGN = "multi-state (ADR-011)"
FAMILIES = ("logistic", "lightgbm", "ebm")


def parse_expect(text: str) -> dict[str, str]:
    """'current=logistic,30=logistic,60=logistic' -> {step: family}; every step must be named."""
    pairs = dict(p.split("=", 1) for p in text.split(",") if p)
    if sorted(pairs) != sorted(oot.STEPS) or any(f not in FAMILIES for f in pairs.values()):
        raise ValueError(f"--expect must name a family for each of {oot.STEPS}: {text!r}")
    return pairs


def newest_by_step_family(runs: list[Any]) -> dict[tuple[str, str], Any]:
    """The newest training run per (step, family), ignoring sensitivity runs."""
    found: dict[tuple[str, str], Any] = {}
    for r in runs:
        t = r.data.tags
        if (
            t.get("transition") in oot.STEPS
            and t.get("family") in FAMILIES
            and t.get("sensitivity", "none") == "none"
        ):
            key = (t["transition"], t["family"])
            if key not in found or r.info.start_time > found[key].info.start_time:
                found[key] = r
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--commit", required=True, help="full git commit of the training runs")
    parser.add_argument("--input-build", required=True, help="stamp of the transition input build")
    parser.add_argument(
        "--expect", required=True, help="the approved set, e.g. current=logistic,30=logistic,60=logistic"
    )
    args = parser.parse_args(argv)
    expect = parse_expect(args.expect)
    import mlflow

    tracking.configure(mlflow)
    earlier = mlflow.search_runs(filter_string=f"tags.stage = 'selection' and tags.locked_commit = '{args.commit}'",
                                 search_all_experiments=True, output_format="list")  # fmt: skip
    if earlier:
        raise SystemExit(f"a selection run for {args.commit[:12]} already exists ({earlier[0].info.run_id})")
    runs = mlflow.search_runs(filter_string=f"tags.git_commit = '{args.commit}'", search_all_experiments=True,
                              output_format="list")  # fmt: skip
    found = newest_by_step_family(runs)
    missing = [f"{s}/{f}" for s in oot.STEPS for f in FAMILIES if (s, f) not in found]
    if missing:
        raise SystemExit(f"training runs missing at {args.commit[:12]}: {missing}")
    choices, record = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for step in oot.STEPS:
            results = []
            for fam in FAMILIES:
                run = found[(step, fam)]
                path = mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path="validation.json",
                                                           dst_path=f"{tmp}/{run.info.run_id}")  # fmt: skip
                with open(path, encoding="utf-8") as fh:
                    v = json.load(fh)
                results.append(
                    selection.FamilyResult(fam, v["log_loss"], v["calibration_passes"], v["reason_codes"])
                )
            choice = selection.choose(results)
            choices[step] = choice.chosen
            record[step] = {"chosen": choice.chosen, "reasons": choice.reasons}
    wrong = {s: (choices[s], expect[s]) for s in oot.STEPS if choices[s] != expect[s]}
    if wrong:
        raise SystemExit(f"the rule's choice differs from the approved set (step: rule, approved): {wrong}")
    experiment = found[(oot.STEPS[0], expect[oot.STEPS[0]])].info.experiment_id
    with mlflow.start_run(experiment_id=experiment, run_name="selection-locked-multistate") as run:
        mlflow.set_tags({"stage": "selection", "design": DESIGN, "locked_commit": args.commit,
                         "input_build": args.input_build, "chosen": ";".join(f"{s}:{choices[s]}" for s in oot.STEPS),
                         "approved_by": "Martins, 18 Nov 2026", "git_commit": args.commit})  # fmt: skip
        mlflow.log_params({f"run_{s}": found[(s, choices[s])].info.run_id for s in oot.STEPS}
                          | {f"info_{s}_{f}": found[(s, f)].info.run_id for s in oot.STEPS for f in FAMILIES})  # fmt: skip
        mlflow.log_dict(record, "selection.json")
    print(f"selection run {run.info.run_id}: " + ", ".join(f"{s} {choices[s]} ({found[(s, choices[s])].info.run_id[:8]})"
                                                          for s in oot.STEPS))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
