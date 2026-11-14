"""Unit tests for app/metrics.py record_per_second()."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app import metrics


class PerSecondMetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_dir = metrics.results_dir
        self._old_path = metrics.per_second_path
        self.temporary = tempfile.TemporaryDirectory()
        metrics.results_dir = Path(self.temporary.name)
        metrics.per_second_path = metrics.results_dir / "per_second.csv"

    def tearDown(self) -> None:
        metrics.results_dir = self._old_dir
        metrics.per_second_path = self._old_path
        self.temporary.cleanup()

    def test_writes_every_second_and_handles_partial_last_bucket(self) -> None:
        buckets = {
            0: {"sequences": {0}, "payload_bytes": 100, "latencies_ns": [1_000_000]},
            # second 1 is deliberately absent: the graph still needs a zero row
            2: {"sequences": {2}, "payload_bytes": 200, "latencies_ns": [2_000_000]},
        }

        metrics.record_per_second(
            experiment_id="9001",
            run_id=9001,
            flow="high",
            buckets=buckets,
            expected_packets=300,
            duration_seconds=2.5,
        )

        with metrics.per_second_path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([int(row["second"]) for row in rows], [0, 1, 2])
        self.assertEqual([float(row["bucket_seconds"]) for row in rows], [1.0, 1.0, 0.5])
        self.assertEqual(sum(int(row["expected_packets"]) for row in rows), 300)
        self.assertEqual(rows[1]["unique_received"], "0")
        self.assertEqual(rows[1]["payload_bytes"], "0")
        self.assertEqual(rows[2]["payload_bytes"], "200")
        self.assertAlmostEqual(float(rows[2]["goodput_mbps"]), 0.0032, places=6)
        self.assertAlmostEqual(float(rows[2]["p95_latency_ms"]), 2.0, places=6)

    def test_no_buckets_writes_nothing(self) -> None:
        metrics.record_per_second(
            experiment_id="9002",
            run_id=9002,
            flow="high",
            buckets={},
            expected_packets=100,
            duration_seconds=1.0,
        )
        self.assertFalse(metrics.per_second_path.exists())


if __name__ == "__main__":
    unittest.main()
