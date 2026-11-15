"""End-to-end checks for final-report selection in analysis/analyze.py."""

from __future__ import annotations

import argparse
import csv
import tempfile
import unittest
from pathlib import Path

from analysis.analyze import run_pipeline
from analysis.schema import EXPECTED_FLOWS
from analysis.tests.test_validation import append_summary_run, build_layout


def pipeline_args(
    results: Path,
    experiments: Path,
    output: Path,
    *,
    practice: bool = False,
    strict: bool = False,
    final: bool = False,
    skip_plots: bool = True,
) -> argparse.Namespace:
    return argparse.Namespace(
        results=results,
        experiments=experiments,
        output=output,
        policy_log=None,
        practice=practice,
        skip_plots=skip_plots,
        strict=strict,
        final=final,
        force=False,
    )


class AnalyzeSelectionTests(unittest.TestCase):
    def test_final_pipeline_reports_only_latest_three_canonical_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results, experiments = build_layout(root)
            append_summary_run(
                experiments,
                "baseline",
                "no_congestion",
                "none",
                2000,
                ("high",),
            )
            append_summary_run(
                experiments,
                "test",
                "no_congestion",
                "none",
                9000,
                EXPECTED_FLOWS["no_congestion"],
            )
            output = root / "final-output"

            status = run_pipeline(
                pipeline_args(
                    results,
                    experiments,
                    output,
                    practice=False,
                    strict=True,
                    final=True,
                )
            )
            self.assertEqual(status, 0)

            with (output / "tables" / "per_flow_medians.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(
                {row["experiment"] for row in rows},
                {"baseline", "baseline_ryu", "static", "dynamic", "static_surge"},
            )
            baseline = {
                row["flow"]: row
                for row in rows
                if row["experiment"] == "baseline"
            }
            self.assertEqual(baseline["high"]["run_ids"], "2101 2102 2103")
            self.assertNotIn("test/no_congestion", {row["experiment"] for row in rows})

    def test_practice_view_is_separate_from_the_final_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results, experiments = build_layout(root)
            append_summary_run(
                experiments,
                "test",
                "no_congestion",
                "none",
                9000,
                EXPECTED_FLOWS["no_congestion"],
            )
            output = root / "practice-output"

            status = run_pipeline(
                pipeline_args(
                    results,
                    experiments,
                    output,
                    practice=True,
                    strict=False,
                    final=False,
                    skip_plots=False,
                )
            )
            self.assertEqual(status, 0)

            results_tables = (output / "results_tables.md").read_text()
            practice_tables = (output / "practice_tables.md").read_text()
            self.assertNotIn("test/no_congestion", results_tables)
            self.assertIn("test/no_congestion", practice_tables)


if __name__ == "__main__":
    unittest.main()
