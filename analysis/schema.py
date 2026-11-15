"""Shared CSV schemas and experiment naming for the analysis pipeline.

The application writes append-only CSV files in ``results/``.  The experiment
runner copies the per-run metric rows into ``experiments/<label>/summary.csv``.
This module is the single place that describes those files, so the validator
and the analysis pipeline cannot silently disagree about column names.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FLOWS: tuple[str, ...] = ("high", "medium", "low", "background")
FLOW_ORDER: dict[str, int] = {flow: index for index, flow in enumerate(FLOWS)}

# Only high and medium run in the no-congestion baseline.  The other scenarios
# use all four flows.
EXPECTED_FLOWS: dict[str, tuple[str, ...]] = {
    "no_congestion": ("high", "medium"),
    "congestion": FLOWS,
    "surge": FLOWS,
}

# The 28-byte application header from app/protocol.py (format "!IQQq").
# Throughput counts this header plus payload; goodput counts payload only.
DATA_HEADER_BYTES = 28

# Files written before the throughput split do not have these two columns.
# They remain readable for practice/legacy data, but the final gate requires
# them in every selected run.
THROUGHPUT_FIELDS: tuple[str, ...] = ("received_bytes", "throughput_mbps")

# One row per flow per run.  Keep this identical to app/metrics.py:fields.
METRICS_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "run_id",
    "flow",
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

# Header written by the pre-throughput receiver.
LEGACY_METRICS_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "run_id",
    "flow",
    "expected_packets",
    "unique_received",
    "duplicates",
    "out_of_order",
    "lost_packets",
    "packet_loss_pct",
    "payload_bytes",
    "duration_seconds",
    "goodput_mbps",
    "avg_latency_ms",
    "p50_latency_ms",
    "p95_latency_ms",
)

# One row per run.  experiment_id mirrors run_id in the current application.
FAIRNESS_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "high_mbps",
    "medium_mbps",
    "low_mbps",
    "background_mbps",
    "jains_fairness",
)

# Written by mininet/run_experiment.py.
MANIFEST_FIELDS: tuple[str, ...] = ("run_id", "scenario", "mode", "duration_s")

# experiments/<label>/summary.csv = scenario + mode + the metrics columns.
SUMMARY_FIELDS: tuple[str, ...] = ("scenario", "mode") + METRICS_FIELDS
LEGACY_SUMMARY_FIELDS: tuple[str, ...] = ("scenario", "mode") + LEGACY_METRICS_FIELDS

# Written by app/receiver.py through app/metrics.py after this change.
PER_SECOND_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "run_id",
    "flow",
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

LEGACY_PER_SECOND_FIELDS: tuple[str, ...] = (
    "experiment_id",
    "run_id",
    "flow",
    "second",
    "bucket_seconds",
    "expected_packets",
    "unique_received",
    "lost_packets",
    "packet_loss_pct",
    "payload_bytes",
    "goodput_mbps",
    "avg_latency_ms",
    "p95_latency_ms",
)

# Written by the experiment runner for the surge scenario.
EVENT_FIELDS: tuple[str, ...] = ("ts_ns", "run_id", "event", "flow", "detail")

# Owned by the dynamic-controller teammate.  The loader accepts either ``ts``
# or ``ts_ns`` as the first column and normalises it to ``ts_ns``.
POLICY_FIELDS: tuple[str, ...] = ("ts_ns", "flow_id", "old_queue", "new_queue", "reason")

# The five reportable combinations.  static_surge is the reference point for
# dynamic, so it is validated alongside the four main experiments.
CANONICAL_EXPERIMENTS: dict[str, tuple[str, str]] = {
    "baseline": ("baseline", "no_congestion"),
    "baseline_ryu": ("baseline_ryu", "congestion"),
    "static": ("static", "congestion"),
    "dynamic": ("dynamic", "surge"),
    "static_surge": ("static", "surge"),
}

# The four experiments used in the comparison bar charts.  static_surge is a
# fifth reference, not one of the four main comparison groups.
COMPARISON_EXPERIMENTS: tuple[str, ...] = (
    "baseline",
    "baseline_ryu",
    "static",
    "dynamic",
)

SURGE_EXPERIMENTS: tuple[str, ...] = ("static_surge", "dynamic")

KNOWN_MANIFEST_SCENARIOS: tuple[str, ...] = tuple(EXPECTED_FLOWS)
KNOWN_MANIFEST_MODES: tuple[str, ...] = ("none", "remote")


def canonical_name(folder: str, scenario: str) -> str:
    """Return the report name for a summary folder + scenario pair.

    ``run_all.py`` uses the label ``static`` for both the congested static run
    and the extra static-policy surge run, so the scenario is part of the
    identity.  Practice folders (``test``, ``receiver_test``) stay visible as
    ``<folder>/<scenario>`` and are never mixed into final-report groups.
    """

    for name, (expected_folder, expected_scenario) in CANONICAL_EXPERIMENTS.items():
        if folder == expected_folder and scenario == expected_scenario:
            return name
    return f"{folder}/{scenario}"


def expected_flows(scenario: str) -> tuple[str, ...]:
    return EXPECTED_FLOWS.get(scenario, FLOWS)


def sort_flows(flows: set[str] | list[str] | tuple[str, ...]) -> list[str]:
    return sorted(set(flows), key=lambda flow: FLOW_ORDER.get(flow, len(FLOW_ORDER)))
