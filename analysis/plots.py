"""Matplotlib figures for the QoS comparison and the dynamic surge timeline.

The module uses the Agg backend so it works over SSH and in CI without a
display.  Missing experiments or logs are reported to the caller instead of
being drawn as zero, so a partially finished dataset cannot look like a real
result.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from analysis.loader import (
    Dataset,
    find_events_file,
    find_policy_file,
    load_events,
    load_policy,
    report_groups,
)
from analysis.schema import (
    COMPARISON_EXPERIMENTS,
    FLOW_ORDER,
    SURGE_EXPERIMENTS,
    sort_flows,
)

COLORS = {
    "high": "#d62728",
    "medium": "#ff7f0e",
    "low": "#2ca02c",
    "background": "#1f77b4",
}

EXPERIMENT_COLORS = {
    "baseline": "#4c72b0",
    "baseline_ryu": "#dd8452",
    "static": "#55a868",
    "dynamic": "#c44e52",
    "static_surge": "#8172b3",
}

METRIC_LABELS = {
    "goodput_mbps": ("Goodput (Mbps)", "comparison_goodput.png"),
    "packet_loss_pct": ("Packet loss (%)", "comparison_loss.png"),
    "p95_latency_ms": ("p95 latency (ms)", "comparison_p95_latency.png"),
    "avg_latency_ms": ("Average latency (ms)", "comparison_avg_latency.png"),
}


class NoPlotData(Exception):
    """Raised when there is not enough data to draw a requested figure."""


def _value(row: dict[str, Any], field: str) -> float | None:
    value = row.get(field)
    if isinstance(value, (int, float)) and not (isinstance(value, float) and math.isnan(value)):
        return float(value)
    return None


def _rows_by_experiment(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["experiment"]), {})[str(row["flow"])] = row
    return grouped


def plot_comparison(
    flow_rows: list[dict[str, Any]],
    output_dir: Path,
    *,
    metric: str,
    filename: str | None = None,
    title: str | None = None,
    experiments: Iterable[str] = COMPARISON_EXPERIMENTS,
) -> Path:
    """Grouped bar chart: one group per flow, one bar per experiment."""

    ylabel, default_filename = METRIC_LABELS[metric]
    filename = filename or default_filename
    experiment_order = [name for name in experiments if name in _rows_by_experiment(flow_rows)]
    grouped = _rows_by_experiment(flow_rows)
    flows = sort_flows(list({str(row["flow"]) for row in flow_rows}))

    if not flows or not experiment_order:
        raise NoPlotData(f"no {metric} rows for experiments {experiment_order}")

    output_dir.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(10, 5.5))
    width = 0.8 / max(1, len(experiment_order))

    for experiment_index, experiment in enumerate(experiment_order):
        offsets = [
            flow_index - 0.4 + width / 2 + experiment_index * width
            for flow_index in range(len(flows))
        ]
        values = [
            _value(grouped.get(experiment, {}).get(flow, {}), metric)
            for flow in flows
        ]
        heights = [value if value is not None else 0.0 for value in values]
        bars = axis.bar(
            offsets,
            heights,
            width=width,
            label=experiment,
            color=EXPERIMENT_COLORS.get(experiment),
            edgecolor="black",
            linewidth=0.4,
        )
        for bar, value in zip(bars, values):
            if value is None:
                axis.annotate(
                    "n/a",
                    (bar.get_x() + bar.get_width() / 2, 0),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    rotation=90,
                )
            else:
                axis.annotate(
                    f"{value:.2f}",
                    (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center",
                    va="bottom",
                    fontsize=7,
                    rotation=90,
                )

    axis.set_xticks(range(len(flows)))
    axis.set_xticklabels(flows)
    axis.set_ylabel(ylabel)
    axis.set_title(title or f"Per-flow {ylabel.lower()}")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(title="Experiment")
    figure.tight_layout()
    path = output_dir / filename
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def estimate_start_ns(events: list[dict[str, Any]]) -> int | None:
    """Infer the monotonic timestamp of the start of the run from events.csv.

    ``run_experiment.py`` currently only logs delayed flow starts.  A surge run
    therefore has a line such as ``flow_start background joined 10s into the
    run``; subtracting that delay gives the run start.  A future ``run_start``
    event is used directly when present.
    """

    if not events:
        return None

    for event in events:
        if event.get("event") in {"run_start", "start", "experiment_start"}:
            return int(event["ts_ns"])

    for event in events:
        detail = str(event.get("detail") or "")
        match = re.search(r"joined\s+([0-9]+(?:\.[0-9]+)?)s", detail)
        if match and event.get("event") == "flow_start":
            delay_ns = int(float(match.group(1)) * 1_000_000_000)
            return int(event["ts_ns"]) - delay_ns

    return int(min(event["ts_ns"] for event in events))


def event_offsets(
    events: list[dict[str, Any]], start_ns: int | None
) -> list[tuple[float, str]]:
    if start_ns is None:
        return []
    offsets: list[tuple[float, str]] = []
    for event in events:
        offset = (int(event["ts_ns"]) - start_ns) / 1_000_000_000
        if offset < 0:
            continue
        label = str(event.get("event") or "event")
        flow = str(event.get("flow") or "")
        detail = str(event.get("detail") or "")
        text = f"{label} {flow}".strip()
        if detail:
            text = f"{text}: {detail}"
        offsets.append((offset, text))
    return offsets


def policy_offsets(
    policy_rows: list[dict[str, Any]], start_ns: int | None
) -> list[tuple[float, str]]:
    # If events.csv is unavailable, show policy changes relative to the first
    # change.  The absolute alignment is then approximate and the caller's
    # warning says so.
    reference = start_ns
    if reference is None and policy_rows:
        reference = int(policy_rows[0]["ts_ns"])
    if reference is None:
        return []

    offsets: list[tuple[float, str]] = []
    for row in policy_rows:
        offset = (int(row["ts_ns"]) - reference) / 1_000_000_000
        if offset < 0:
            continue
        flow = str(row.get("flow_id") or "")
        old_queue = row.get("old_queue")
        new_queue = row.get("new_queue")
        reason = str(row.get("reason") or "")
        text = f"policy {flow}: q{old_queue}→q{new_queue}"
        if reason:
            text = f"{text} ({reason})"
        offsets.append((offset, text))
    return offsets


def _plot_event_lines(
    axes: list[Any],
    events: list[tuple[float, str]],
    policy: list[tuple[float, str]],
) -> None:
    for axis in axes:
        for offset, _text in events:
            axis.axvline(offset, color="#555555", linestyle="--", linewidth=0.9, alpha=0.8)
        for offset, _text in policy:
            axis.axvline(offset, color="#c44e52", linestyle=":", linewidth=1.2, alpha=0.9)

    # Put labels once, above the first panel.
    if axes and (events or policy):
        top = axes[0]
        y = 0.98
        for offset, text in events:
            top.text(
                offset,
                y,
                text,
                rotation=90,
                va="top",
                ha="right",
                fontsize=7,
                color="#333333",
                transform=top.get_xaxis_transform(),
            )
        for offset, text in policy:
            top.text(
                offset,
                y,
                text,
                rotation=90,
                va="top",
                ha="right",
                fontsize=7,
                color="#a52a2a",
                transform=top.get_xaxis_transform(),
            )


def plot_surge_timeline(
    per_second_rows: list[dict[str, Any]],
    output_path: Path,
    *,
    events: list[dict[str, Any]] | None = None,
    policy: list[dict[str, Any]] | None = None,
    title: str = "Dynamic surge timeline",
    run_id: int | None = None,
) -> Path:
    """Three stacked per-second panels: goodput, loss, p95 latency."""

    rows = [
        row for row in per_second_rows
        if run_id is None or row.get("run_id") == run_id
    ]
    if not rows:
        raise NoPlotData(f"no per-second rows for run {run_id}")

    by_flow: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_flow[str(row["flow"])].append(row)
    flows = sort_flows(list(by_flow))

    events = events or []
    policy = policy or []
    start_ns = estimate_start_ns(events)
    event_lines = event_offsets(events, start_ns)
    policy_lines = policy_offsets(policy, start_ns)

    figure, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)
    panels = (
        ("goodput_mbps", "Goodput (Mbps)"),
        ("packet_loss_pct", "Packet loss (%)"),
        ("p95_latency_ms", "p95 latency (ms)"),
    )

    for axis, (field, label) in zip(axes, panels):
        for flow in flows:
            points = sorted(by_flow[flow], key=lambda row: row.get("second") or 0)
            x: list[float] = []
            y: list[float] = []
            for row in points:
                value = _value(row, field)
                if value is None:
                    continue
                second = row.get("second")
                width = row.get("bucket_seconds") or 1.0
                if second is None:
                    continue
                x.append(float(second) + float(width) / 2)
                y.append(value)
            if x:
                axis.plot(x, y, marker="o", markersize=2.5, linewidth=1.4, label=flow, color=COLORS.get(flow))
        axis.set_ylabel(label)
        axis.grid(alpha=0.25)
        axis.legend(loc="upper right", fontsize=8)

    _plot_event_lines(list(axes), event_lines, policy_lines)
    axes[-1].set_xlabel("Seconds since run start")
    axes[0].set_title(title)

    if not events:
        axes[0].text(
            0.01,
            0.95,
            "events.csv not found; vertical event lines omitted",
            transform=axes[0].transAxes,
            fontsize=8,
            color="#a52a2a",
            va="top",
        )
    if not policy:
        axes[0].text(
            0.01,
            0.88,
            "controller policy log not found; policy lines omitted",
            transform=axes[0].transAxes,
            fontsize=8,
            color="#a52a2a",
            va="top",
        )

    figure.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=150)
    plt.close(figure)
    return output_path


def median_timeline_rows(
    per_second_rows: list[dict[str, Any]], run_ids: list[int]
) -> list[dict[str, Any]]:
    """Median per flow/second across repeated runs, for one summary figure."""

    selected = [row for row in per_second_rows if row.get("run_id") in set(run_ids)]
    buckets: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in selected:
        second = row.get("second")
        if second is None:
            continue
        buckets[(str(row["flow"]), int(second))].append(row)

    medians: list[dict[str, Any]] = []
    for (flow, second), rows in sorted(buckets.items(), key=lambda item: (FLOW_ORDER.get(item[0][0], 99), item[0][1])):
        medians.append(
            {
                "experiment_id": "median",
                "run_id": 0,
                "flow": flow,
                "second": second,
                "bucket_seconds": _median_float(row.get("bucket_seconds") for row in rows) or 1.0,
                "expected_packets": _median_float(row.get("expected_packets") for row in rows),
                "unique_received": _median_float(row.get("unique_received") for row in rows),
                "lost_packets": _median_float(row.get("lost_packets") for row in rows),
                "packet_loss_pct": _median_float(row.get("packet_loss_pct") for row in rows),
                "payload_bytes": _median_float(row.get("payload_bytes") for row in rows),
                "goodput_mbps": _median_float(row.get("goodput_mbps") for row in rows),
                "avg_latency_ms": _median_float(row.get("avg_latency_ms") for row in rows),
                "p95_latency_ms": _median_float(row.get("p95_latency_ms") for row in rows),
            }
        )
    return medians


def _median_float(values: Iterable[Any]) -> float | None:
    usable = [float(value) for value in values if isinstance(value, (int, float))]
    if not usable:
        return None
    usable.sort()
    middle = len(usable) // 2
    if len(usable) % 2:
        return usable[middle]
    return (usable[middle - 1] + usable[middle]) / 2


def surge_run_inputs(
    dataset: Dataset,
    results_dir: Path,
    experiments_dir: Path,
    *,
    canonical: str = "dynamic",
    policy_log: Path | None = None,
) -> list[dict[str, Any]]:
    """Collect data for the latest three selected runs of a surge group."""

    groups = report_groups(dataset)
    runs = groups.get(canonical, {})
    inputs: list[dict[str, Any]] = []

    for run_id in sorted(runs):
        rows = [row for row in dataset.per_second if row.get("run_id") == run_id]
        events_path = find_events_file(experiments_dir, results_dir, canonical, run_id)
        policy_path = find_policy_file(
            experiments_dir, results_dir, canonical, run_id, explicit=policy_log
        )
        inputs.append(
            {
                "run_id": run_id,
                "per_second": rows,
                "events": load_events(events_path) if events_path else [],
                "events_path": events_path,
                "policy": load_policy(policy_path, run_id) if policy_path else [],
                "policy_path": policy_path,
            }
        )
    return inputs


def plot_surge_figures(
    dataset: Dataset,
    results_dir: Path,
    experiments_dir: Path,
    output_dir: Path,
    *,
    canonical: str = "dynamic",
    policy_log: Path | None = None,
) -> list[Path]:
    """Write one timeline per run plus a median timeline when repeats exist."""

    inputs = surge_run_inputs(
        dataset,
        results_dir,
        experiments_dir,
        canonical=canonical,
        policy_log=policy_log,
    )
    available = [item for item in inputs if item["per_second"]]
    if not available:
        raise NoPlotData(
            f"no per-second data for {canonical}; run the receiver after "
            "upgrading app/metrics.py, then rerun the surge experiment"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for item in available:
        paths.append(
            plot_surge_timeline(
                item["per_second"],
                output_dir / f"timeline_{canonical}_{item['run_id']}.png",
                events=item["events"],
                policy=item["policy"],
                title=f"{canonical} surge run {item['run_id']}",
                run_id=item["run_id"],
            )
        )

    run_ids = [item["run_id"] for item in available]
    if len(run_ids) >= 2:
        median_rows = median_timeline_rows(dataset.per_second, run_ids)
        # Events are identical across repeats; use the first run's log for the
        # vertical markers.
        first = available[0]
        paths.append(
            plot_surge_timeline(
                median_rows,
                output_dir / f"timeline_{canonical}_median.png",
                events=first["events"],
                policy=first["policy"],
                title=f"{canonical} surge median of {len(run_ids)} runs",
            )
        )

    # Surge reference comparison (static policy vs dynamic) is useful even
    # though it is not one of the four main bar-chart groups.
    return paths


def plot_surge_reference(
    flow_rows: list[dict[str, Any]], output_dir: Path
) -> list[Path]:
    rows = [row for row in flow_rows if row["experiment"] in SURGE_EXPERIMENTS]
    if not rows:
        raise NoPlotData("no static_surge or dynamic rows for the surge reference")
    paths: list[Path] = []
    for metric in ("goodput_mbps", "packet_loss_pct", "p95_latency_ms"):
        ylabel, default_filename = METRIC_LABELS[metric]
        paths.append(
            plot_comparison(
                rows,
                output_dir,
                metric=metric,
                filename=f"surge_{default_filename.removeprefix('comparison_')}",
                title=f"Surge reference: {ylabel.lower()}",
                experiments=SURGE_EXPERIMENTS,
            )
        )
    return paths


__all__ = [
    "NoPlotData",
    "estimate_start_ns",
    "event_offsets",
    "median_timeline_rows",
    "plot_comparison",
    "plot_surge_figures",
    "plot_surge_reference",
    "plot_surge_timeline",
    "policy_offsets",
    "surge_run_inputs",
]
