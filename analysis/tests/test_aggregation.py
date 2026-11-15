"""Tests for the summary loader and median aggregation."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from analysis.aggregate import aggregate_fairness, aggregate_flow_medians, fairness_per_run
from analysis.loader import load_dataset
from analysis.schema import EXPECTED_FLOWS, SUMMARY_FIELDS
from analysis.tests.test_validation import build_layout, metric_row


def append_summary_run(
    experiments: Path,
    folder: str,
    scenario: str,
    mode: str,
    run_id: int,
    flows: tuple[str, ...],
    *,
    goodput_mbps: float | None = None,
) -> None:
    path = experiments / folder / "summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        if is_new:
            writer.writeheader()
        for flow in flows:
            row = metric_row(run_id, flow)
            if goodput_mbps is not None:
                row["goodput_mbps"] = goodput_mbps
            writer.writerow({"scenario": scenario, "mode": mode, **row})


class AggregationTests(unittest.TestCase):
    def test_medians_use_all_three_repeats(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            dataset = load_dataset(results, experiments)
            rows = aggregate_flow_medians(dataset)
            by_key = {(row["experiment"], row["flow"]): row for row in rows}

            dynamic_high = by_key[("dynamic", "high")]
            self.assertEqual(dynamic_high["n_runs"], 3)
            self.assertEqual(dynamic_high["run_ids"], "2110 2111 2112")
            self.assertAlmostEqual(dynamic_high["packet_loss_pct_median"], 0.0)
            self.assertIsNotNone(dynamic_high["goodput_mbps_median"])
            self.assertAlmostEqual(
                dynamic_high["throughput_mbps_median"],
                0.16,
                places=6,
            )
            self.assertGreater(
                dynamic_high["throughput_mbps_median"],
                dynamic_high["goodput_mbps_median"],
            )

            # The no-congestion baseline only promises two flows.
            self.assertIn(("baseline", "high"), by_key)
            self.assertNotIn(("baseline", "low"), by_key)

    def test_older_nonconsecutive_runs_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            append_summary_run(
                experiments,
                "baseline",
                "no_congestion",
                "none",
                2000,
                EXPECTED_FLOWS["no_congestion"],
                goodput_mbps=99.0,
            )

            dataset = load_dataset(results, experiments)
            rows = aggregate_flow_medians(dataset)
            baseline = {
                row["flow"]: row
                for row in rows
                if row["experiment"] == "baseline"
            }
            self.assertEqual(baseline["high"]["run_ids"], "2101 2102 2103")
            self.assertNotEqual(baseline["high"]["goodput_mbps_median"], 99.0)

    def test_practice_groups_are_separate_from_the_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            append_summary_run(
                experiments,
                "test",
                "no_congestion",
                "none",
                9000,
                EXPECTED_FLOWS["no_congestion"],
            )

            dataset = load_dataset(results, experiments)
            final_experiments = {
                row["experiment"] for row in aggregate_flow_medians(dataset)
            }
            practice_experiments = {
                row["experiment"]
                for row in aggregate_flow_medians(dataset, practice=True)
            }
            self.assertNotIn("test/no_congestion", final_experiments)
            self.assertEqual(practice_experiments, {"test/no_congestion"})

            practice_fairness = aggregate_fairness(dataset, practice=True)
            self.assertEqual(
                {row["experiment"] for row in practice_fairness},
                {"test/no_congestion"},
            )

    def test_fairness_prefers_fairness_csv_and_aggregates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            dataset = load_dataset(results, experiments)
            per_run = fairness_per_run(dataset)
            self.assertTrue(per_run)
            self.assertTrue(all(row["source"] == "fairness.csv" for row in per_run))
            self.assertTrue(all(row["jains_fairness"] == 1.0 for row in per_run))

            table = aggregate_fairness(dataset)
            by_name = {row["experiment"]: row for row in table}
            self.assertEqual(set(by_name), {"baseline", "baseline_ryu", "static", "dynamic", "static_surge"})
            self.assertEqual(by_name["dynamic"]["n_runs"], 3)
            self.assertAlmostEqual(by_name["dynamic"]["jains_fairness_median"], 1.0)

    def test_missing_per_second_is_reported_not_invented(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            (results / "per_second.csv").unlink()
            dataset = load_dataset(results, experiments)
            self.assertEqual(dataset.per_second, [])
            self.assertIn(str(results / "per_second.csv"), dataset.missing_files)


if __name__ == "__main__":
    unittest.main()
