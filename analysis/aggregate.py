"""Aggregate repeated runs into medians and fairness tables."""

from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from analysis.loader import Dataset, canonical_groups
from analysis.schema import (
    COMPARISON_EXPERIMENTS,
    FLOWS,
    expected_flows,
    sort_flows,
)

FLOW_METRICS: tuple[str, ...] = (
    "goodput_mbps",
    "packet_loss_pct",
    "avg_latency_ms",
    "p95_latency_ms",
)

FLOW_TABLE_FIELDS: tuple[str, ...] = (
    "experiment",
    "scenario",
    "flow",
    "n_runs",
    "run_ids",
    "goodput_mbps_median",
    "packet_loss_pct_median",
    "avg_latency_ms_median",
    "p95_latency_ms_median",
)

FAIRNESS_TABLE_FIELDS: tuple[str, ...] = (
    "experiment",
    "scenario",
    "n_runs",
    "run_ids",
    "jains_fairness_median",
    "jains_fairness_min",
    "jains_fairness_max",
    "high_mbps_median",
    "medium_mbps_median",
    "low_mbps_median",
    "background_mbps_median",
)

FAIRNESS_RUN_FIELDS: tuple[str, ...] = (
    "experiment",
    "run_id",
    "n_flows",
    "flows",
    "high_mbps",
    "medium_mbps",
    "low_mbps",
    "background_mbps",
    "jains_fairness",
    "source",
)


def _median(values: Iterable[float]) -> float | None:
    usable = [value for value in values if value is not None]
    if not usable:
        return None
    return float(statistics.median(usable))


def _metric(row: dict[str, Any], name: str) -> float | None:
    value = row.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def aggregate_flow_medians(dataset: Dataset) -> list[dict[str, Any]]:
    """Median of each flow metric across a canonical experiment's runs."""

    groups = canonical_groups(dataset)
    table: list[dict[str, Any]] = []

    for canonical, runs in groups.items():
        scenario = ""
        for run_rows in runs.values():
            if run_rows:
                scenario = str(run_rows[0].get("scenario") or "")
                break
        wanted_flows = expected_flows(scenario) if scenario else FLOWS
        run_ids = sorted(runs)

        by_flow: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
        for run_id, run_rows in runs.items():
            for row in run_rows:
                flow = row.get("flow")
                if flow in wanted_flows:
                    by_flow[str(flow)].append((run_id, row))

        for flow in sort_flows(list(by_flow)):
            if flow not in wanted_flows and not by_flow[flow]:
                continue
            entries = by_flow.get(flow, [])
            table.append(
                {
                    "experiment": canonical,
                    "scenario": scenario,
                    "flow": flow,
                    "n_runs": len({run_id for run_id, _row in entries}),
                    "run_ids": " ".join(str(run_id) for run_id in run_ids),
                    "goodput_mbps_median": _median(
                        _metric(row, "goodput_mbps") for _run_id, row in entries
                    ),
                    "packet_loss_pct_median": _median(
                        _metric(row, "packet_loss_pct") for _run_id, row in entries
                    ),
                    "avg_latency_ms_median": _median(
                        _metric(row, "avg_latency_ms") for _run_id, row in entries
                    ),
                    "p95_latency_ms_median": _median(
                        _metric(row, "p95_latency_ms") for _run_id, row in entries
                    ),
                }
            )

        # Make a missing flow visible in the table instead of silently leaving
        # it out of a comparison.
        for flow in sort_flows(list(wanted_flows)):
            if flow in by_flow:
                continue
            table.append(
                {
                    "experiment": canonical,
                    "scenario": scenario,
                    "flow": flow,
                    "n_runs": 0,
                    "run_ids": " ".join(str(run_id) for run_id in run_ids),
                    "goodput_mbps_median": None,
                    "packet_loss_pct_median": None,
                    "avg_latency_ms_median": None,
                    "p95_latency_ms_median": None,
                }
            )

    return table


def jains_index(throughputs: Iterable[float]) -> float | None:
    values = [float(value) for value in throughputs if value is not None and value >= 0]
    if not values or sum(value * value for value in values) == 0:
        return None
    total = sum(values)
    return total * total / (len(values) * sum(value * value for value in values))


def fairness_per_run(dataset: Dataset) -> list[dict[str, Any]]:
    """One fairness row per run, preferring the receiver's fairness.csv value."""

    groups = canonical_groups(dataset)
    rows: list[dict[str, Any]] = []

    for canonical, runs in groups.items():
        for run_id, run_rows in sorted(runs.items()):
            throughputs = {
                str(row.get("flow")): _metric(row, "goodput_mbps") or 0.0
                for row in run_rows
                if row.get("flow") in FLOWS
            }
            if not throughputs:
                continue

            fairness_row = dataset.fairness.get(run_id)
            if fairness_row is not None:
                jain = fairness_row.get("jains_fairness")
                source = "fairness.csv"
            else:
                jain = None
                source = "recomputed"

            if jain is None:
                jain = jains_index(throughputs.values())

            scenario = str(run_rows[0].get("scenario") or "")
            rows.append(
                {
                    "experiment": canonical,
                    "scenario": scenario,
                    "run_id": run_id,
                    "n_flows": len(throughputs),
                    "flows": " ".join(sort_flows(list(throughputs))),
                    "high_mbps": throughputs.get("high", 0.0),
                    "medium_mbps": throughputs.get("medium", 0.0),
                    "low_mbps": throughputs.get("low", 0.0),
                    "background_mbps": throughputs.get("background", 0.0),
                    "jains_fairness": jain,
                    "source": source,
                }
            )
    return rows


def aggregate_fairness(dataset: Dataset) -> list[dict[str, Any]]:
    per_run = fairness_per_run(dataset)
    by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in per_run:
        by_experiment[str(row["experiment"])].append(row)

    table: list[dict[str, Any]] = []
    for canonical, rows in by_experiment.items():
        rows.sort(key=lambda row: int(row["run_id"]))
        scenario = str(rows[0].get("scenario") or "")
        jain_values = [float(row["jains_fairness"]) for row in rows if row.get("jains_fairness") is not None]
        table.append(
            {
                "experiment": canonical,
                "scenario": scenario,
                "n_runs": len({row["run_id"] for row in rows}),
                "run_ids": " ".join(str(row["run_id"]) for row in rows),
                "jains_fairness_median": _median(jain_values),
                "jains_fairness_min": min(jain_values) if jain_values else None,
                "jains_fairness_max": max(jain_values) if jain_values else None,
                "high_mbps_median": _median(float(row["high_mbps"]) for row in rows),
                "medium_mbps_median": _median(float(row["medium_mbps"]) for row in rows),
                "low_mbps_median": _median(float(row["low_mbps"]) for row in rows),
                "background_mbps_median": _median(float(row["background_mbps"]) for row in rows),
            }
        )
    return table


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _format(value: Any, digits: int = 3) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def render_flow_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Experiment | Flow | Runs | Goodput (Mbps) | Loss (%) | Avg latency (ms) | p95 latency (ms) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {experiment} | {flow} | {n_runs} | {goodput} | {loss} | {avg} | {p95} |".format(
                experiment=row["experiment"],
                flow=row["flow"],
                n_runs=row["n_runs"],
                goodput=_format(row["goodput_mbps_median"]),
                loss=_format(row["packet_loss_pct_median"]),
                avg=_format(row["avg_latency_ms_median"]),
                p95=_format(row["p95_latency_ms_median"]),
            )
        )
    return "\n".join(lines) + "\n"


def render_fairness_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Experiment | Runs | Jain median | Jain min–max | High Mbps | Medium Mbps | Low Mbps | Background Mbps |",
        "|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        spread = (
            f"{_format(row['jains_fairness_min'])} – {_format(row['jains_fairness_max'])}"
            if row["jains_fairness_min"] is not None
            else "—"
        )
        lines.append(
            "| {experiment} | {n_runs} | {median} | {spread} | {high} | {medium} | {low} | {background} |".format(
                experiment=row["experiment"],
                n_runs=row["n_runs"],
                median=_format(row["jains_fairness_median"]),
                spread=spread,
                high=_format(row["high_mbps_median"]),
                medium=_format(row["medium_mbps_median"]),
                low=_format(row["low_mbps_median"]),
                background=_format(row["background_mbps_median"]),
            )
        )
    return "\n".join(lines) + "\n"


def comparison_rows(flow_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the four main experiments, in report order."""

    order = {name: index for index, name in enumerate(COMPARISON_EXPERIMENTS)}
    return sorted(
        (row for row in flow_rows if row["experiment"] in order),
        key=lambda row: (order[row["experiment"]], row["flow"]),
    )


__all__ = [
    "FAIRNESS_RUN_FIELDS",
    "FAIRNESS_TABLE_FIELDS",
    "FLOW_TABLE_FIELDS",
    "aggregate_fairness",
    "aggregate_flow_medians",
    "comparison_rows",
    "fairness_per_run",
    "jains_index",
    "render_fairness_table",
    "render_flow_table",
    "write_csv",
]
