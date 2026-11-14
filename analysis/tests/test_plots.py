"""Tests for the comparison and surge-timeline figure infrastructure."""

from __future__ import annotations

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
    plot_surge_timeline,
    policy_offsets,
)
from analysis.tests.test_validation import build_layout


class PlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.results, self.experiments = build_layout(self.root)
        self.dataset = load_dataset(self.results, self.experiments)

    def tearDown(self) -> None:
        self.temporary.cleanup()

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


if __name__ == "__main__":
    unittest.main()
