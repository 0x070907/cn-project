# this lets type hints like tuple[int, int] ; int | None ; etc... work on python versions older than 3.10 ,the old way was Tuple[int, int]; Optional[int]  from the typing module. it has no effect on how the program runs
from __future__ import annotations

import csv
import os
import statistics
import time
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TypedDict

try:
    # Normal execution: receiver.py runs from app/ and imports siblings directly.
    from csv_schema import append_dict_rows, ensure_schema, read_dict_rows
    from protocol import data_header_size
except ImportError:  # pragma: no cover - exercised when imported as app.metrics
    # Test/package execution: the repository root is on sys.path.
    from app.csv_schema import append_dict_rows, ensure_schema, read_dict_rows
    from app.protocol import data_header_size

# imp (fix this later when done with the full thingy):  currently we assume that the sender and receivers monotonic clocks share the same time base, it works for localhost setup but may not work for mininet  

# results go in results/, one level above app/, no matter where the script is run from.
# QOS_RESULTS_DIR is only used by tests/local checks; the experiment runner keeps
# the default so all normal runs write to the same place.
_results_override = os.environ.get("QOS_RESULTS_DIR")
results_dir = (
    Path(_results_override).expanduser()
    if _results_override
    else Path(__file__).resolve().parent.parent / "results"
)
csv_path = results_dir / "metrics.csv"
fairness_path = results_dir / "fairness.csv"
per_second_path = results_dir / "per_second.csv"


class SecondBucket(TypedDict):
    """Packets that arrived during one second of one flow's run."""

    sequences: set[int]
    payload_bytes: int
    received_bytes: int
    latencies_ns: list[int]

# column order of metrics.csv: one row per flow per run
fields: list[str] = [
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
]

# Header written before throughput was separated from goodput.  The migration
# path uses this to reconstruct received_bytes/throughput_mbps for old rows.
legacy_fields: list[str] = [
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
]

# one row per flow per second, used by the dynamic surge timeline.  The totals
# in metrics.csv stay the authoritative per-run numbers; these rows split the
# same packets over time so the graph can show what changed while the run was
# still going.
per_second_fields: list[str] = [
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
]

legacy_per_second_fields: list[str] = [
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
]


# Sequence can be indexed or len() works,so it needs an ordered thing like a list. a set would not work
def percentile(values: Sequence[float], p: float) -> float | None:
    # nearest-rank percentile: the smallest value that at least p% of the samples are <= to (e.g. p95 = 95% of packets were faster than this)

    if not values:
        return None

    values = sorted(values)
    index = max(0, int((p / 100) * len(values) + 0.999999) - 1)
    return values[index]


def _mbps(byte_count: int, duration_seconds: float) -> float:
    """Convert bytes over a duration to Mbps, using zero for no duration."""

    if duration_seconds <= 0:
        return 0.0
    return byte_count * 8 / duration_seconds / 1_000_000


def migrate_legacy_metric_row(row: Mapping[str, str]) -> dict[str, str]:
    """Add the throughput fields to a pre-throughput metrics/summary row.

    The old files only stored payload bytes.  Reconstruct the complete
    application datagram count from the known QoS header size, then use the
    same formula as ``record_run``.  Existing goodput and all other values are
    preserved exactly.
    """

    migrated = dict(row)
    payload_bytes = int(row["payload_bytes"])
    unique_received = int(row["unique_received"])
    duration_seconds = float(row["duration_seconds"])

    received_bytes = payload_bytes + unique_received * data_header_size
    migrated["received_bytes"] = str(received_bytes)
    migrated["throughput_mbps"] = str(
        round(_mbps(received_bytes, duration_seconds), 6)
    )
    return migrated


def migrate_legacy_per_second_row(row: Mapping[str, str]) -> dict[str, str]:
    """Add throughput fields to a pre-throughput per-second row."""

    migrated = dict(row)
    payload_bytes = int(row["payload_bytes"])
    unique_received = int(row["unique_received"])
    bucket_seconds = float(row["bucket_seconds"])

    received_bytes = payload_bytes + unique_received * data_header_size
    migrated["received_bytes"] = str(received_bytes)
    migrated["throughput_mbps"] = str(
        round(_mbps(received_bytes, bucket_seconds), 6)
    )
    return migrated


def ensure_metrics_schema(path: Path | None = None) -> bool:
    """Migrate ``metrics.csv`` to the current schema before reading/appending."""

    target = path or csv_path
    return ensure_schema(
        target,
        current_fields=fields,
        legacy_fields=legacy_fields,
        migrate_legacy=migrate_legacy_metric_row,
        label="metrics.csv",
    )


def read_metrics_rows(path: Path | None = None) -> list[dict[str, str]]:
    """Read metrics rows after safely migrating old headers."""

    target = path or csv_path
    return read_dict_rows(
        target,
        current_fields=fields,
        legacy_fields=legacy_fields,
        migrate_legacy=migrate_legacy_metric_row,
        label="metrics.csv",
    )


def record_run(
    experiment_id: str,
    run_id: int,
    flow: str,
    expected_packets: int,
    sequences: Collection[int],  # Collection: only len() is used. the receiver passes a set, which is not a Sequence
    duplicates: int,
    out_of_order: int,
    payload_bytes: int,
    duration_seconds: float,
    latencies_ns: Iterable[int],  # Iterable: it is only looped over once (the list comprehension below)
    received_bytes: int | None = None,
) -> None:
    
    # only unique sequence numbers count as successfully received packets
    unique_received = len(sequences)
    lost_packets = max(0, expected_packets - unique_received)

    if expected_packets > 0:
        packet_loss_pct = lost_packets / expected_packets * 100
    else:
        packet_loss_pct = 0.0

    # Throughput counts complete application datagrams (QoS header + payload)
    # for the unique packets that were delivered.  Duplicate datagrams and the
    # UDP/IP headers added by the kernel are not included.  Older callers that
    # do not supply received_bytes get the same value reconstructed from the
    # known protocol header size.
    if received_bytes is None:
        received_bytes = payload_bytes + unique_received * data_header_size

    # goodput measures useful payload delivered per second (headers excluded, so it sits slightly below the sending rate)
    goodput_mbps = _mbps(payload_bytes, duration_seconds)
    throughput_mbps = _mbps(received_bytes, duration_seconds)

    # we convert latency samples from nanoseconds to milliseconds
    latencies_ms = [
        latency / 1_000_000
        for latency in latencies_ns
        if latency >= 0
    ]

    row: dict[str, str | int | float] = {
        "experiment_id": experiment_id,
        "run_id": run_id,
        "flow": flow,
        "expected_packets": expected_packets,
        "unique_received": unique_received,
        "duplicates": duplicates,
        "out_of_order": out_of_order,
        "lost_packets": lost_packets,
        "packet_loss_pct": round(packet_loss_pct, 4),
        "payload_bytes": payload_bytes,
        "received_bytes": received_bytes,
        "duration_seconds": round(duration_seconds, 6),
        "goodput_mbps": round(goodput_mbps, 6),
        "throughput_mbps": round(throughput_mbps, 6),
        "avg_latency_ms": (
            round(statistics.mean(latencies_ms), 6)
            if latencies_ms else ""
        ),
        "p50_latency_ms": (
            round(percentile(latencies_ms, 50), 6)
            if latencies_ms else ""
        ),
        "p95_latency_ms": (
            round(percentile(latencies_ms, 95), 6)
            if latencies_ms else ""
        ),
    }

    # then we uh append each completed run without overwriting previous results.
    # append_dict_rows first reconciles any legacy header, so a new-width row
    # can never be written underneath an old header.
    results_dir.mkdir(parents=True, exist_ok=True)  # create results/ on first write
    append_dict_rows(
        csv_path,
        current_fields=fields,
        rows=[row],
        legacy_fields=legacy_fields,
        migrate_legacy=migrate_legacy_metric_row,
        label="metrics.csv",
    )

    print(f"saved metrics for {flow} run {run_id}")


def record_per_second(
    experiment_id: str,
    run_id: int,
    flow: str,
    buckets: Mapping[int, SecondBucket],
    expected_packets: int,
    duration_seconds: float,
) -> None:
    """Append one row per second for a completed flow.

    ``buckets`` is keyed by the integer second since the first data packet.
    The sender does not transmit a per-second schedule, so the expected packet
    count for a second is estimated by spreading this flow's known total
    evenly over the measured duration.  The per-run rows in metrics.csv remain
    exact; these rows are a timeline view and are labelled as such in the docs.
    """

    if not buckets:
        return

    max_second = max(buckets)
    span_seconds = duration_seconds if duration_seconds > 0 else float(max_second + 1)

    def cumulative_expected(seconds: float) -> int:
        if expected_packets <= 0 or span_seconds <= 0:
            return 0
        fraction = min(max(seconds, 0.0), span_seconds) / span_seconds
        return int(round(expected_packets * fraction))

    rows: list[dict[str, str | int | float]] = []

    for second in range(max_second + 1):
        start = float(second)
        end = min(float(second + 1), span_seconds)

        # A flow whose END arrives very soon after its last packet can leave a
        # sub-millisecond final bucket.  Keep a positive width so goodput does
        # not divide by zero.
        if end <= start:
            end = start + 1.0
        bucket_seconds = end - start

        bucket = buckets.get(
            second,
            {
                "sequences": set(),
                "payload_bytes": 0,
                "received_bytes": 0,
                "latencies_ns": [],
            },
        )
        unique_received = len(bucket["sequences"])
        expected_this_second = max(
            0, cumulative_expected(end) - cumulative_expected(start)
        )
        lost_packets = max(0, expected_this_second - unique_received)

        if expected_this_second > 0:
            packet_loss_pct = lost_packets / expected_this_second * 100
        else:
            packet_loss_pct = 0.0

        payload_bytes = bucket["payload_bytes"]
        received_bytes = bucket.get("received_bytes")
        if received_bytes is None:
            received_bytes = payload_bytes + unique_received * data_header_size
        goodput_mbps = _mbps(payload_bytes, bucket_seconds)
        throughput_mbps = _mbps(received_bytes, bucket_seconds)

        latencies_ms = [
            latency / 1_000_000
            for latency in bucket["latencies_ns"]
            if latency >= 0
        ]

        rows.append(
            {
                "experiment_id": experiment_id,
                "run_id": run_id,
                "flow": flow,
                "second": second,
                "bucket_seconds": round(bucket_seconds, 6),
                "expected_packets": expected_this_second,
                "unique_received": unique_received,
                "lost_packets": lost_packets,
                "packet_loss_pct": round(packet_loss_pct, 4),
                "payload_bytes": payload_bytes,
                "received_bytes": received_bytes,
                "goodput_mbps": round(goodput_mbps, 6),
                "throughput_mbps": round(throughput_mbps, 6),
                "avg_latency_ms": (
                    round(statistics.mean(latencies_ms), 6)
                    if latencies_ms else ""
                ),
                "p95_latency_ms": (
                    round(percentile(latencies_ms, 95), 6)
                    if latencies_ms else ""
                ),
            }
        )

    results_dir.mkdir(parents=True, exist_ok=True)
    append_dict_rows(
        per_second_path,
        current_fields=per_second_fields,
        rows=rows,
        legacy_fields=legacy_per_second_fields,
        migrate_legacy=migrate_legacy_per_second_row,
        label="per_second.csv",
    )

    print(f"saved per-second metrics for {flow} run {run_id}")


# Collection: needs len() and has to loop over the values several times,so a single lookup Iterable like a generator would not be safe. it never indexes, so no Sequence needed
def jains_fairness(throughputs: Collection[float]) -> float | None:

    # fairness is undefined if every flow has zero throughput
    if not throughputs or sum(x*x for x in throughputs) == 0:
        return None

    total = sum(throughputs)
    return total * total / ( len(throughputs) * sum(x*x for x in throughputs) )
    # Jain's index J = (sum x)^2 / (n * sum x^2): 1 means every flow got the same, 1/n means one flow took everything


def record_fairness(experiment_id: str, throughputs: dict[str, float]) -> None:  # saves the results by calling jains_fairness func internally and then saves the individual flow throughputs and the calc score into fairness.csv

    fairness : float | None = jains_fairness(list(throughputs.values()))

    if fairness is None:
        print("cannot calculate fairness when all flows have zero throughput")
        return

    path = fairness_path  # one row per experiment (all four flows together), unlike metrics.csv which has one row per flow
    
    fields: list[str] = [
        "experiment_id",
        "high_mbps",
        "medium_mbps",
        "low_mbps",
        "background_mbps",
        "jains_fairness",
    ]

    row: dict[str, str | float] = {
        "experiment_id": experiment_id,
        "high_mbps": throughputs.get("high", 0),
        "medium_mbps": throughputs.get("medium", 0),
        "low_mbps": throughputs.get("low", 0),
        "background_mbps": throughputs.get("background", 0),
        "jains_fairness": round(fairness, 6),
    }

    results_dir.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists() and path.stat().st_size > 0

    with path.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    print(f"saved fairness for experiment {experiment_id}")

