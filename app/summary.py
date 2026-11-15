"""Read and write ``experiments/<label>/summary.csv`` safely.

A summary is a metrics row plus the scenario/mode metadata that metrics.csv
does not store.  It must use exactly the same metric schema as metrics.csv;
otherwise a summary written under an old header shifts every metric after the
payload byte count.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

try:
    from app import metrics as app_metrics
    from app.csv_schema import append_dict_rows, read_dict_rows, write_dict_rows
except ImportError:  # pragma: no cover - used when app/ is on sys.path
    import metrics as app_metrics  # type: ignore[no-redef]
    from csv_schema import append_dict_rows, read_dict_rows, write_dict_rows  # type: ignore[no-redef]

SUMMARY_FIELDS: tuple[str, ...] = ("scenario", "mode") + tuple(app_metrics.fields)
LEGACY_SUMMARY_FIELDS: tuple[str, ...] = (
    ("scenario", "mode") + tuple(app_metrics.legacy_fields)
)


def summary_row(
    scenario: str,
    mode: str,
    metric_row: Mapping[str, object],
) -> dict[str, object]:
    """Combine metadata with a metrics row using the current column order."""

    row: dict[str, object] = {"scenario": scenario, "mode": mode}
    for field in app_metrics.fields:
        row[field] = metric_row.get(field, "")
    return row


def read_summary_rows(path: Path) -> list[dict[str, str]]:
    """Read a summary, migrating its legacy metric columns if needed."""

    return read_dict_rows(
        path,
        current_fields=SUMMARY_FIELDS,
        legacy_fields=LEGACY_SUMMARY_FIELDS,
        migrate_legacy=app_metrics.migrate_legacy_metric_row,
        label=f"summary:{path.parent.name}",
    )


def write_summary_rows(
    path: Path,
    rows: Iterable[Mapping[str, object]],
) -> None:
    """Replace a summary atomically with current-schema rows."""

    write_dict_rows(path, SUMMARY_FIELDS, rows)


def append_summary_rows(
    path: Path,
    scenario: str,
    mode: str,
    metric_rows: Iterable[Mapping[str, object]],
) -> None:
    """Append metric rows as summary rows after reconciling the header."""

    rows = [
        summary_row(scenario, mode, metric_row)
        for metric_row in metric_rows
    ]
    if not rows:
        return

    append_dict_rows(
        path,
        current_fields=SUMMARY_FIELDS,
        rows=rows,
        legacy_fields=LEGACY_SUMMARY_FIELDS,
        migrate_legacy=app_metrics.migrate_legacy_metric_row,
        label=f"summary:{path.parent.name}",
    )
