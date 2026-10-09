import socket
import select
import time

import protocol


def main():
    ports = protocol.flow_ports
    sockets = {}
    runs = {}
    grace_period = 1.0

    # map flow IDs back to their names for printing
    flow_names = {
        flow_id: name for name, flow_id in protocol.flow_ids.items()
    }

    # keep track of packets received across all runs for the final summary
    received = {flow_id: 0 for flow_id in ports}
    duplicates = {flow_id: 0 for flow_id in ports}
    out_of_order = {flow_id: 0 for flow_id in ports}
    last_sequence = {flow_id: -1 for flow_id in ports}

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
                                "ended_at": None,
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
                        runs[key]["ended_at"] = now

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
                    print(f"{flow_names[flow_id]}: duplicate packet {sequence_number}")
                    continue

                # a sequence number smaller than the previous highest means reordering
                if sequence_number < run["last_sequence"]:
                    out_of_order[flow_id] += 1

                run["sequences"].add(sequence_number)
                run["last_sequence"] = max(
                    run["last_sequence"], sequence_number
                )

                last_sequence[flow_id] = max(
                    last_sequence[flow_id], sequence_number
                )

                print(
                    f"received {flow_names[flow_id]} packet {sequence_number} "
                    f"from {address[0]}:{address[1]}"
                )

            # give delayed UDP packets time to arrive before calculating packet loss
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

