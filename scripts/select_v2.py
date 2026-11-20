"""Model v2 selection (ADR-009 Amendment 3): apply the 2% rule per step to the six training runs.

    CECL_ENV=dev python -m scripts.select_v2 --commit <full hash> [--log]

Finds the newest v2 training run per (step, family) at one commit (logistic and LightGBM; the EBM is
dropped for v2), reads each run's `summary.json`, `exams.json` and `lock_band.json`, applies
`cecl.models.v2_pipeline.select_step` and prints the selection table and the chosen combination with its
run ids (the walk-forward job's `chosen` and `logistic` parameters). With `--log` it also logs one
`v2-selection` run and writes `docs/phase2/model_v2_selection.json` (aggregates only). API calls, no
compute. The holdout is not touched.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from cecl import tracking
from cecl.models import oot
from cecl.models import v2_pipeline as v2

OUT = Path("docs/phase2/model_v2_selection.json")


def newest(runs: list[Any]) -> dict[tuple[str, str], Any]:
    found: dict[tuple[str, str], Any] = {}
    for r in runs:
        t = r.data.tags
        if (t.get("model_version") == "v2" and t.get("stage") == "train" and t.get("transition") in oot.STEPS
                and t.get("family") in v2.FAMILIES and t.get("sensitivity", "none") == "none"):  # fmt: skip
            key = (t["transition"], t["family"])
            if key not in found or r.info.start_time > found[key].info.start_time:
                found[key] = r
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--commit", required=True)
    parser.add_argument("--log", action="store_true", help="log the selection run and write the JSON record")
    args = parser.parse_args(argv)
    import mlflow

    tracking.configure(mlflow)
    runs = mlflow.search_runs(filter_string=f"tags.git_commit = '{args.commit}'", search_all_experiments=True,
                              output_format="list")  # fmt: skip
    found = newest(runs)
    missing = [f"{s}/{f}" for s in oot.STEPS for f in v2.FAMILIES if (s, f) not in found]
    if missing:
        raise SystemExit(f"v2 training runs missing at {args.commit[:12]}: {missing}")
    record: dict[str, Any] = {"rules": "ADR-009 Amendment 3", "commit": args.commit, "steps": {}}
    chosen, logistic = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        for step in oot.STEPS:
            results = {}
            for fam in v2.FAMILIES:
                run = found[(step, fam)]
                art = {}
                for name in ("summary", "exams", "lock_band"):
                    path = mlflow.artifacts.download_artifacts(run_id=run.info.run_id, artifact_path=f"{name}.json",
                                                               dst_path=f"{tmp}/{run.info.run_id}")  # fmt: skip
                    with open(path, encoding="utf-8") as fh:
                        art[name] = json.load(fh)
                results[fam] = {"average": art["summary"]["average_exam_log_loss"], "exams": art["exams"],
                                "lock_band": art["lock_band"], "reason_codes": art["summary"]["reason_codes"]}  # fmt: skip
            sel = v2.select_step(results)
            for row in sel["table"]:
                row["run_id"] = found[(step, row["family"])].info.run_id
                row["chosen_setting"] = found[(step, row["family"])].data.params.get("C") or {
                    k: found[(step, row["family"])].data.params.get(k)
                    for k in ("num_leaves", "min_child_weight")
                }
                row["failing_cells"] = row["failing_cells"][:40]
            record["steps"][step] = sel
            chosen[step] = found[(step, sel["winner"])].info.run_id
            logistic[step] = found[(step, "logistic")].info.run_id
    record["chosen"], record["logistic"] = chosen, logistic
    for step in oot.STEPS:
        s = record["steps"][step]
        print(f"\n{step}: winner {s['winner']} ({s['why']})")
        for row in s["table"]:
            print(f"  {row['family']:9s} exam log loss {row['average_log_loss']:.5f} gain {row['gain_over_logistic']:+.2%} "
                  f"bars {'pass' if row['bars_pass'] else 'FAIL'} reasons {row['reason_codes']} "
                  f"failing {len(row['failing_cells'])}")  # fmt: skip
    print("\nchosen=" + json.dumps(chosen) + "\nlogistic=" + json.dumps(logistic))
    if args.log:
        OUT.write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
        experiment = found[(oot.STEPS[0], "logistic")].info.experiment_id
        with mlflow.start_run(experiment_id=experiment, run_name="v2-selection") as run:
            mlflow.set_tags({"model_version": "v2", "stage": "selection", "rules": "ADR-009 Amendment 3",
                             "git_commit": args.commit,
                             "chosen": ";".join(f"{s}:{record['steps'][s]['winner']}" for s in oot.STEPS)})  # fmt: skip
            mlflow.log_params(
                {f"run_{s}": chosen[s] for s in oot.STEPS} | {f"logistic_{s}": logistic[s] for s in oot.STEPS}
            )
            mlflow.log_dict(record, "selection.json")
        print(f"selection run {run.info.run_id}; record written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
