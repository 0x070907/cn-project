"""Validate the measurement CSVs before they are aggregated or plotted.

The application appends rows to CSV files, so a failed or repeated run can
leave duplicate run IDs, missing flows, or reused IDs behind.  This module
checks the files that already exist and can optionally enforce the final
five-experiment / three-repeat layout.

Run it directly:

    python3 analysis/validate.py
    python3 analysis/validate.py --strict
    python3 analysis/validate.py --final --strict

Exit status is 1 when an error (or, with ``--strict``, a warning) is found.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Allow both "python3 analysis/validate.py" and "python3 -m analysis.validate".
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.loader import load_summary_rows  # noqa: E402
from analysis.schema import (  # noqa: E402
    CANONICAL_EXPERIMENTS,
    DATA_HEADER_BYTES,
    FAIRNESS_FIELDS,
    FLOWS,
    KNOWN_MANIFEST_MODES,
    KNOWN_MANIFEST_SCENARIOS,
    LEGACY_METRICS_FIELDS,
    LEGACY_PER_SECOND_FIELDS,
    LEGACY_SUMMARY_FIELDS,
    MANIFEST_FIELDS,
    METRICS_FIELDS,
    PER_SECOND_FIELDS,
    REPO_ROOT,
    SUMMARY_FIELDS,
    canonical_name,
    expected_flows,
)
from analysis.selection import (  # noqa: E402
    REPORTABLE_FOLDERS,
    REPORTABLE_PAIRS,
    REPORT_RUN_COUNT,
    consecutive_run_ids,
    is_reportable_pair,
    latest_run_ids,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass
class Issue:
    severity: str
    code: str
    path: Path | None
    row: int | None
    message: str

    def render(self) -> str:
        location = ""
        if self.path is not None:
            location = str(self.path)
            if self.row is not None:
                location += f":{self.row}"
            location += ":"
        return f"[{self.severity.upper()}] {self.code}: {location} {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    files_checked: list[Path] = field(default_factory=list)
    rows_checked: dict[str, int] = field(default_factory=dict)

    def add(
        self,
        severity: str,
        code: str,
        path: Path | None,
        row: int | None,
        message: str,
    ) -> None:
        self.issues.append(Issue(severity, code, path, row, message))

    def error(self, code: str, path: Path | None, row: int | None, message: str) -> None:
        self.add(SEVERITY_ERROR, code, path, row, message)

    def warning(self, code: str, path: Path | None, row: int | None, message: str) -> None:
        self.add(SEVERITY_WARNING, code, path, row, message)

    def count(self, severity: str) -> int:
        return sum(1 for issue in self.issues if issue.severity == severity)

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == SEVERITY_WARNING]

    def render(self) -> str:
        lines: list[str] = []
        lines.append("Data validation report")
        lines.append("======================")
        lines.append(f"Files checked: {len(self.files_checked)}")
        for name, count in sorted(self.rows_checked.items()):
            lines.append(f"  {name}: {count} row(s)")
        lines.append(f"Errors: {self.count(SEVERITY_ERROR)}")
        lines.append(f"Warnings: {self.count(SEVERITY_WARNING)}")
        lines.append("")
        if not self.issues:
            lines.append("No problems found.")
            return "\n".join(lines)

        for issue in self.issues:
            lines.append(issue.render())
        return "\n".join(lines)


def _integer_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _selected_final_runs(experiments_dir: Path) -> dict[str, tuple[int, ...]]:
    """Scan summaries for the latest three IDs of each reportable pair."""

    grouped: dict[str, set[int]] = {}
    for row in load_summary_rows(experiments_dir):
        folder = str(row.get("folder") or "")
        scenario = str(row.get("scenario") or "")
        if not is_reportable_pair(folder, scenario):
            continue
        run_id = row.get("run_id")
        if run_id is None:
            continue
        grouped.setdefault(str(row["canonical"]), set()).add(int(run_id))

    return {
        name: latest_run_ids(grouped.get(name, ()))
        for name in CANONICAL_EXPERIMENTS
    }


def _read_csv(
    report: Report,
    path: Path,
    required_fields: tuple[str, ...],
    label: str,
    *,
    allowed_fields: tuple[str, ...] | None = None,
) -> list[tuple[int, dict[str, str]]] | None:
    """Read a CSV, check its header, and return (line number, row) pairs.

    ``required_fields`` is the minimum schema (legacy files can omit the
    throughput columns during non-final validation).  ``allowed_fields`` is the
    full current schema, so legitimate new columns are not reported as extra.
    """

    if not path.exists():
        report.error("missing_file", path, None, f"{label} file does not exist")
        return None
    if path.stat().st_size == 0:
        report.error("empty_file", path, None, f"{label} file is empty")
        return None

    report.files_checked.append(path)

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        missing = [name for name in required_fields if name not in header]
        if missing:
            report.error(
                "missing_columns",
                path,
                None,
                f"missing required column(s): {', '.join(missing)}",
            )
            return None

        allowed = allowed_fields or required_fields
        unexpected = [name for name in header if name not in allowed]
        if unexpected:
            report.warning(
                "extra_columns",
                path,
                None,
                f"unexpected column(s): {', '.join(unexpected)}",
            )

        present_required = tuple(name for name in header if name in required_fields)
        if present_required != required_fields:
            report.warning(
                "column_order",
                path,
                None,
                "columns are present but not in the expected order",
            )

        rows: list[tuple[int, dict[str, str]]] = []
        for line_number, row in enumerate(reader, start=2):
            if all(value in (None, "") for value in row.values()):
                continue
            rows.append((line_number, row))

    report.rows_checked[label] = report.rows_checked.get(label, 0) + len(rows)
    return rows


def _parse_int(
    report: Report,
    path: Path,
    line: int,
    row: dict[str, str],
    name: str,
    *,
    minimum: int | None = 0,
) -> int | None:
    raw = row.get(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        report.error("not_an_integer", path, line, f"{name}={raw!r} is not an integer")
        return None
    if minimum is not None and value < minimum:
        report.error(
            "negative_value",
            path,
            line,
            f"{name}={value} is below the minimum {minimum}",
        )
    return value


def _parse_float(
    report: Report,
    path: Path,
    line: int,
    row: dict[str, str],
    name: str,
    *,
    required: bool = True,
    minimum: float | None = 0.0,
) -> float | None:
    raw = row.get(name, "")
    if raw in ("", None):
        if required:
            report.error("missing_value", path, line, f"{name} is empty")
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        report.error("not_a_number", path, line, f"{name}={raw!r} is not a number")
        return None
    if not math.isfinite(value):
        report.error("not_finite", path, line, f"{name}={raw!r} is not finite")
        return None
    if minimum is not None and value < minimum:
        report.error(
            "negative_value",
            path,
            line,
            f"{name}={value} is below the minimum {minimum}",
        )
    return value


def _check_metric_row(
    report: Report,
    path: Path,
    line: int,
    row: dict[str, str],
    *,
    check_experiment_id: bool = True,
    require_throughput: bool = False,
) -> tuple[int | None, str | None] | None:
    """Validate one metrics-shaped row (metrics.csv or summary.csv)."""

    run_id = _parse_int(report, path, line, row, "run_id")
    flow = (row.get("flow") or "").strip()
    if flow not in FLOWS:
        report.error("unknown_flow", path, line, f"flow={flow!r} is not a known flow")
    elif flow == "":
        flow = None

    if check_experiment_id:
        experiment_id = (row.get("experiment_id") or "").strip()
        if not experiment_id:
            report.error("missing_value", path, line, "experiment_id is empty")
        elif run_id is not None and experiment_id != str(run_id):
            report.warning(
                "experiment_id_mismatch",
                path,
                line,
                f"experiment_id={experiment_id!r} does not mirror run_id={run_id}",
            )

    expected = _parse_int(report, path, line, row, "expected_packets")
    unique = _parse_int(report, path, line, row, "unique_received")
    duplicates = _parse_int(report, path, line, row, "duplicates")
    out_of_order = _parse_int(report, path, line, row, "out_of_order")
    lost = _parse_int(report, path, line, row, "lost_packets")
    payload = _parse_int(report, path, line, row, "payload_bytes")

    received_raw = row.get("received_bytes", "")
    if received_raw in ("", None):
        received = None
        if require_throughput:
            report.error("missing_value", path, line, "received_bytes is empty")
    else:
        received = _parse_int(report, path, line, row, "received_bytes")

    loss_pct = _parse_float(report, path, line, row, "packet_loss_pct")
    duration = _parse_float(report, path, line, row, "duration_seconds")
    goodput = _parse_float(report, path, line, row, "goodput_mbps")
    throughput = _parse_float(
        report,
        path,
        line,
        row,
        "throughput_mbps",
        required=require_throughput,
    )
    avg_latency = _parse_float(
        report, path, line, row, "avg_latency_ms", required=False
    )
    p50 = _parse_float(report, path, line, row, "p50_latency_ms", required=False)
    p95 = _parse_float(report, path, line, row, "p95_latency_ms", required=False)

    if expected is not None and unique is not None and unique > expected:
        report.error(
            "unique_exceeds_expected",
            path,
            line,
            f"unique_received={unique} exceeds expected_packets={expected}",
        )

    if expected is not None and unique is not None and lost is not None:
        wanted_lost = max(0, expected - unique)
        if lost != wanted_lost:
            report.error(
                "lost_packets_mismatch",
                path,
                line,
                f"lost_packets={lost} but expected-unique={wanted_lost}",
            )

    if expected is not None and lost is not None and loss_pct is not None:
        wanted_pct = lost / expected * 100 if expected > 0 else 0.0
        if abs(loss_pct - wanted_pct) > 0.01:
            report.error(
                "loss_pct_mismatch",
                path,
                line,
                f"packet_loss_pct={loss_pct} but lost/expected={wanted_pct:.4f}",
            )

    if payload is not None and duration is not None and goodput is not None:
        wanted_goodput = payload * 8 / duration / 1_000_000 if duration > 0 else 0.0
        # duration_seconds is rounded to 6 decimals before goodput is derived,
        # so allow a 0.01 Mbps rounding margin instead of exact equality.
        if abs(goodput - wanted_goodput) > 0.01:
            report.error(
                "goodput_mismatch",
                path,
                line,
                f"goodput_mbps={goodput} but payload*8/duration={wanted_goodput:.6f}",
            )

    if received is not None and payload is not None and unique is not None:
        wanted_received = payload + unique * DATA_HEADER_BYTES
        if received != wanted_received:
            report.error(
                "received_bytes_mismatch",
                path,
                line,
                f"received_bytes={received} but payload_bytes + "
                f"{DATA_HEADER_BYTES}*unique_received={wanted_received}",
            )

    if received is not None and duration is not None and throughput is not None:
        wanted_throughput = (
            received * 8 / duration / 1_000_000 if duration > 0 else 0.0
        )
        if abs(throughput - wanted_throughput) > 0.01:
            report.error(
                "throughput_mismatch",
                path,
                line,
                f"throughput_mbps={throughput} but "
                f"received*8/duration={wanted_throughput:.6f}",
            )

    if unique is not None and unique > 0:
        if avg_latency is None:
            report.warning(
                "missing_latency",
                path,
                line,
                "unique_received > 0 but avg_latency_ms is empty",
            )
        if p50 is not None and p95 is not None and p50 > p95:
            report.warning(
                "percentile_order",
                path,
                line,
                f"p50_latency_ms={p50} is greater than p95_latency_ms={p95}",
            )

    if unique == 0 and any(value is not None and value != 0 for value in (avg_latency, p50, p95)):
        report.warning(
            "latency_without_packets",
            path,
            line,
            "latency values are present even though no packet was received",
        )

    if duplicates is not None and duplicates < 0:
        report.error("negative_value", path, line, "duplicates is negative")
    if out_of_order is not None and out_of_order < 0:
        report.error("negative_value", path, line, "out_of_order is negative")

    if run_id is None or flow not in FLOWS:
        return None
    return run_id, flow


def validate_metrics(
    report: Report,
    path: Path,
    *,
    run_ids: set[int] | None = None,
    require_throughput: bool = False,
) -> dict[tuple[int, str], list[int]]:
    """Validate results/metrics.csv and return row locations by (run, flow)."""

    required = METRICS_FIELDS if require_throughput else LEGACY_METRICS_FIELDS
    rows = _read_csv(
        report,
        path,
        required,
        "metrics.csv",
        allowed_fields=METRICS_FIELDS,
    )
    locations: dict[tuple[int, str], list[int]] = {}
    if rows is None:
        return locations

    for line, row in rows:
        if run_ids is not None and _integer_or_none(row.get("run_id")) not in run_ids:
            continue
        key = _check_metric_row(
            report,
            path,
            line,
            row,
            require_throughput=require_throughput,
        )
        if key is None:
            continue
        locations.setdefault(key, []).append(line)

    for (run_id, flow), lines in sorted(locations.items()):
        if len(lines) > 1:
            report.warning(
                "duplicate_metric_row",
                path,
                lines[0],
                f"run_id={run_id} flow={flow} appears {len(lines)} times "
                f"(lines {', '.join(str(line) for line in lines)}); "
                "the analysis keeps the last occurrence",
            )

    return locations


def validate_fairness(
    report: Report,
    path: Path,
    *,
    run_ids: set[int] | None = None,
) -> dict[int, list[int]]:
    rows = _read_csv(report, path, FAIRNESS_FIELDS, "fairness.csv")
    locations: dict[int, list[int]] = {}
    if rows is None:
        return locations

    for line, row in rows:
        if run_ids is not None and _integer_or_none(row.get("experiment_id")) not in run_ids:
            continue
        experiment_id = (row.get("experiment_id") or "").strip()
        if not experiment_id:
            report.error("missing_value", path, line, "experiment_id is empty")
        try:
            run_id = int(experiment_id)
        except ValueError:
            report.warning(
                "non_integer_experiment_id",
                path,
                line,
                f"experiment_id={experiment_id!r} is not an integer run id",
            )
            run_id = None

        throughputs: list[float] = []
        for flow in FLOWS:
            value = _parse_float(report, path, line, row, f"{flow}_mbps")
            if value is not None:
                throughputs.append(value)

        jain = _parse_float(report, path, line, row, "jains_fairness", minimum=0.0)
        if jain is not None and jain > 1.0 + 1e-9:
            report.error(
                "fairness_out_of_range",
                path,
                line,
                f"jains_fairness={jain} is greater than 1",
            )

        # fairness.csv always has four columns.  For no_congestion only two
        # flows ran, so the receiver computed the index over the nonzero
        # entries; for a congestion run with a genuinely zero-throughput flow
        # it computed over all four.  Accept either interpretation.
        candidates: list[float] = []
        positive = [value for value in throughputs if value > 0]
        if positive:
            candidates.append(
                sum(positive) ** 2
                / (len(positive) * sum(value * value for value in positive))
            )
        if throughputs and sum(throughputs) > 0:
            candidates.append(
                sum(throughputs) ** 2
                / (len(throughputs) * sum(value * value for value in throughputs))
            )
        if jain is not None and candidates:
            if min(abs(jain - wanted) for wanted in candidates) > 1e-4:
                report.warning(
                    "fairness_mismatch",
                    path,
                    line,
                    f"jains_fairness={jain} but recomputed value(s) are "
                    + ", ".join(f"{wanted:.6f}" for wanted in candidates),
                )

        if run_id is not None:
            locations.setdefault(run_id, []).append(line)

    for run_id, lines in sorted(locations.items()):
        if len(lines) > 1:
            report.warning(
                "duplicate_fairness_row",
                path,
                lines[0],
                f"fairness for run_id={run_id} appears {len(lines)} times",
            )

    return locations


def validate_manifest(
    report: Report,
    path: Path,
    *,
    run_ids: set[int] | None = None,
) -> dict[int, list[int]]:
    rows = _read_csv(report, path, MANIFEST_FIELDS, "manifest.csv")
    locations: dict[int, list[int]] = {}
    if rows is None:
        return locations

    for line, row in rows:
        if run_ids is not None and _integer_or_none(row.get("run_id")) not in run_ids:
            continue
        run_id = _parse_int(report, path, line, row, "run_id")
        scenario = (row.get("scenario") or "").strip()
        mode = (row.get("mode") or "").strip()
        duration = _parse_float(report, path, line, row, "duration_s")

        if scenario not in KNOWN_MANIFEST_SCENARIOS:
            report.error(
                "unknown_scenario",
                path,
                line,
                f"scenario={scenario!r} is not one of {sorted(KNOWN_MANIFEST_SCENARIOS)}",
            )
        if mode not in KNOWN_MANIFEST_MODES:
            report.error(
                "unknown_mode",
                path,
                line,
                f"mode={mode!r} is not one of {sorted(KNOWN_MANIFEST_MODES)}",
            )
        if duration is not None and duration <= 0:
            report.error("invalid_duration", path, line, f"duration_s={duration} must be > 0")
        if run_id is not None:
            locations.setdefault(run_id, []).append(line)

    for run_id, lines in sorted(locations.items()):
        if len(lines) > 1:
            report.warning(
                "duplicate_manifest_row",
                path,
                lines[0],
                f"run_id={run_id} appears {len(lines)} times in the manifest",
            )

    return locations


def validate_summary(
    report: Report,
    path: Path,
    folder: str,
    metrics_rows: dict[tuple[int, str], list[int]],
    *,
    run_ids: set[int] | None = None,
    pairs: frozenset[tuple[str, str]] | None = None,
    require_throughput: bool = False,
) -> list[dict[str, str]]:
    """Validate one experiments/<label>/summary.csv and return its rows."""

    required = SUMMARY_FIELDS if require_throughput else LEGACY_SUMMARY_FIELDS
    rows = _read_csv(
        report,
        path,
        required,
        f"summary:{folder}",
        allowed_fields=SUMMARY_FIELDS,
    )
    if rows is None:
        return []

    normalised: list[dict[str, str]] = []
    seen: dict[tuple[int, str], list[int]] = {}
    run_scenarios: dict[int, set[str]] = {}

    for line, row in rows:
        scenario = (row.get("scenario") or "").strip()
        mode = (row.get("mode") or "").strip()
        if pairs is not None and (folder, scenario) not in pairs:
            continue
        if run_ids is not None and _integer_or_none(row.get("run_id")) not in run_ids:
            continue
        if scenario not in KNOWN_MANIFEST_SCENARIOS:
            report.error("unknown_scenario", path, line, f"scenario={scenario!r}")
        if mode not in KNOWN_MANIFEST_MODES:
            report.error("unknown_mode", path, line, f"mode={mode!r}")

        key = _check_metric_row(
            report,
            path,
            line,
            row,
            check_experiment_id=True,
            require_throughput=require_throughput,
        )
        if key is not None:
            seen.setdefault(key, []).append(line)
            run_scenarios.setdefault(key[0], set()).add(scenario)

            metrics_lines = metrics_rows.get(key)
            if not metrics_lines:
                report.warning(
                    "summary_without_metrics",
                    path,
                    line,
                    f"run_id={key[0]} flow={key[1]} is not present in results/metrics.csv",
                )

        normalised.append({**row, "folder": folder, "canonical": canonical_name(folder, scenario)})

    for (run_id, flow), lines in sorted(seen.items()):
        if len(lines) > 1:
            report.warning(
                "duplicate_summary_row",
                path,
                lines[0],
                f"run_id={run_id} flow={flow} appears {len(lines)} times "
                f"(lines {', '.join(str(line) for line in lines)})",
            )

    for run_id, scenarios in sorted(run_scenarios.items()):
        if len(scenarios) > 1:
            report.error(
                "run_id_cross_scenario",
                path,
                None,
                f"run_id={run_id} is used for more than one scenario: {sorted(scenarios)}",
            )

    # Check that each run has exactly the flows its scenario promises.
    run_rows: dict[int, list[dict[str, str]]] = {}
    for entry in normalised:
        try:
            run_id = int(entry.get("run_id", ""))
        except (TypeError, ValueError):
            continue
        run_rows.setdefault(run_id, []).append(entry)

    for run_id, entries in sorted(run_rows.items()):
        scenario = entries[0].get("scenario", "")
        wanted = expected_flows(scenario)
        present = {entry.get("flow", "") for entry in entries}
        missing = [flow for flow in wanted if flow not in present]
        extra = [flow for flow in present if flow not in wanted]
        if missing:
            report.warning(
                "missing_flow",
                path,
                None,
                f"run_id={run_id} ({scenario}) has no row for flow(s): {', '.join(missing)}",
            )
        if extra:
            report.error(
                "unexpected_flow",
                path,
                None,
                f"run_id={run_id} ({scenario}) has unexpected flow(s): {', '.join(extra)}",
            )

    # Summary must agree with the manifest, when the manifest knows the run.
    return normalised


def validate_per_second(
    report: Report,
    path: Path,
    *,
    run_ids: set[int] | None = None,
    require_throughput: bool = False,
) -> dict[tuple[int, str], list[int]]:
    required = (
        PER_SECOND_FIELDS if require_throughput else LEGACY_PER_SECOND_FIELDS
    )
    rows = _read_csv(
        report,
        path,
        required,
        "per_second.csv",
        allowed_fields=PER_SECOND_FIELDS,
    )
    locations: dict[tuple[int, str], list[int]] = {}
    if rows is None:
        return locations

    seen_seconds: dict[tuple[int, str], set[int]] = {}
    for line, row in rows:
        if run_ids is not None and _integer_or_none(row.get("run_id")) not in run_ids:
            continue
        run_id = _parse_int(report, path, line, row, "run_id")
        flow = (row.get("flow") or "").strip()
        if flow not in FLOWS:
            report.error("unknown_flow", path, line, f"flow={flow!r} is not a known flow")
            continue

        second = _parse_int(report, path, line, row, "second")
        bucket_seconds = _parse_float(report, path, line, row, "bucket_seconds")
        if bucket_seconds is not None and bucket_seconds <= 0:
            report.error(
                "invalid_bucket_seconds",
                path,
                line,
                f"bucket_seconds={bucket_seconds} must be > 0",
            )
        expected = _parse_int(report, path, line, row, "expected_packets")
        unique = _parse_int(report, path, line, row, "unique_received")
        lost = _parse_int(report, path, line, row, "lost_packets")
        loss_pct = _parse_float(report, path, line, row, "packet_loss_pct")
        payload = _parse_int(report, path, line, row, "payload_bytes")
        received_raw = row.get("received_bytes", "")
        if received_raw in ("", None):
            received = None
            if require_throughput:
                report.error("missing_value", path, line, "received_bytes is empty")
        else:
            received = _parse_int(report, path, line, row, "received_bytes")
        goodput = _parse_float(report, path, line, row, "goodput_mbps")
        throughput = _parse_float(
            report,
            path,
            line,
            row,
            "throughput_mbps",
            required=require_throughput,
        )
        avg_latency = _parse_float(
            report, path, line, row, "avg_latency_ms", required=False
        )
        p95 = _parse_float(report, path, line, row, "p95_latency_ms", required=False)

        if run_id is None:
            continue
        locations.setdefault((run_id, flow), []).append(line)

        if second is not None:
            seconds = seen_seconds.setdefault((run_id, flow), set())
            if second in seconds:
                report.error(
                    "duplicate_second",
                    path,
                    line,
                    f"run_id={run_id} flow={flow} second={second} appears more than once",
                )
            seconds.add(second)

        if expected is not None and unique is not None and unique > expected:
            # The per-second expected count is an even-spread estimate, so a
            # burst can legitimately arrive in one second.  Only the per-run
            # metrics.csv row is guaranteed to be an exact count.
            report.warning(
                "second_unique_above_estimate",
                path,
                line,
                f"unique_received={unique} exceeds the estimated expected_packets={expected}",
            )
        if expected is not None and unique is not None and lost is not None:
            wanted_lost = max(0, expected - unique)
            if lost != wanted_lost:
                report.error(
                    "lost_packets_mismatch",
                    path,
                    line,
                    f"lost_packets={lost} but expected-unique={wanted_lost}",
                )
        if expected is not None and lost is not None and loss_pct is not None:
            wanted_pct = lost / expected * 100 if expected > 0 else 0.0
            if abs(loss_pct - wanted_pct) > 0.01:
                report.error(
                    "loss_pct_mismatch",
                    path,
                    line,
                    f"packet_loss_pct={loss_pct} but lost/expected={wanted_pct:.4f}",
                )
        if (
            payload is not None
            and goodput is not None
            and bucket_seconds is not None
            and bucket_seconds > 0
        ):
            wanted_goodput = payload * 8 / bucket_seconds / 1_000_000
            if abs(goodput - wanted_goodput) > 0.001:
                report.error(
                    "goodput_mismatch",
                    path,
                    line,
                    f"goodput_mbps={goodput} but "
                    f"payload*8/bucket_seconds={wanted_goodput:.6f}",
                )
        if received is not None and payload is not None and unique is not None:
            wanted_received = payload + unique * DATA_HEADER_BYTES
            if received != wanted_received:
                report.error(
                    "received_bytes_mismatch",
                    path,
                    line,
                    f"received_bytes={received} but payload_bytes + "
                    f"{DATA_HEADER_BYTES}*unique_received={wanted_received}",
                )
        if (
            received is not None
            and throughput is not None
            and bucket_seconds is not None
            and bucket_seconds > 0
        ):
            wanted_throughput = received * 8 / bucket_seconds / 1_000_000
            if abs(throughput - wanted_throughput) > 0.001:
                report.error(
                    "throughput_mismatch",
                    path,
                    line,
                    f"throughput_mbps={throughput} but "
                    f"received*8/bucket_seconds={wanted_throughput:.6f}",
                )
        if unique == 0 and any(value is not None for value in (avg_latency, p95)):
            report.warning(
                "latency_without_packets",
                path,
                line,
                "latency values are present even though no packet was received",
            )

    return locations


def validate_cross_file(
    report: Report,
    metrics_path: Path,
    manifest_path: Path,
    metrics_rows: dict[tuple[int, str], list[int]],
    manifest_rows: dict[int, list[int]],
    summaries: list[dict[str, str]],
    fairness_path: Path,
    fairness_rows: dict[int, list[int]],
) -> None:
    metrics_runs = {run_id for run_id, _flow in metrics_rows}
    manifest_runs = set(manifest_rows)

    for run_id in sorted(metrics_runs - manifest_runs):
        report.warning(
            "metrics_run_without_manifest",
            metrics_path,
            None,
            f"run_id={run_id} has metrics rows but no manifest row",
        )
    for run_id in sorted(manifest_runs - metrics_runs):
        report.warning(
            "manifest_run_without_metrics",
            manifest_path,
            None,
            f"run_id={run_id} is in the manifest but has no metrics rows",
        )

    for entry in summaries:
        try:
            run_id = int(entry.get("run_id", ""))
        except (TypeError, ValueError):
            continue
        if run_id not in manifest_runs:
            report.warning(
                "summary_run_without_manifest",
                Path(str(entry.get("source", "summary.csv"))),
                None,
                f"run_id={run_id} is in a summary but not in the manifest",
            )
            continue
    for run_id in sorted(metrics_runs - set(fairness_rows)):
        report.warning(
            "run_without_fairness",
            fairness_path,
            None,
            f"run_id={run_id} has metrics but no fairness row",
        )

    for run_id in sorted(set(fairness_rows) - metrics_runs):
        report.warning(
            "fairness_without_metrics",
            fairness_path,
            None,
            f"run_id={run_id} has a fairness row but no metrics rows",
        )


def validate_final_layout(
    report: Report,
    summaries: list[dict[str, str]],
    selected_runs: dict[str, tuple[int, ...]],
    fairness_rows: dict[int, list[int]],
    per_second_runs: set[int],
    per_second_present: bool,
) -> None:
    """Check the latest three selected runs for the five final experiments."""

    by_canonical: dict[str, list[dict[str, str]]] = {}
    for entry in summaries:
        by_canonical.setdefault(entry["canonical"], []).append(entry)

    all_selected: dict[int, set[str]] = {}
    for name in CANONICAL_EXPERIMENTS:
        selected = tuple(selected_runs.get(name, ()))
        if not selected:
            report.error(
                "final_missing_experiment",
                None,
                None,
                f"no summary rows found for {name} "
                f"(expected folder {CANONICAL_EXPERIMENTS[name][0]!r}, "
                f"scenario {CANONICAL_EXPERIMENTS[name][1]!r})",
            )
            continue

        if len(selected) < REPORT_RUN_COUNT:
            report.error(
                "final_too_few_runs",
                None,
                None,
                f"{name} has {len(selected)} selected run(s); "
                f"exactly {REPORT_RUN_COUNT} latest runs are required",
            )
        elif not consecutive_run_ids(selected):
            report.error(
                "final_non_consecutive_runs",
                None,
                None,
                f"{name} latest run IDs {list(selected)} are not consecutive",
            )

        runs: dict[int, list[dict[str, str]]] = {}
        for entry in by_canonical.get(name, []):
            run_id = _integer_or_none(entry.get("run_id"))
            if run_id in selected:
                runs.setdefault(run_id, []).append(entry)
        for run_id in selected:
            all_selected.setdefault(run_id, set()).add(name)

        scenario = CANONICAL_EXPERIMENTS[name][1]
        wanted = expected_flows(scenario)
        for run_id in selected:
            run_entries = runs.get(run_id, [])
            if not run_entries:
                report.error(
                    "final_missing_run",
                    None,
                    None,
                    f"{name} selected run_id={run_id} has no summary rows",
                )
                continue

            flows = [entry.get("flow", "") for entry in run_entries]
            missing = [flow for flow in wanted if flow not in flows]
            extra = [flow for flow in flows if flow not in wanted]
            duplicate_flows = sorted({flow for flow in flows if flows.count(flow) > 1})
            if missing:
                report.error(
                    "final_missing_flow",
                    None,
                    None,
                    f"{name} run_id={run_id} is missing flow(s): {', '.join(missing)}",
                )
            if extra:
                report.error(
                    "final_unexpected_flow",
                    None,
                    None,
                    f"{name} run_id={run_id} has unexpected flow(s): {', '.join(extra)}",
                )
            if duplicate_flows:
                report.error(
                    "final_duplicate_flow",
                    None,
                    None,
                    f"{name} run_id={run_id} repeats flow(s): {', '.join(duplicate_flows)}",
                )
            if run_id not in fairness_rows:
                report.error(
                    "final_missing_fairness",
                    None,
                    None,
                    f"{name} run_id={run_id} has no fairness.csv row",
                )

    for run_id, names in sorted(all_selected.items()):
        if len(names) > 1:
            report.error(
                "final_reused_run_id",
                None,
                None,
                f"run_id={run_id} is used by more than one experiment: {sorted(names)}",
            )

    dynamic_runs = tuple(selected_runs.get("dynamic", ()))
    if dynamic_runs:
        if not per_second_present:
            report.error(
                "final_missing_per_second",
                None,
                None,
                "dynamic experiment exists but results/per_second.csv is missing; "
                "the surge timeline cannot be drawn",
            )
        else:
            for run_id in dynamic_runs:
                if run_id not in per_second_runs:
                    report.error(
                        "final_dynamic_run_without_per_second",
                        None,
                        None,
                        f"dynamic run_id={run_id} has no rows in results/per_second.csv",
                    )


def validate_all(
    results_dir: Path,
    experiments_dir: Path,
    *,
    strict: bool = False,
    final: bool = False,
) -> Report:
    report = Report()

    metrics_path = results_dir / "metrics.csv"
    fairness_path = results_dir / "fairness.csv"
    manifest_path = results_dir / "manifest.csv"
    per_second_path = results_dir / "per_second.csv"

    selected_runs: dict[str, tuple[int, ...]] = {}
    allowed_run_ids: set[int] | None = None
    if final:
        selected_runs = _selected_final_runs(experiments_dir)
        allowed_run_ids = {
            run_id
            for run_ids in selected_runs.values()
            for run_id in run_ids
        }

    # With no selected final runs there is nothing whose throughput columns can
    # be checked.  Keep legacy practice files out of the final error list; the
    # missing-experiment errors are the actionable problem in that situation.
    require_throughput = final and bool(allowed_run_ids)

    metrics_rows = validate_metrics(
        report,
        metrics_path,
        run_ids=allowed_run_ids,
        require_throughput=require_throughput,
    )
    fairness_rows = validate_fairness(report, fairness_path, run_ids=allowed_run_ids)
    manifest_rows = validate_manifest(report, manifest_path, run_ids=allowed_run_ids)

    if per_second_path.exists():
        per_second_locations = validate_per_second(
            report,
            per_second_path,
            run_ids=allowed_run_ids,
            require_throughput=require_throughput,
        )
        per_second_runs = {run_id for run_id, _flow in per_second_locations}
        per_second_present = True
    else:
        per_second_runs = set()
        per_second_present = False
        if not final:
            report.warning(
                "missing_per_second",
                per_second_path,
                None,
                "per-second metrics are not available; run the receiver after "
                "upgrading app/metrics.py, or the surge timeline will be skipped",
            )

    summaries: list[dict[str, str]] = []
    summary_paths = sorted(experiments_dir.glob("*/summary.csv")) if experiments_dir.exists() else []
    if final:
        summary_paths = [
            path for path in summary_paths
            if path.parent.name in REPORTABLE_FOLDERS
        ]
    if not summary_paths and not final:
        report.error(
            "missing_summary",
            experiments_dir,
            None,
            "no experiments/*/summary.csv files found",
        )

    for summary_path in summary_paths:
        folder = summary_path.parent.name
        entries = validate_summary(
            report,
            summary_path,
            folder,
            metrics_rows,
            run_ids=allowed_run_ids,
            pairs=REPORTABLE_PAIRS if final else None,
            require_throughput=require_throughput,
        )
        for entry in entries:
            entry["source"] = str(summary_path)
        summaries.extend(entries)

    # Compare summary scenario/mode with the manifest.
    manifest_values: dict[int, dict[str, str]] = {}
    if manifest_path.exists():
        with manifest_path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    manifest_values[int(row["run_id"])] = row
                except (KeyError, TypeError, ValueError):
                    continue
    for entry in summaries:
        try:
            run_id = int(entry.get("run_id", ""))
        except (TypeError, ValueError):
            continue
        manifest_row = manifest_values.get(run_id)
        if manifest_row is None:
            continue
        if entry.get("scenario") != manifest_row.get("scenario"):
            report.error(
                "summary_scenario_mismatch",
                Path(str(entry.get("source", "summary.csv"))),
                None,
                f"run_id={run_id} summary scenario={entry.get('scenario')!r} "
                f"but manifest says {manifest_row.get('scenario')!r}",
            )
        if entry.get("mode") != manifest_row.get("mode"):
            report.error(
                "summary_mode_mismatch",
                Path(str(entry.get("source", "summary.csv"))),
                None,
                f"run_id={run_id} summary mode={entry.get('mode')!r} "
                f"but manifest says {manifest_row.get('mode')!r}",
            )

    validate_cross_file(
        report,
        metrics_path,
        manifest_path,
        metrics_rows,
        manifest_rows,
        summaries,
        fairness_path,
        fairness_rows,
    )

    if final:
        validate_final_layout(
            report,
            summaries,
            selected_runs,
            fairness_rows,
            per_second_runs,
            per_second_present,
        )

    if strict:
        for warning in report.warnings:
            if warning.code == "second_unique_above_estimate":
                continue
            report.issues.append(
                Issue(
                    SEVERITY_ERROR,
                    f"strict_{warning.code}",
                    warning.path,
                    warning.row,
                    warning.message,
                )
            )

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="validate the QoS measurement CSVs")
    parser.add_argument(
        "--results",
        type=Path,
        default=REPO_ROOT / "results",
        help="directory containing metrics.csv, fairness.csv and manifest.csv",
    )
    parser.add_argument(
        "--experiments",
        type=Path,
        default=REPO_ROOT / "experiments",
        help="directory containing <label>/summary.csv folders",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "analysis" / "output" / "validation_report.txt",
        help="where to write the text report",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="print the report only; do not write it to --output",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors (use this before the final submission)",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help=(
            "require the five reportable pairs and check the latest three "
            "time-generated run IDs per pair"
        ),
    )
    args = parser.parse_args()

    report = validate_all(
        args.results,
        args.experiments,
        strict=args.strict,
        final=args.final,
    )
    text = report.render()
    print(text)

    if not args.no_write:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
        print(f"\nreport written to {args.output}")

    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
