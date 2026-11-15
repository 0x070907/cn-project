"""Regression tests for metrics/summary CSV schema migration."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app import metrics, summary
from app.csv_schema import CsvSchemaError, write_rows


def legacy_metric_values(
    *,
    run_id: int = 100,
    flow: str = "high",
    expected: int = 2,
    unique: int = 2,
    payload: int = 344,
    duration: float = 1.0,
) -> dict[str, str]:
    lost = max(0, expected - unique)
    return {
        "experiment_id": str(run_id),
        "run_id": str(run_id),
        "flow": flow,
        "expected_packets": str(expected),
        "unique_received": str(unique),
        "duplicates": "0",
        "out_of_order": "0",
        "lost_packets": str(lost),
        "packet_loss_pct": str(round(lost / expected * 100, 4)),
        "payload_bytes": str(payload),
        "duration_seconds": str(duration),
        "goodput_mbps": str(round(payload * 8 / duration / 1_000_000, 6)),
        "avg_latency_ms": "0.1",
        "p50_latency_ms": "0.1",
        "p95_latency_ms": "0.2",
    }


def current_metric_values(
    *,
    run_id: int = 101,
    flow: str = "medium",
    expected: int = 2,
    unique: int = 2,
    payload: int = 944,
    received: int = 1000,
    duration: float = 2.0,
) -> dict[str, str]:
    lost = max(0, expected - unique)
    return {
        "experiment_id": str(run_id),
        "run_id": str(run_id),
        "flow": flow,
        "expected_packets": str(expected),
        "unique_received": str(unique),
        "duplicates": "0",
        "out_of_order": "0",
        "lost_packets": str(lost),
        "packet_loss_pct": str(round(lost / expected * 100, 4)),
        "payload_bytes": str(payload),
        "received_bytes": str(received),
        "duration_seconds": str(duration),
        "goodput_mbps": str(round(payload * 8 / duration / 1_000_000, 6)),
        "throughput_mbps": str(round(received * 8 / duration / 1_000_000, 6)),
        "avg_latency_ms": "0.1",
        "p50_latency_ms": "0.1",
        "p95_latency_ms": "0.2",
    }


def write_dict_csv(
    path: Path,
    fields: list[str] | tuple[str, ...],
    rows: list[dict[str, str]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class MetricsSchemaMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_results_dir = metrics.results_dir
        self._old_csv_path = metrics.csv_path
        self.temporary = tempfile.TemporaryDirectory()
        metrics.results_dir = Path(self.temporary.name)
        metrics.csv_path = metrics.results_dir / "metrics.csv"

    def tearDown(self) -> None:
        metrics.results_dir = self._old_results_dir
        metrics.csv_path = self._old_csv_path
        self.temporary.cleanup()

    def test_legacy_csv_is_migrated_and_rerun_is_idempotent(self) -> None:
        legacy = legacy_metric_values()
        write_dict_csv(metrics.csv_path, metrics.legacy_fields, [legacy])

        self.assertTrue(metrics.ensure_metrics_schema())
        before = metrics.csv_path.read_text()
        self.assertFalse(metrics.ensure_metrics_schema())
        self.assertEqual(metrics.csv_path.read_text(), before)

        rows = metrics.read_metrics_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(list(row), metrics.fields)
        self.assertEqual(row["received_bytes"], "400")
        self.assertEqual(row["goodput_mbps"], legacy["goodput_mbps"])
        self.assertEqual(row["throughput_mbps"], "0.0032")
        self.assertEqual(row["duration_seconds"], "1.0")

    def test_new_format_csv_reads_without_rewriting(self) -> None:
        current = current_metric_values()
        write_dict_csv(metrics.csv_path, metrics.fields, [current])
        before = metrics.csv_path.read_text()

        rows = metrics.read_metrics_rows()

        self.assertFalse(metrics.ensure_metrics_schema())
        self.assertEqual(metrics.csv_path.read_text(), before)
        self.assertEqual(rows, [current])

    def test_append_after_migration_keeps_one_current_header(self) -> None:
        write_dict_csv(
            metrics.csv_path,
            metrics.legacy_fields,
            [legacy_metric_values()],
        )
        current = current_metric_values()

        metrics.record_run(
            experiment_id=current["experiment_id"],
            run_id=int(current["run_id"]),
            flow=current["flow"],
            expected_packets=int(current["expected_packets"]),
            sequences=set(range(int(current["unique_received"]))),
            duplicates=0,
            out_of_order=0,
            payload_bytes=int(current["payload_bytes"]),
            duration_seconds=float(current["duration_seconds"]),
            latencies_ns=[100_000, 200_000],
            received_bytes=int(current["received_bytes"]),
        )

        with metrics.csv_path.open(newline="") as handle:
            raw = list(csv.reader(handle))

        self.assertEqual(raw[0], metrics.fields)
        self.assertEqual(len(raw), 3)
        self.assertEqual({len(row) for row in raw}, {len(metrics.fields)})
        self.assertEqual(raw[1][metrics.fields.index("received_bytes")], "400")
        self.assertEqual(raw[2][metrics.fields.index("received_bytes")], "1000")
        self.assertEqual(raw[2][metrics.fields.index("throughput_mbps")], "0.004")

    def test_current_width_rows_under_legacy_header_are_preserved(self) -> None:
        current = current_metric_values()
        with metrics.csv_path.open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(metrics.legacy_fields)
            writer.writerow([current[field] for field in metrics.fields])

        row = metrics.read_metrics_rows()[0]

        self.assertEqual(row["received_bytes"], current["received_bytes"])
        self.assertEqual(row["duration_seconds"], current["duration_seconds"])
        self.assertEqual(row["goodput_mbps"], current["goodput_mbps"])
        self.assertEqual(row["throughput_mbps"], current["throughput_mbps"])
        self.assertEqual(row["p95_latency_ms"], current["p95_latency_ms"])

    def test_unknown_schema_is_refused_without_changing_the_file(self) -> None:
        write_rows(metrics.csv_path, ["foo", "bar"], [["1", "2"]])
        before = metrics.csv_path.read_text()

        with self.assertRaises(CsvSchemaError):
            metrics.read_metrics_rows()

        self.assertEqual(metrics.csv_path.read_text(), before)


class SummarySchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_summary_generation_has_current_header_and_no_shifted_values(self) -> None:
        metric_row = current_metric_values()
        path = self.root / "dynamic" / "summary.csv"

        summary.append_summary_rows(path, "surge", "remote", [metric_row])

        with path.open(newline="") as handle:
            raw = list(csv.reader(handle))

        self.assertEqual(raw[0], list(summary.SUMMARY_FIELDS))
        self.assertEqual(len(raw), 2)
        self.assertEqual(len(raw[1]), len(summary.SUMMARY_FIELDS))
        self.assertEqual(raw[1][0:2], ["surge", "remote"])
        for field, value in metric_row.items():
            self.assertEqual(raw[1][summary.SUMMARY_FIELDS.index(field)], value)

    def test_legacy_summary_is_migrated_before_appending(self) -> None:
        path = self.root / "static" / "summary.csv"
        legacy = legacy_metric_values()
        write_dict_csv(
            path,
            summary.LEGACY_SUMMARY_FIELDS,
            [{"scenario": "congestion", "mode": "none", **legacy}],
        )
        current = current_metric_values(run_id=101)

        summary.append_summary_rows(path, "surge", "remote", [current])

        with path.open(newline="") as handle:
            raw = list(csv.reader(handle))

        self.assertEqual(raw[0], list(summary.SUMMARY_FIELDS))
        self.assertEqual(len(raw), 3)
        self.assertEqual({len(row) for row in raw}, {len(summary.SUMMARY_FIELDS)})
        self.assertEqual(raw[1][0:2], ["congestion", "none"])
        self.assertEqual(
            raw[1][summary.SUMMARY_FIELDS.index("received_bytes")],
            "400",
        )
        self.assertEqual(
            raw[1][summary.SUMMARY_FIELDS.index("throughput_mbps")],
            "0.0032",
        )
        self.assertEqual(raw[2][0:2], ["surge", "remote"])
        self.assertEqual(
            raw[2][summary.SUMMARY_FIELDS.index("throughput_mbps")],
            current["throughput_mbps"],
        )
        self.assertNotIn("", raw[0])


if __name__ == "__main__":
    unittest.main()
