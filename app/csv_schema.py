"""Schema-safe helpers for the append-only measurement CSVs.

The receiver may be upgraded while an old CSV is still on disk.  Appending a
new-width row under an old header silently shifts every value, so every writer
must reconcile the header before it appends.  These helpers migrate legacy
rows atomically, refuse unknown headers, and validate row widths on reads.
"""

from __future__ import annotations

import csv
import ast
import os
import stat
import tempfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path


class CsvSchemaError(RuntimeError):
    """Raised when a CSV cannot be migrated or read without losing data."""


Migration = Callable[[Mapping[str, str]], Mapping[str, str]]


def _read_table(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    with path.open(newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return [], []

        rows: list[tuple[int, list[str]]] = []
        for line, row in enumerate(reader, start=2):
            # Blank lines carry no measurement.  They are skipped rather than
            # treated as rows, but any non-empty malformed row is an error.
            if not row or all(cell == "" for cell in row):
                continue
            rows.append((line, row))
    return header, rows


def _check_widths(
    path: Path,
    label: str,
    fields: Sequence[str],
    rows: Iterable[tuple[int, list[str]]],
) -> None:
    for line, row in rows:
        if len(row) != len(fields):
            raise CsvSchemaError(
                f"{label} {path}:{line}: row has {len(row)} values but the "
                f"header has {len(fields)} columns"
            )


def write_rows(
    path: Path,
    fields: Sequence[str],
    rows: Iterable[Iterable[object]],
) -> None:
    """Replace a CSV atomically, preserving its permissions where possible."""

    fields = list(fields)
    materialised = [list(row) for row in rows]
    _check_widths(path, path.name, fields, enumerate(materialised, start=2))

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(fields)
            writer.writerows(materialised)

        if path.exists():
            os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_dict_rows(
    path: Path,
    fields: Sequence[str],
    rows: Iterable[Mapping[str, object]],
) -> None:
    fields = list(fields)
    expected = set(fields)
    materialised: list[list[object]] = []

    for row in rows:
        if set(row) != expected:
            missing = sorted(expected - set(row))
            extra = sorted(set(row) - expected)
            raise CsvSchemaError(
                f"{path.name}: row fields do not match the schema "
                f"(missing={missing}, extra={extra})"
            )
        materialised.append([row[field] for field in fields])

    write_rows(path, fields, materialised)


def ensure_schema(
    path: Path,
    *,
    current_fields: Sequence[str],
    legacy_fields: Sequence[str] = (),
    migrate_legacy: Migration | None = None,
    label: str = "CSV",
) -> bool:
    """Make ``path`` use ``current_fields``, migrating legacy rows if needed.

    Returns ``True`` when the file was rewritten.  A current header is checked
    for row-width consistency but not rewritten.  An unknown header or a
    malformed row raises :class:`CsvSchemaError` instead of guessing.
    """

    current = list(current_fields)
    legacy = list(legacy_fields)

    if not path.exists() or path.stat().st_size == 0:
        return False

    header, rows = _read_table(path)

    if header == current:
        _check_widths(path, label, current, rows)
        return False

    legacy_with_trailing_empty = bool(legacy) and header == legacy + [""]

    if not legacy or (header != legacy and not legacy_with_trailing_empty):
        raise CsvSchemaError(
            f"{label} {path}: unsupported header {header!r}; "
            f"expected {current!r}"
            + (f" or legacy {legacy!r}" if legacy else "")
        )

    if migrate_legacy is None:
        raise CsvSchemaError(
            f"{label} {path}: legacy header found but no migration is configured"
        )

    migrated_rows: list[list[str]] = []
    for line, row in rows:
        # Recover p50 and p95 values packed into one legacy summary cell.
        if legacy_with_trailing_empty and len(row) == len(current) - 1:
            try:
                packed = ast.literal_eval(row[-1])
            except (ValueError, SyntaxError) as error:
                raise CsvSchemaError(
                    f"{label} {path}:{line}: invalid packed percentile values"
                ) from error

            if not isinstance(packed, list) or len(packed) != 2:
                raise CsvSchemaError(
                    f"{label} {path}:{line}: expected two packed percentile values"
                )

            row = row[:-1] + [str(value) for value in packed]
        # A current-width row under an old header was written by the upgraded
        # app after the header change.  Its values are already in the new
        # column order, so keep them as-is.
        if len(row) == len(current):
            migrated_rows.append(row)
            continue
        

        if legacy_with_trailing_empty and len(row) == len(legacy) + 1:
            if row[-1] != "":
                raise CsvSchemaError(
                    f"{label} {path}:{line}: unexpected value in trailing column"
                )
            row = row[:-1]

        if len(row) != len(legacy):
            raise CsvSchemaError(
                f"{label} {path}:{line}: row has {len(row)} values; expected "
                f"{len(legacy)} legacy or {len(current)} current columns"
            )

        try:
            migrated = dict(migrate_legacy(dict(zip(legacy, row))))
        except Exception as error:
            raise CsvSchemaError(
                f"{label} {path}:{line}: cannot migrate legacy row: {error}"
            ) from error

        missing = [name for name in current if name not in migrated]
        if missing:
            raise CsvSchemaError(
                f"{label} {path}:{line}: migration did not produce "
                f"column(s): {', '.join(missing)}"
            )
        migrated_rows.append([str(migrated[name]) for name in current])

    write_rows(path, current, migrated_rows)
    return True


def read_dict_rows(
    path: Path,
    *,
    current_fields: Sequence[str],
    legacy_fields: Sequence[str] = (),
    migrate_legacy: Migration | None = None,
    label: str = "CSV",
) -> list[dict[str, str]]:
    """Read a CSV after reconciling its schema, returning current-schema rows."""

    current = list(current_fields)
    ensure_schema(
        path,
        current_fields=current,
        legacy_fields=legacy_fields,
        migrate_legacy=migrate_legacy,
        label=label,
    )

    if not path.exists() or path.stat().st_size == 0:
        return []

    header, rows = _read_table(path)
    if header != current:
        raise CsvSchemaError(
            f"{label} {path}: header changed unexpectedly while reading"
        )
    _check_widths(path, label, current, rows)
    return [dict(zip(current, row)) for _line, row in rows]


def append_dict_rows(
    path: Path,
    *,
    current_fields: Sequence[str],
    rows: Iterable[Mapping[str, object]],
    legacy_fields: Sequence[str] = (),
    migrate_legacy: Migration | None = None,
    label: str = "CSV",
) -> None:
    """Append rows only after the existing header has been reconciled."""

    current = list(current_fields)
    ensure_schema(
        path,
        current_fields=current,
        legacy_fields=legacy_fields,
        migrate_legacy=migrate_legacy,
        label=label,
    )

    materialised = list(rows)
    expected = set(current)
    for row in materialised:
        if set(row) != expected:
            missing = sorted(expected - set(row))
            extra = sorted(set(row) - expected)
            raise CsvSchemaError(
                f"{label} {path}: row fields do not match the schema "
                f"(missing={missing}, extra={extra})"
            )

    file_has_content = path.exists() and path.stat().st_size > 0
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=current, extrasaction="raise")
        if not file_has_content:
            writer.writeheader()
        writer.writerows(materialised)
