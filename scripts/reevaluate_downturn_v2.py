"""Re-evaluate model v2's downturn check under ADR-009 Amendment 5 (no re-run).

    CECL_ENV=dev python -m scripts.reevaluate_downturn_v2 [--log]

Reads the saved results of the newest `v2-downturn-check` run (`downturn_check.json`: the projections are
not repeated), keeps tests 1-6 as recorded and replaces test 7 by its percentage-point form
(`cecl.models.sensitivity.reevaluate`). Prints all four test-7 comparisons in points. With `--log` it logs
one `v2-downturn-check-amendment5` run (tag `source_run` = the check run) holding the new verdict next to
the original one. The original run is not changed. API calls, no compute.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile

from cecl import tracking
from cecl.models import sensitivity as sv
from scripts.register_v2 import newest_check


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--log", action="store_true", help="log the re-evaluation run")
    args = parser.parse_args(argv)
    import mlflow

    tracking.configure(mlflow)
    check = newest_check(mlflow)
    with tempfile.TemporaryDirectory() as tmp:
        path = mlflow.artifacts.download_artifacts(
            run_id=check.info.run_id, artifact_path="downturn_check.json", dst_path=tmp
        )
        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
    out = sv.reevaluate(saved)
    print(
        f"check run {check.info.run_id} (original verdict: {'pass' if saved['verdict']['passes'] else 'fail'})"
    )
    for shock, comps in out["tests"]["7_risk_ordering_points"]["shocks"].items():
        for name, c in comps.items():
            r, s = c["riskier"], c["safer"]
            print(f"  {shock:8s} {name:5s} riskier {r['baseline_pct']:.2f}% -> {r['shocked_pct']:.2f}% "
                  f"({r['rise_points']:+.2f} pts) | safer {s['baseline_pct']:.2f}% -> {s['shocked_pct']:.2f}% "
                  f"({s['rise_points']:+.2f} pts) | {'pass' if c['passes'] else 'FAIL'}")  # fmt: skip
    print("tests:", {k: t["passes"] for k, t in out["tests"].items()}, "| passes:", out["passes"])
    if args.log:
        with mlflow.start_run(
            experiment_id=check.info.experiment_id, run_name="v2-downturn-check-amendment5"
        ) as run:
            mlflow.set_tags({"model_version": "v2", "stage": "downturn_check_amendment5",
                             "rules": "ADR-009 Amendment 5", "source_run": check.info.run_id})  # fmt: skip
            mlflow.log_params({"runs": check.data.params["runs"], "source_run": check.info.run_id})
            mlflow.log_metric("passes", float(out["passes"]))
            mlflow.log_dict(out, "downturn_check_amendment5.json")
        print(f"logged run {run.info.run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
