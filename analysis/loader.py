"""Read experiment summaries and raw result files without pandas.

The committed data lives in ``experiments/<label>/summary.csv``.  Per-second
rows and event/policy logs live in ``results/`` because that directory is
git-ignored.  All loaders return normalised dictionaries so the aggregation and
plot code does not depend on the exact CSV string representation.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from analysis.schema import (
    CANONICAL_EXPERIMENTS,
    FLOWS,
    METRICS_FIELDS,
    REPO_ROOT,
    canonical_name,
)
from analysis.selection import practice_groups, select_canonical_groups

METRIC_NUMERIC_FIELDS: tuple[str, ...] = (
    "run_id",
    "expected_packets",
    "unique_received",
    "duplicates",
    "out_of_order",
    "lost_packets",
    "packet_loss_pct",
    "payload_bytes",
    "received_bytes",
    "duration_seconds",
    "goodput_mbps",
    "throughput_mbps",
    "avg_latency_ms",
    "p50_latency_ms",
    "p95_latency_ms",
)

PER_SECOND_NUMERIC_FIELDS: tuple[str, ...] = (
    "run_id",
    "second",
    "bucket_seconds",
    "expected_packets",
    "unique_received",
    "lost_packets",
    "packet_loss_pct",
    "payload_bytes",
    "received_bytes",
    "goodput_mbps",
    "throughput_mbps",
    "avg_latency_ms",
    "p95_latency_ms",
)


@dataclass
class Dataset:
    summaries: list[dict[str, Any]] = field(default_factory=list)
    fairness: dict[int, dict[str, Any]] = field(default_factory=dict)
    manifest: dict[int, dict[str, Any]] = field(default_factory=dict)
    per_second: list[dict[str, Any]] = field(default_factory=list)
    duplicate_rows: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalise_metric_row(row: dict[str, str]) -> dict[str, Any]:
    normalised: dict[str, Any] = dict(row)
    for name in METRIC_NUMERIC_FIELDS:
        if name == "run_id":
            normalised["run_id"] = _int(row.get(name))
        else:
            normalised[name] = _float(row.get(name))
    normalised["flow"] = (row.get("flow") or "").strip()
    return normalised


def load_summary_rows(experiments_dir: Path) -> list[dict[str, Any]]:
    """Load every experiments/<label>/summary.csv into one normalised list."""

    rows: list[dict[str, Any]] = []
    if not experiments_dir.exists():
        return rows

    for path in sorted(experiments_dir.glob("*/summary.csv")):
        folder = path.parent.name
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for line_number, raw in enumerate(reader, start=2):
                if all(value in (None, "") for value in raw.values()):
                    continue
                row = _normalise_metric_row(raw)
                scenario = (raw.get("scenario") or "").strip()
                row.update(
                    {
                        "folder": folder,
                        "scenario": scenario,
                        "mode": (raw.get("mode") or "").strip(),
                        "canonical": canonical_name(folder, scenario),
                        "source": str(path),
                        "line": line_number,
                    }
                )
                rows.append(row)
    return rows


def deduplicate_rows(
    rows: Iterable[dict[str, Any]],
    key_fields: tuple[str, ...] = ("canonical", "run_id", "flow"),
) -> tuple[list[dict[str, Any]], list[str]]:
    """Keep the last occurrence of each key and report conflicting duplicates.

    The application only appends, so a rerun with the same run ID leaves both
    rows behind.  Validation flags that situation.  For analysis we keep the
    last row (the most recently written one) and list the conflict so it is not
    silently mixed into a median.
    """

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    conflicts: list[str] = []
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        previous = unique.get(key)
        if previous is not None:
            changed = [
                name
                for name in METRICS_FIELDS
                if str(previous.get(name, "")) != str(row.get(name, ""))
            ]
            if changed:
                conflicts.append(
                    f"{key}: conflicting duplicate columns {', '.join(changed)}; "
                    f"using line {row.get('source')}:{row.get('line')}"
                )
        unique[key] = row
    return list(unique.values()), conflicts


def group_runs(
    rows: Iterable[dict[str, Any]],
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    grouped: dict[str, dict[int, list[dict[str, Any]]]] = {}
    for row in rows:
        run_id = row.get("run_id")
        if run_id is None:
            continue
        grouped.setdefault(str(row["canonical"]), {}).setdefault(int(run_id), []).append(row)
    return grouped


def load_fairness(path: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not path.exists():
        return result
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            run_id = _int(raw.get("experiment_id"))
            if run_id is None:
                continue
            row: dict[str, Any] = dict(raw)
            row["run_id"] = run_id
            for flow in FLOWS:
                row[f"{flow}_mbps"] = _float(raw.get(f"{flow}_mbps")) or 0.0
            row["jains_fairness"] = _float(raw.get("jains_fairness"))
            result[run_id] = row
    return result


def load_manifest(path: Path) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not path.exists():
        return result
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            run_id = _int(raw.get("run_id"))
            if run_id is None:
                continue
            row: dict[str, Any] = dict(raw)
            row["run_id"] = run_id
            row["duration_s"] = _float(raw.get("duration_s"))
            result[run_id] = row
    return result


def load_per_second(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            row: dict[str, Any] = dict(raw)
            for name in PER_SECOND_NUMERIC_FIELDS:
                if name == "run_id":
                    row[name] = _int(raw.get(name))
                elif name == "second":
                    row[name] = _int(raw.get(name))
                else:
                    row[name] = _float(raw.get(name))
            row["flow"] = (raw.get("flow") or "").strip()
            if row["run_id"] is None or row["flow"] not in FLOWS:
                continue
            rows.append(row)
    rows.sort(key=lambda row: (row["run_id"], row["flow"], row["second"] or 0))
    return rows


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(newline="") as handle:
        for raw in csv.DictReader(handle):
            ts = _int(raw.get("ts_ns"))
            run_id = _int(raw.get("run_id"))
            if ts is None or run_id is None:
                continue
            rows.append(
                {
                    **raw,
                    "ts_ns": ts,
                    "run_id": run_id,
                    "event": (raw.get("event") or "").strip(),
                    "flow": (raw.get("flow") or "").strip(),
                    "detail": (raw.get("detail") or "").strip(),
                }
            )
    rows.sort(key=lambda row: row["ts_ns"])
    return rows


def find_events_file(
    experiments_dir: Path,
    results_dir: Path,
    canonical: str,
    run_id: int,
) -> Path | None:
    spec = CANONICAL_EXPERIMENTS.get(canonical)
    folder = spec[0] if spec else canonical.split("/", 1)[0]
    scenario = spec[1] if spec else canonical.split("/", 1)[1] if "/" in canonical else ""

    candidates = [
        experiments_dir / folder / f"events_{run_id}.csv",
        results_dir / f"{scenario}_{run_id}" / "events.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_policy_file(
    experiments_dir: Path,
    results_dir: Path,
    canonical: str,
    run_id: int,
    explicit: Path | None = None,
) -> Path | None:
    """Find the dynamic controller's policy log.

    The controller teammate owns this file.  The agreed schema is
    ``ts, flow_id, old_queue, new_queue, reason`` with a monotonic-ns timestamp.
    Several natural locations are accepted, and ``--policy-log`` can always
    point at a file explicitly.
    """

    if explicit is not None:
        return explicit if explicit.exists() else None

    spec = CANONICAL_EXPERIMENTS.get(canonical)
    folder = spec[0] if spec else canonical.split("/", 1)[0]
    scenario = spec[1] if spec else canonical.split("/", 1)[1] if "/" in canonical else ""

    candidates = [
        experiments_dir / folder / f"policy_{run_id}.csv",
        experiments_dir / folder / "policy.csv",
        results_dir / f"{scenario}_{run_id}" / "policy.csv",
        results_dir / f"policy_{run_id}.csv",
        results_dir / "policy.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_policy(path: Path, run_id: int | None = None) -> list[dict[str, Any]]:
    """Load a policy log, accepting ``ts`` or ``ts_ns`` as the time column."""

    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        header = set(reader.fieldnames or ())
        time_column = next(
            (name for name in ("ts_ns", "ts", "timestamp_ns", "timestamp") if name in header),
            None,
        )
        if time_column is None:
            return []
        for raw in reader:
            ts = _int(raw.get(time_column))
            if ts is None:
                continue
            row_run_id = _int(raw.get("run_id"))
            if run_id is not None and row_run_id is not None and row_run_id != run_id:
                continue
            flow_raw = (raw.get("flow_id") or raw.get("flow") or "").strip()
            flow = flow_raw
            if flow_raw.isdigit():
                flow = {str(index + 1): name for index, name in enumerate(FLOWS)}.get(flow_raw, flow_raw)
            rows.append(
                {
                    **raw,
                    "ts_ns": ts,
                    "run_id": row_run_id,
                    "flow_id": flow,
                    "old_queue": _int(raw.get("old_queue")),
                    "new_queue": _int(raw.get("new_queue")),
                    "reason": (raw.get("reason") or "").strip(),
                }
            )
    rows.sort(key=lambda row: row["ts_ns"])
    return rows


def load_dataset(
    results_dir: Path = REPO_ROOT / "results",
    experiments_dir: Path = REPO_ROOT / "experiments",
    *,
    policy_log: Path | None = None,
) -> Dataset:
    dataset = Dataset()
    dataset.summaries = load_summary_rows(experiments_dir)
    dataset.summaries, dataset.duplicate_rows = deduplicate_rows(dataset.summaries)
    dataset.fairness = load_fairness(results_dir / "fairness.csv")
    dataset.manifest = load_manifest(results_dir / "manifest.csv")
    dataset.per_second = load_per_second(results_dir / "per_second.csv")

    for name, path in (
        ("metrics.csv", results_dir / "metrics.csv"),
        ("fairness.csv", results_dir / "fairness.csv"),
        ("manifest.csv", results_dir / "manifest.csv"),
        ("per_second.csv", results_dir / "per_second.csv"),
    ):
        if not path.exists():
            dataset.missing_files.append(str(path))

    # Keep the parameter so callers can pass a single policy file.  The actual
    # per-run resolution happens in plots.load_surge_inputs().
    _ = policy_log
    return dataset


def canonical_groups(dataset: Dataset) -> dict[str, dict[int, list[dict[str, Any]]]]:
    return group_runs(dataset.summaries)


def report_groups(
    dataset: Dataset,
    *,
    practice: bool = False,
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    """Return rows for the report, with the latest three runs per pair.

    Practice groups keep all of their runs because they are diagnostic data,
    not part of the final report.
    """

    groups = canonical_groups(dataset)
    if practice:
        return practice_groups(groups)
    return select_canonical_groups(groups)


def available_canonical(dataset: Dataset) -> list[str]:
    groups = canonical_groups(dataset)
    return [name for name in CANONICAL_EXPERIMENTS if name in groups]


def available_practice(dataset: Dataset) -> list[str]:
    groups = canonical_groups(dataset)
    return sorted(name for name in groups if name not in CANONICAL_EXPERIMENTS)


__all__ = [
    "Dataset",
    "available_canonical",
    "available_practice",
    "canonical_groups",
    "deduplicate_rows",
    "find_events_file",
    "find_policy_file",
    "group_runs",
    "load_dataset",
    "load_events",
    "load_fairness",
    "load_manifest",
    "load_per_second",
    "load_policy",
    "load_summary_rows",
    "report_groups",
]
