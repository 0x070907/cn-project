
import argparse
import socket
import time
import secrets

import protocol


def main():
    parser = argparse.ArgumentParser(description="udp sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--port", type=int)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--rate", type=float, default=0.1)
    parser.add_argument("--flow", default="high")
    args = parser.parse_args()

    if args.flow not in protocol.flow_ids:
        parser.error("flow must be high, medium, low or background")

    flow_id = protocol.flow_ids[args.flow]
    port = args.port if args.port is not None else protocol.flow_ports[flow_id]

    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")

    if port != protocol.flow_ports[flow_id]:
        parser.error("port does not match the selected flow")

    if args.count < 1:
        parser.error("count must be at least 1")

    if not protocol.data_header_size <= args.size <= 65507:
        parser.error(
            f"size must be between {protocol.data_header_size} and 65507 bytes"
        )

    if args.rate <= 0:
        parser.error("rate must be greater than 0")

    data = b"x" * (args.size - protocol.data_header_size)

    # rate is in mbps so we need to convert it into bits per s
    # interval = ratio of the bits per packet and the bits sent per sec
    interval = (args.size * 8) / (args.rate * 1_000_000)
    interval_ns = int(interval * 1_000_000_000)

    run_id = args.run_id if args.run_id is not None else secrets.randbits(64) # this preserves our old behaviour when we dont specify --run-id on cli
    server = (args.ip, port)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)

    def send_control(message_type, expected_ack):
        message = protocol.pack_control(
            message_type, run_id, flow_id, args.count
        )

        # retry control messages in case an acknowledgement is lost
        for attempt in range(5):
            sock.sendto(message, server)

            try:
                response, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue

            control = protocol.unpack_control(response)

            if (
                addr == server
                and control is not None
                and control[0] == expected_ack
                and control[1] == run_id
                and control[2] == flow_id
                and control[3] == args.count
            ):
                return True

        return False

    try:
        if not send_control(protocol.start_message, protocol.start_ack):
            print("receiver did not acknowledge run start")
            return

        print(f"started run {run_id} for {args.flow}")

        start_time = time.monotonic_ns()

        for i in range(args.count):
            timestamp = time.monotonic_ns()

            packet = protocol.pack_data(
                flow_id, i, run_id, timestamp, data
            )

            sock.sendto(packet, server)
            print(f"sent {args.flow} packet {i}")

            # schedule packets at the requested approximate rate
            next_send = start_time + (i + 1) * interval_ns
            delay = (next_send - time.monotonic_ns()) / 1_000_000_000

            if delay > 0 and i + 1 < args.count:
                time.sleep(delay)

        if send_control(protocol.end_message, protocol.end_ack):
            print(f"finished run {run_id}")
        else:
            print("warning: receiver did not acknowledge run completion")

        print(f"sent {args.count} packets to {args.ip}:{port}")
        print(f"packet size: {args.size} bytes")
        print(f"target rate: {args.rate} mbps")

    except OSError as error:
        print(f"failed to send packets: {error}")

    except KeyboardInterrupt:
        print("\nstopping sender")

    finally:
        sock.close()


if __name__ == "__main__":
    main()

