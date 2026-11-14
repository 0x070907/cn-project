"""Unit tests for analysis/validate.py.

These tests build a complete five-experiment layout in a temporary directory.
The numbers are deliberately synthetic: they test the validator, not the
project's final measurements.
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from analysis.schema import (
    CANONICAL_EXPERIMENTS,
    EXPECTED_FLOWS,
    FAIRNESS_FIELDS,
    MANIFEST_FIELDS,
    METRICS_FIELDS,
    PER_SECOND_FIELDS,
    SUMMARY_FIELDS,
)
from analysis.validate import validate_all


FLOW_SPECS = {
    "high": (200, 1.0),
    "medium": (500, 2.0),
    "low": (1000, 3.0),
    "background": (1400, 4.0),
}


def metric_row(run_id: int, flow: str) -> dict[str, str | int | float]:
    expected = 100
    payload = expected * (FLOW_SPECS[flow][0] - 28)
    return {
        "experiment_id": run_id,
        "run_id": run_id,
        "flow": flow,
        "expected_packets": expected,
        "unique_received": expected,
        "duplicates": 0,
        "out_of_order": 0,
        "lost_packets": 0,
        "packet_loss_pct": 0.0,
        "payload_bytes": payload,
        "duration_seconds": 1.0,
        "goodput_mbps": round(payload * 8 / 1_000_000, 6),
        "avg_latency_ms": 0.1,
        "p50_latency_ms": 0.1,
        "p95_latency_ms": 0.2,
    }


def per_second_row(run_id: int, flow: str) -> dict[str, str | int | float]:
    row = metric_row(run_id, flow)
    return {
        "experiment_id": run_id,
        "run_id": run_id,
        "flow": flow,
        "second": 0,
        "expected_packets": row["expected_packets"],
        "unique_received": row["unique_received"],
        "lost_packets": row["lost_packets"],
        "packet_loss_pct": row["packet_loss_pct"],
        "payload_bytes": row["payload_bytes"],
        "goodput_mbps": row["goodput_mbps"],
        "avg_latency_ms": row["avg_latency_ms"],
        "p95_latency_ms": row["p95_latency_ms"],
    }


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_layout(root: Path, *, drop_flow: bool = False) -> tuple[Path, Path]:
    results = root / "results"
    experiments = root / "experiments"
    results.mkdir(parents=True)

    next_run_id = 2101
    metrics: list[dict] = []
    fairness: list[dict] = []
    manifest: list[dict] = []
    per_second: list[dict] = []
    summary_rows: dict[Path, list[dict]] = {}

    for canonical, (folder, scenario) in CANONICAL_EXPERIMENTS.items():
        runs = [next_run_id, next_run_id + 1, next_run_id + 2]
        next_run_id += 3
        for run_id in runs:
            flows = list(EXPECTED_FLOWS[scenario])
            if drop_flow and canonical == "static" and run_id == runs[1]:
                flows = flows[:-1]
            for flow in flows:
                row = metric_row(run_id, flow)
                metrics.append(row)
                summary = {
                    "scenario": scenario,
                    "mode": "none" if canonical == "baseline" else "remote",
                    **row,
                }
                summary_rows.setdefault(experiments / folder, []).append(summary)
                if canonical == "dynamic":
                    per_second.append(per_second_row(run_id, flow))

            throughputs = {
                "high": 1.0,
                "medium": 1.0,
                "low": 1.0 if "low" in flows else 0.0,
                "background": 1.0 if "background" in flows else 0.0,
            }
            positive = [value for value in throughputs.values() if value > 0]
            jain = sum(positive) ** 2 / (len(positive) * sum(v * v for v in positive))
            fairness.append(
                {
                    "experiment_id": run_id,
                    "high_mbps": throughputs["high"],
                    "medium_mbps": throughputs["medium"],
                    "low_mbps": throughputs["low"],
                    "background_mbps": throughputs["background"],
                    "jains_fairness": round(jain, 6),
                }
            )
            manifest.append(
                {
                    "run_id": run_id,
                    "scenario": scenario,
                    "mode": "none" if canonical == "baseline" else "remote",
                    "duration_s": 30,
                }
            )

    write_csv(results / "metrics.csv", METRICS_FIELDS, metrics)
    write_csv(results / "fairness.csv", FAIRNESS_FIELDS, fairness)
    write_csv(results / "manifest.csv", MANIFEST_FIELDS, manifest)
    write_csv(results / "per_second.csv", PER_SECOND_FIELDS, per_second)
    for folder, rows in summary_rows.items():
        write_csv(folder / "summary.csv", SUMMARY_FIELDS, rows)

    return results, experiments


class ValidationTests(unittest.TestCase):
    def test_complete_final_layout_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            report = validate_all(results, experiments, strict=True, final=True)
            self.assertEqual(report.errors, [], report.render())
            self.assertEqual(report.warnings, [], report.render())

    def test_missing_flow_fails_final_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary), drop_flow=True)
            report = validate_all(results, experiments, final=True)
            self.assertNotEqual(report.errors, [])
            self.assertTrue(
                any(
                    issue.code in {"final_missing_flow", "missing_flow"}
                    for issue in report.errors
                ),
                report.render(),
            )

    def test_duplicate_row_is_a_strict_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            metrics_path = results / "metrics.csv"
            with metrics_path.open("a", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=METRICS_FIELDS)
                writer.writerow(metric_row(2101, "high"))

            report = validate_all(results, experiments, strict=False)
            self.assertTrue(
                any(issue.code == "duplicate_metric_row" for issue in report.warnings),
                report.render(),
            )
            strict_report = validate_all(results, experiments, strict=True)
            self.assertTrue(
                any(issue.code == "strict_duplicate_metric_row" for issue in strict_report.errors),
                strict_report.render(),
            )

    def test_non_consecutive_runs_fail_final_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results, experiments = build_layout(Path(temporary))
            summary_path = experiments / "baseline" / "summary.csv"
            with summary_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                if row["run_id"] == "2102":
                    row["run_id"] = "2200"
                    row["experiment_id"] = "2200"
            write_csv(summary_path, SUMMARY_FIELDS, rows)

            report = validate_all(results, experiments, final=True)
            self.assertTrue(
                any(issue.code == "final_non_consecutive_runs" for issue in report.errors),
                report.render(),
            )


if __name__ == "__main__":
    unittest.main()
