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
        self._old_metrics_path = metrics.csv_path
        self.temporary = tempfile.TemporaryDirectory()
        metrics.results_dir = Path(self.temporary.name)
        metrics.per_second_path = metrics.results_dir / "per_second.csv"
        metrics.csv_path = metrics.results_dir / "metrics.csv"

    def tearDown(self) -> None:
        metrics.results_dir = self._old_dir
        metrics.per_second_path = self._old_path
        metrics.csv_path = self._old_metrics_path
        self.temporary.cleanup()

    def test_writes_every_second_and_handles_partial_last_bucket(self) -> None:
        buckets = {
            0: {
                "sequences": {0},
                "payload_bytes": 100,
                "received_bytes": 128,
                "latencies_ns": [1_000_000],
            },
            # second 1 is deliberately absent: the graph still needs a zero row
            2: {
                "sequences": {2},
                "payload_bytes": 200,
                "received_bytes": 228,
                "latencies_ns": [2_000_000],
            },
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
        self.assertEqual(rows[1]["received_bytes"], "0")
        self.assertEqual(rows[2]["payload_bytes"], "200")
        self.assertEqual(rows[2]["received_bytes"], "228")
        self.assertAlmostEqual(float(rows[2]["goodput_mbps"]), 0.0032, places=6)
        self.assertAlmostEqual(float(rows[2]["throughput_mbps"]), 0.003648, places=6)
        self.assertAlmostEqual(float(rows[2]["p95_latency_ms"]), 2.0, places=6)

    def test_throughput_counts_header_bytes_but_goodput_does_not(self) -> None:
        metrics.record_run(
            experiment_id="9000",
            run_id=9000,
            flow="high",
            expected_packets=10,
            sequences=set(range(10)),
            duplicates=0,
            out_of_order=0,
            payload_bytes=1_720,
            received_bytes=2_000,
            duration_seconds=2.0,
            latencies_ns=[1_000_000] * 10,
        )

        with metrics.csv_path.open(newline="") as handle:
            row = next(csv.DictReader(handle))

        self.assertEqual(row["received_bytes"], "2000")
        self.assertAlmostEqual(float(row["goodput_mbps"]), 0.00688, places=6)
        self.assertAlmostEqual(float(row["throughput_mbps"]), 0.008, places=6)

    def test_zero_duration_gives_zero_throughput(self) -> None:
        metrics.record_run(
            experiment_id="9003",
            run_id=9003,
            flow="high",
            expected_packets=1,
            sequences={0},
            duplicates=0,
            out_of_order=0,
            payload_bytes=172,
            received_bytes=200,
            duration_seconds=0.0,
            latencies_ns=[1_000_000],
        )

        with metrics.csv_path.open(newline="") as handle:
            row = next(csv.DictReader(handle))

        self.assertEqual(row["goodput_mbps"], "0.0")
        self.assertEqual(row["throughput_mbps"], "0.0")

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
