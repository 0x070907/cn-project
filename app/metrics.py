# this lets type hints like tuple[int, int] ; int | None ; etc... work on python versions older than 3.10 ,the old way was Tuple[int, int]; Optional[int]  from the typing module. it has no effect on how the program runs
from __future__ import annotations

import csv
import statistics
import time
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
# imp (fix this later when done with the full thingy):  currently we assume that the sender and receivers monotonic clocks share the same time base, it works for localhost setup but may not work for mininet  

# results go in results/, one level above app/, no matter where the script is run from
results_dir = Path(__file__).resolve().parent.parent / "results"
csv_path = results_dir / "metrics.csv"
fairness_path = results_dir / "fairness.csv"

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
    "duration_seconds",
    "goodput_mbps",
    "avg_latency_ms",
    "p50_latency_ms",
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
) -> None:
    
    # only unique sequence numbers count as successfully received packets
    unique_received = len(sequences)
    lost_packets = max(0, expected_packets - unique_received)

    if expected_packets > 0:
        packet_loss_pct = lost_packets / expected_packets * 100
    else:
        packet_loss_pct = 0.0

    # goodput measures useful payload delivered per second (headers excluded, so it sits slightly below the sending rate)
    if duration_seconds > 0:
        goodput_mbps = payload_bytes * 8 / duration_seconds / 1_000_000
    else:
        goodput_mbps = 0.0

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
        "duration_seconds": round(duration_seconds, 6),
        "goodput_mbps": round(goodput_mbps, 6),
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

    # then we uh append each completed run without overwriting previous results
    results_dir.mkdir(parents=True, exist_ok=True)  # create results/ on first write
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with csv_path.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)

        if not file_exists:
            writer.writeheader()

        writer.writerow(row)

    print(f"saved metrics for {flow} run {run_id}")

# Collection: needs len() and has to loop over the values several times,so a single lookup Iterable like a generator would not be safe. it never indexes, so no Sequence needed
def jains_fairness(throughputs: Collection[float]) -> float | None:

    # fairness is undefined if every flow has zero throughput
    if not throughputs or sum(x*x for x in throughputs) == 0:
        return None

    total = sum(throughputs)
    return total * total / ( len(throughputs) * sum(x*x for x in throughputs) )
    # Jain's index J = (sum x)^2 / (n * sum x^2): 1 means every flow got the same, 1/n means one flow took everything


def record_fairness(experiment_id: str, throughputs: dict[str, float]) -> None:  # saves the results by calling jains_fairness func internally and then saves the individual flow throughputs and the calc score into fairness.csv

    fairness : list[float] = jains_fairness(list(throughputs.values()))

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

