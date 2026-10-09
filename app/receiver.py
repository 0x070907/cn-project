
import select
import socket
import struct

host = "0.0.0.0"
ports = {   # our convention
    5001: "high",
    5002: "medium",
    5003: "low",
    5004: "background"
}

header_format = "!IQq"  # same format 
header_size = struct.calcsize(header_format)

flow_ids = {
    "high": 1,
    "medium": 2,
    "low": 3,
    "background": 4
}

# we keep track of packets separately for each flow
received = {flow_id: set() for flow_id in flow_ids.values()}
duplicates = {flow_id: 0 for flow_id in flow_ids.values()}
out_of_order = {flow_id: 0 for flow_id in flow_ids.values()}
last_sequence = {}

sockets = {}

try:
    # create socket for each traffic class
    for port, flow_name in ports.items():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        sockets[sock] = port
        print(f"listening for {flow_name} traffic on {host}:{port}")

    while True:
        # we wait until at least one socket has a packet ready
        ready, _, _ = select.select(list(sockets), [], [])

        for sock in ready:
            packet, addr = sock.recvfrom(65535)
            port = sockets[sock]

            # we then ignore packets that are too small to contain the header
            if len(packet) < header_size:
                print(f"ignoring invalid packet from {addr}")
                continue

            try:
                flow_id, sequence_number, timestamp = struct.unpack(
                    header_format, packet[:header_size]
                )
            except struct.error:
                print(f"ignoring invalid packet from {addr}")
                continue

            flow_name = ports[port]
            expected_flow_id = flow_ids[flow_name]

            # make sure the flow id matches the destination port
            if flow_id != expected_flow_id:
                print(f"ignoring mismatched flow id {flow_id} on port {port}")
                continue

            # track unique packets and detect duplicates or reordering
            seen=received[flow_id]

            if sequence_number in seen:
                duplicates[flow_id] += 1
                print(f"{flow_name}: duplicate packet {sequence_number}")
                continue

            if flow_id in last_sequence:
                if sequence_number < last_sequence[flow_id]:
                    out_of_order[flow_id] += 1

            seen.add(sequence_number)
            last_sequence[flow_id] = max(
                sequence_number, last_sequence.get(flow_id, -1)
            )

            print(
                f"received {flow_name} packet {sequence_number} "
                f"({len(packet)} bytes) from {addr}"
            )

except KeyboardInterrupt:
    print("\nstopping receiver")

except OSError as error:
    print(f"receiver error: {error}")

finally:
    for sock in sockets:
        sock.close()

    print("\nreceiver summary:")

    for port, flow_name in ports.items():
        flow_id = flow_ids[flow_name]
        print(f"{flow_name} (port {port}):")
        print(f"  unique packets: {len(received[flow_id])}")
        print(f"  duplicates: {duplicates[flow_id]}")
        print(f"  out of order: {out_of_order[flow_id]}")

