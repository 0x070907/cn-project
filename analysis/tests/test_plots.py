"""Tests for the comparison and surge-timeline figure infrastructure."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from analysis.aggregate import aggregate_flow_medians, comparison_rows
from analysis.loader import load_dataset
from analysis.plots import (
    estimate_start_ns,
    event_offsets,
    median_timeline_rows,
    plot_comparison,
    plot_surge_figures,
    plot_surge_timeline,
    policy_offsets,
    surge_run_inputs,
)
from analysis.schema import (
    EVENT_FIELDS,
    EXPECTED_FLOWS,
    PER_SECOND_FIELDS,
    POLICY_FIELDS,
)
from analysis.tests.test_aggregation import append_summary_run
from analysis.tests.test_validation import build_layout, per_second_row


class PlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.results, self.experiments = build_layout(self.root)
        self.dataset = load_dataset(self.results, self.experiments)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_surge_timeline_uses_only_latest_three_runs(self) -> None:
        legacy_run = 2000
        append_summary_run(
            self.experiments,
            "dynamic",
            "surge",
            "remote",
            legacy_run,
            EXPECTED_FLOWS["surge"],
        )
        with (self.results / "per_second.csv").open("a", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=PER_SECOND_FIELDS)
            for flow in EXPECTED_FLOWS["surge"]:
                writer.writerow(per_second_row(legacy_run, flow))

        dataset = load_dataset(self.results, self.experiments)
        inputs = surge_run_inputs(
            dataset,
            self.results,
            self.experiments,
            canonical="dynamic",
        )
        self.assertEqual([item["run_id"] for item in inputs], [2110, 2111, 2112])

        paths = plot_surge_figures(
            dataset,
            self.results,
            self.experiments,
            self.root / "surge-selected",
        )
        names = [path.name for path in paths]
        self.assertNotIn(f"timeline_dynamic_{legacy_run}.png", names)
        self.assertIn("timeline_dynamic_2110.png", names)
        self.assertIn("timeline_dynamic_median.png", names)

    def test_comparison_figure_is_written(self) -> None:
        rows = comparison_rows(aggregate_flow_medians(self.dataset))
        path = plot_comparison(rows, self.root / "figures", metric="goodput_mbps")
        self.assertTrue(path.exists())
        self.assertGreater(path.stat().st_size, 1000)

    def test_event_start_is_recovered_from_delayed_join(self) -> None:
        events = [
            {
                "ts_ns": 1_000_000_000_000 + 10_000_000_000,
                "event": "flow_start",
                "flow": "background",
                "detail": "joined 10s into the run",
            }
        ]
        start = estimate_start_ns(events)
        self.assertEqual(start, 1_000_000_000_000)
        offsets = event_offsets(events, start)
        self.assertEqual(len(offsets), 1)
        self.assertAlmostEqual(offsets[0][0], 10.0)

    def test_policy_offsets_use_the_same_clock(self) -> None:
        policy = [
            {
                "ts_ns": 1_000_000_000_000 + 11_000_000_000,
                "flow_id": "high",
                "old_queue": 0,
                "new_queue": 1,
                "reason": "overload",
            }
        ]
        offsets = policy_offsets(policy, 1_000_000_000_000)
        self.assertEqual(len(offsets), 1)
        self.assertAlmostEqual(offsets[0][0], 11.0)
        self.assertIn("q0→q1", offsets[0][1])

    def test_timeline_and_median_figure_are_written(self) -> None:
        dynamic_rows = [row for row in self.dataset.per_second if row["run_id"] == 2110]
        self.assertTrue(dynamic_rows)
        events = [
            {
                "ts_ns": 1_000_000_000_000,
                "event": "run_start",
                "flow": "",
                "detail": "",
            }
        ]
        policy = [
            {
                "ts_ns": 1_000_000_000_000 + 11_000_000_000,
                "flow_id": "high",
                "old_queue": 0,
                "new_queue": 1,
                "reason": "overload",
            }
        ]
        path = plot_surge_timeline(
            dynamic_rows,
            self.root / "figures" / "timeline.png",
            events=events,
            policy=policy,
            run_id=2110,
        )
        self.assertTrue(path.exists())

        median_rows = median_timeline_rows(self.dataset.per_second, [2110, 2111, 2112])
        self.assertTrue(median_rows)
        self.assertEqual({row["run_id"] for row in median_rows}, {0})
        median_path = plot_surge_timeline(median_rows, self.root / "figures" / "median.png")
        self.assertTrue(median_path.exists())

    def test_surge_inputs_find_events_and_policy_files(self) -> None:
        dynamic_dir = self.experiments / "dynamic"
        dynamic_dir.mkdir(parents=True, exist_ok=True)
        start_ns = 1_000_000_000_000

        with (dynamic_dir / "events_2110.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=EVENT_FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "ts_ns": start_ns,
                    "run_id": 2110,
                    "event": "run_start",
                    "flow": "",
                    "detail": "",
                }
            )

        with (dynamic_dir / "policy_2110.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=POLICY_FIELDS)
            writer.writeheader()
            writer.writerow(
                {
                    "ts_ns": start_ns + 11_000_000_000,
                    "flow_id": "high",
                    "old_queue": 0,
                    "new_queue": 1,
                    "reason": "overload",
                }
            )

        inputs = surge_run_inputs(
            self.dataset, self.results, self.experiments, canonical="dynamic"
        )
        first = inputs[0]
        self.assertTrue(first["events"])
        self.assertTrue(first["policy"])
        self.assertIsNotNone(first["events_path"])
        self.assertIsNotNone(first["policy_path"])

        paths = plot_surge_figures(
            self.dataset, self.results, self.experiments, self.root / "surge"
        )
        self.assertTrue(paths)
        self.assertTrue(any("median" in path.name for path in paths))


if __name__ == "__main__":
    unittest.main()
