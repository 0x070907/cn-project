from __future__ import annotations

import socket
import select
import time
from typing import TypedDict

import protocol
import metrics

# this class hints the type checker(mypy) what each value of the runs dict is,keyed by per (run_id, flow_id)
# a TypedDict is still a normal dict at runtime
class RunState(TypedDict):
    
    expected: int
    sequences: set[int]
    last_sequence: int
    started_at: int | None  # None until the first data packet arrives (time.monotonic_ns())
    ended_at: float | None  # None until the end message arrives (time.monotonic(), in seconds)
    ended_at_ns: int | None  # same moment as ended_at but in nanoseconds
    payload_bytes: int
    latencies_ns: list[int]
    duplicates: int
    out_of_order: int


def main() -> None:
    ports: dict[int, int] = protocol.flow_ports
    sockets: dict[socket.socket, int] = {}
    runs: dict[tuple[int, int], RunState] = {}
    completed_throughputs: dict[str, dict[str, float]] = {}
    fairness_recorded: set[str] = set()
    grace_period: float = 1.0

    # map flow IDs back to their names for printing
    flow_names: dict[int, str] = {
        flow_id: name for name, flow_id in protocol.flow_ids.items()
    }

    # keep track of packets received across all runs for the final summary
    received: dict[int, int] = {flow_id: 0 for flow_id in ports}
    duplicates: dict[int, int] = {flow_id: 0 for flow_id in ports}
    out_of_order: dict[int, int] = {flow_id: 0 for flow_id in ports}
    last_sequence: dict[int, int] = {flow_id: -1 for flow_id in ports}

    try:
        # create one socket for each traffic flow
        for flow_id, port in ports.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", port))
            sockets[sock] = flow_id
            print(f"listening for {flow_names[flow_id]} traffic on port {port}")

        print("receiver running; press Ctrl+C to stop")

        while True:
            # select lets us listen on all four sockets without blocking on just one
            readable, _, _ = select.select(list(sockets), [], [], 0.2)
            now = time.monotonic()

            for sock in readable:
                packet, address = sock.recvfrom(65535)
                flow_id = sockets[sock]

                # we check whether this is a start/end control message or a data packet
                control = protocol.unpack_control(packet)

                if control is not None:
                    message_type, run_id, control_flow_id, count = control

                    # ignore control messages sent to the wrong flow port
                    if control_flow_id != flow_id:
                        continue

                    key = (run_id, flow_id)

                    if message_type == protocol.start_message:
                        # create tracking state for this run when it starts
                        if key not in runs:
                            runs[key] = {
                                "expected": count,
                                "sequences": set(),
                                "last_sequence": -1,
                                "started_at": None,
                                "ended_at": None,
                                "ended_at_ns": None,
                                "payload_bytes": 0,
                                "latencies_ns": [],
                                "duplicates": 0,
                                "out_of_order": 0,
                            }
                            print(
                                f"{flow_names[flow_id]} run {run_id} started; "
                                f"expecting {count} packets"
                            )

                        elif runs[key]["expected"] != count:
                            continue

                        # acknowledge the start so the sender can begin transmitting
                        sock.sendto(
                            protocol.pack_control(
                                protocol.start_ack, run_id, flow_id, count
                            ),
                            address,
                        )

                    elif message_type == protocol.end_message:
                        if key not in runs:
                            continue

                        # we remember when the run ended so late packets can still arrive
                        runs[key]["expected"] = count

                        if runs[key]["ended_at"] is None:
                            runs[key]["ended_at"] = now
                            runs[key]["ended_at_ns"] = time.monotonic_ns()

                        sock.sendto(
                            protocol.pack_control(
                                protocol.end_ack, run_id, flow_id, count
                            ),
                            address,
                        )

                        print(
                            f"{flow_names[flow_id]} run {run_id} ended; "
                            f"waiting for late packets"
                        )

                    continue

                # decode the data packet using the shared packet format
                try:
                    packet_flow_id, sequence_number, run_id, timestamp, payload = (
                        protocol.unpack_data(packet)
                    )
                except (ValueError, TypeError):
                    continue

                if packet_flow_id != flow_id:
                    continue

                key = (run_id, flow_id)
                run = runs.get(key)

                # ignore packets that dont belong to a run we're tracking
                if run is None:
                    continue

                received[flow_id] += 1

                # duplicate sequence numbers shouldn't count as unique packets
                if sequence_number in run["sequences"]:
                    duplicates[flow_id] += 1
                    run["duplicates"] += 1
                    print(f"{flow_names[flow_id]}: duplicate packet {sequence_number}")
                    continue

                # a sequence number smaller than the previous highest means reordering
                if sequence_number < run["last_sequence"]:
                    out_of_order[flow_id] += 1
                    run["out_of_order"] += 1

                # we track payload size and latency for metrics
                if run["started_at"] is None:
                    run["started_at"] = time.monotonic_ns()

                run["sequences"].add(sequence_number)
                run["last_sequence"] = max(
                    run["last_sequence"], sequence_number
                )

                run["payload_bytes"] += len(payload)
                run["latencies_ns"].append(time.monotonic_ns() - timestamp)

                last_sequence[flow_id] = max(
                    last_sequence[flow_id], sequence_number
                )

                print(
                    f"received {flow_names[flow_id]} packet {sequence_number} "
                    f"from {address[0]}:{address[1]}"
                )

            # we then give delayed UDP packets time to arrive before calculating packet loss
            now = time.monotonic()

            for key, run in list(runs.items()):
                if (
                    run["ended_at"] is not None
                    and now - run["ended_at"] >= grace_period
                ):
                    run_id, flow_id = key
                    expected = run["expected"]
                    unique = len(run["sequences"])
                    lost = max(0, expected - unique)

                    print(
                        f"{flow_names[flow_id]} run {run_id}: "
                        f"expected={expected}, received={unique}, "
                        f"lost={lost}"
                    )

                    if expected > 0:
                        print(f"packet loss: {lost / expected * 100:.2f}%")

                    # calculate the duration from the first data packet to the end message
                    duration_seconds = 0.0

                    if (
                        run["started_at"] is not None
                        and run["ended_at_ns"] is not None
                    ):
                        duration_seconds = max(
                            0,
                            run["ended_at_ns"] - run["started_at"]
                        ) / 1_000_000_000

                    # now we save the completed run's metrics to the CSV
                    experiment_id = str(run_id)

                    metrics.record_run(
                        experiment_id=experiment_id,
                        run_id=run_id,
                        flow=flow_names[flow_id],
                        expected_packets=expected,
                        sequences=run["sequences"],
                        duplicates=run["duplicates"],
                        out_of_order=run["out_of_order"],
                        payload_bytes=run["payload_bytes"],
                        duration_seconds=duration_seconds,
                        latencies_ns=run["latencies_ns"],
                    )

                    # save each flow's measured goodput for this experiment
                    if duration_seconds > 0:
                        goodput_mbps = (
                            run["payload_bytes"] * 8
                            / duration_seconds
                            / 1_000_000
                        )
                    else:
                        goodput_mbps = 0.0

                    if experiment_id not in completed_throughputs:
                        completed_throughputs[experiment_id] = {}

                    completed_throughputs[experiment_id][flow_names[flow_id]] = (
                        goodput_mbps
                    )

                    # calculate fairness once all four flows have completed
                    throughputs = completed_throughputs[experiment_id]

                    if (
                        set(throughputs)
                        == {"high", "medium", "low", "background"}
                        and experiment_id not in fairness_recorded
                    ):
                        metrics.record_fairness(experiment_id, throughputs)
                        fairness_recorded.add(experiment_id)

                    del runs[key]

    except KeyboardInterrupt:
        print("\nstopping receiver")

    except OSError as error:
        print(f"receiver error: {error}")

    finally:
        # print the cumulative per flow statistics even when stopped with Ctrl+C for a graceful exit
        print("\nreceiver summary")

        for flow_id, port in ports.items():
            print(
                f"{flow_names[flow_id]} (port {port}): "
                f"received={received[flow_id]}, "
                f"duplicates={duplicates[flow_id]}, "
                f"out of order={out_of_order[flow_id]}"
            )

        # include runs that were interrupted before their normal summary
        for (run_id, flow_id), run in runs.items():
            expected = run["expected"]
            unique = len(run["sequences"])
            lost = max(0, expected - unique)

            print(
                f"{flow_names[flow_id]} run {run_id}: "
                f"expected={expected}, received={unique}, lost={lost}"
            )

        # we close every socket before exiting
        for sock in sockets:
            sock.close()

        print("receiver sockets closed")


if __name__ == "__main__":
    main()
