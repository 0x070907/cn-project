"""UDP receiver """

# listen on 4 UDP ports (one per flow) -> for every datagram decide whether it is a
# control message (START / END) or a data packet -> keep a scoreboard per (run_id, flow_id)
# -> one second after END arrives, hand the scoreboard to metrics.py, which writes the CSVs.

from __future__ import annotations

import argparse
import socket
import select
import time
from typing import TypedDict

import protocol
import metrics

# this class hints the type checker(mypy) what each value of the runs dict is,keyed by per (run_id, flow_id)
# a TypedDict is still a normal dict at runtime
class RunState(TypedDict):
    
    expected: int              # packet count the sender promised in START/END (only the sender knows the true total)
    sequences: set[int]        # unique sequence numbers seen so far; a set ignores repeats
    last_sequence: int         # highest sequence number so far, used to spot reordering
    started_at: int | None     # None until the first data packet arrives (time.monotonic_ns())
    ended_at: float | None     # None until the end message arrives (time.monotonic(), in seconds)
    ended_at_ns: int | None    # same moment as ended_at but in nanoseconds
    payload_bytes: int         # total payload received (header excluded) -> used for goodput
    latencies_ns: list[int]    # one latency sample per unique packet
    duplicates: int
    out_of_order: int


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--expected-flows",
        type=int,
        choices=range(1, len(protocol.flow_ids) + 1),
        default=len(protocol.flow_ids),
        help="Number of flows expected in each experiment",
    )
    expected_flows = parser.parse_args().expected_flows
    ports: dict[int, int] = protocol.flow_ports
    sockets: dict[socket.socket, int] = {}                    #socket object -> flow_id,the port a packet arrives on tells us its flow
    runs: dict[tuple[int, int], RunState] = {}                #(run_id, flow_id) -> scoreboard of runs still in progress
    # experiment_id -> {flow name: goodput in mbps}, filled as each flow of an experiment finishe,needed for fairness
    completed_throughputs: dict[str, dict[str, float]] = {}
    fairness_recorded: set[str] = set()     # experiments whose fairness row is already written, so we never write it twice
    grace_period: float = 1.0                                 #1 sec to wait after END before finalising,bcz UDP packets may arrive late

    # map flow IDs back to their names for printing
    flow_names: dict[int, str] = {
        flow_id: name for name, flow_id in protocol.flow_ids.items()
    }

    #these totals are only used by the summary printed on Ctrl+C,they do not affect the CSVs
    received: dict[int, int] = {flow_id: 0 for flow_id in ports}
    duplicates: dict[int, int] = {flow_id: 0 for flow_id in ports}
    out_of_order: dict[int, int] = {flow_id: 0 for flow_id in ports}
    last_sequence: dict[int, int] = {flow_id: -1 for flow_id in ports}

    try:
        # create one socket for each traffic flow
        for flow_id, port in ports.items():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # lets us restart the receiver quickly
            sock.bind(("0.0.0.0", port))           # 0.0.0.0 = listen on every interface (needed so h4 in mininet can hear h1/h2/h3)
            sockets[sock] = flow_id
            print(f"listening for {flow_names[flow_id]} traffic on port {port}")

        print("receiver running; press Ctrl+C to stop")

        while True:
            
            # select lets us listen on all four sockets at once without blocking on just one or without using four terminals or threads
            # it  waits until at least one of the four sockets has data (or 0.2s timeout)
            readable, _, _ = select.select(list(sockets), [], [], 0.2)
            now = time.monotonic()    #record the time at which the packet arrived 

            for sock in readable: #readable : list[socket]
                packet, address = sock.recvfrom(65535)
                flow_id : int = sockets[sock]  # which flow this is, decided by the port the packet arrived on

                # we check whether this is a start/end control message or a data packet,but how??
                # protocol.unpack_control(packet) returns None unless the packet is exactly 21 bytes.
                #it also checks the magic text QOS1; data packets are >= 28 bytes so they can never look like control messages
                control : tuple[int, int, int, int] | None = protocol.unpack_control(packet)

                if control is not None:
                    message_type, run_id, control_flow_id, count = control

                    # ignore control messages sent to the wrong flow port
                    if control_flow_id != flow_id:
                        continue

                    #the 4 flows of one experiment share run_id but each has its own flow_id
                    key = (run_id, flow_id)

                    if message_type == protocol.start_message:  # START : creates the scoreboard for this run if it doesn't exist

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

                        #a repeated START (sender never got our ACK) with a different count doesn't match this run, so ignore it
                        #a repeated START with the same count just falls through and gets acknowledged again
                        elif runs[key]["expected"] != count:
                            continue

                        # acknowledge the start(send START_ACK),so the sender can begin transmitting
                        sock.sendto(
                            protocol.pack_control(
                                protocol.start_ack, run_id, flow_id, count
                            ),
                            address,
                        )

                    elif message_type == protocol.end_message: # END: validates the final packet count, records end time , then sends END_ACK.

                        if key not in runs:
                            continue

                        # we remember when the run ended & how many packets need to arrive,so late packets can still arrive 
                        # this count is what makes loss detectable
                        if runs[key]["expected"] != count:
                            continue

                        # only record the end time once, a retried END must not extend the grace period
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

                    continue   # control messages carry no traffic, so skip the data-packet code below and wait for the next datagram

                # decode the data packet using the shared packet format
                try:
                    packet_flow_id, sequence_number, run_id, timestamp, payload = (
                        protocol.unpack_data(packet)
                    )
                except (ValueError, TypeError):
                    continue

                # the flow_id written inside the packet must match the port it arrived on
                if packet_flow_id != flow_id:
                    continue

                key = (run_id, flow_id)
                run = runs.get(key)

                # ignore packets that dont belong to a run we're tracking(for ex: no START seen yet, or the run was already finalised and deleted)
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
                # the first data packet starts the clock for the duration calculation
                if run["started_at"] is None:
                    run["started_at"] = time.monotonic_ns()

                run["sequences"].add(sequence_number)
                run["last_sequence"] = max(
                    run["last_sequence"], sequence_number
                )

                run["payload_bytes"] += len(payload)
                run["latencies_ns"].append(time.monotonic_ns() - timestamp)  # latency = arrival time - the sender's timestamp inside the packet
                # (valid because sender and receiver share one clock, which holds on localhost and inside mininet)

                last_sequence[flow_id] = max(last_sequence[flow_id], sequence_number)

                print(
                    f"received {flow_names[flow_id]} packet {sequence_number} "
                    f"from {address[0]}:{address[1]}"
                )

            # this runs on EVERY loop pass, even when nothing arrived (that is why select has a 0.2s timeout)
            # we then give delayed UDP packets time to arrive before calculating packet loss
            now = time.monotonic()

            for key, run in list(runs.items()):

                # only runs whose END arrived at least grace_period seconds ago are ready
                if (
                    run["ended_at"] is not None
                    and now - run["ended_at"] >= grace_period
                ):
                    run_id, flow_id = key
                    expected = run["expected"]
                    unique = len(run["sequences"])
                    lost = max(0, expected - unique)  # expected (from the sender) - unique packets that really arrived

                    print(
                        f"{flow_names[flow_id]} run {run_id}: "
                        f"expected={expected}, received={unique}, "
                        f"lost={lost}"
                    )

                    if expected > 0:
                        print(f"packet loss: {lost / expected * 100:.2f}%")

                    # calculate the duration from the first data packet to the end message
                    duration_seconds = 0.0  # stays 0.0 if no data packet ever arrived, goodput then becomes 0 too

                    if (
                        run["started_at"] is not None
                        and run["ended_at_ns"] is not None
                    ):
                        duration_seconds = max(
                            0,
                            run["ended_at_ns"] - run["started_at"]
                        ) / 1_000_000_000

                    # now we save the completed run's metrics to the CSV
                    # experiment_id is just the run_id as text, flows that share a run_id count as one experiment
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
                    # same formula metrics.record_run uses,computed here again because that function doesn't return it
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

                    # calculate fairness once at least two flows in the experiment have completed
                    throughputs = completed_throughputs[experiment_id]

                    if (
                        len(throughputs) >= expected_flows
                        and experiment_id not in fairness_recorded
                    ):
                        metrics.record_fairness(experiment_id, throughputs)
                        fairness_recorded.add(experiment_id)

                    # run is done:delete it so late stragglers for it are ignored
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

        # include runs that were interrupted before their normal summary,so nothing was saved to the CSVs for them
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
