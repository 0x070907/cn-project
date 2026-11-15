"""Final-report selection rules shared by aggregation, validation and plots.

The runner generates run IDs from the current time and increments them by one
for each repeat in a batch.  A folder can contain older batches, handwritten
test runs, or practice scenarios.  Reports must use only the five reportable
folder/scenario pairs and, for each pair, the latest three run IDs.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, TypeVar

from analysis.schema import CANONICAL_EXPERIMENTS

REPORT_RUN_COUNT = 3

REPORTABLE_PAIRS: frozenset[tuple[str, str]] = frozenset(
    CANONICAL_EXPERIMENTS.values()
)
REPORTABLE_FOLDERS: frozenset[str] = frozenset(
    folder for folder, _scenario in REPORTABLE_PAIRS
)

RunRows = TypeVar("RunRows")


def is_reportable_pair(folder: str, scenario: str) -> bool:
    """Return whether a summary row belongs to one of the five report groups."""

    return (folder, scenario) in REPORTABLE_PAIRS


def latest_run_ids(
    run_ids: Iterable[int],
    count: int = REPORT_RUN_COUNT,
) -> tuple[int, ...]:
    """Return the highest ``count`` run IDs in numeric order.

    Time-generated run IDs increase between batches, so the highest IDs are the
    latest runs.  Older runs are deliberately ignored even when they are not
    consecutive.
    """

    ordered = sorted({int(run_id) for run_id in run_ids})
    return tuple(ordered[-count:]) if count > 0 else ()


def consecutive_run_ids(
    run_ids: Iterable[int],
    count: int = REPORT_RUN_COUNT,
) -> bool:
    """Whether exactly ``count`` selected IDs form one consecutive range."""

    ordered = sorted({int(run_id) for run_id in run_ids})
    if len(ordered) != count:
        return False
    return ordered == list(range(ordered[0], ordered[0] + count))


def select_canonical_groups(
    groups: Mapping[str, Mapping[int, RunRows]],
    count: int = REPORT_RUN_COUNT,
) -> dict[str, dict[int, RunRows]]:
    """Return reportable groups with only their latest ``count`` runs."""

    selected: dict[str, dict[int, RunRows]] = {}
    for canonical in CANONICAL_EXPERIMENTS:
        runs = groups.get(canonical)
        if not runs:
            continue
        selected_ids = latest_run_ids(runs, count=count)
        selected[canonical] = {
            run_id: runs[run_id]
            for run_id in selected_ids
        }
    return selected


def practice_groups(
    groups: Mapping[str, Mapping[int, RunRows]],
) -> dict[str, dict[int, RunRows]]:
    """Return non-reportable groups, keeping their runs for ``--practice``."""

    return {
        name: dict(runs)
        for name, runs in groups.items()
        if name not in CANONICAL_EXPERIMENTS
    }


def selected_run_ids(
    groups: Mapping[str, Mapping[int, Any]],
    count: int = REPORT_RUN_COUNT,
) -> dict[str, tuple[int, ...]]:
    """Return the selected run IDs for each reportable group."""

    return {
        name: tuple(runs)
        for name, runs in select_canonical_groups(groups, count=count).items()
    }


__all__ = [
    "REPORTABLE_FOLDERS",
    "REPORTABLE_PAIRS",
    "REPORT_RUN_COUNT",
    "consecutive_run_ids",
    "is_reportable_pair",
    "latest_run_ids",
    "practice_groups",
    "select_canonical_groups",
    "selected_run_ids",
]
