"""UDP sender: generates ONE flow per process."""

# sequence of a run: START (retried until acked) -> N data packets paced at --rate (never retried) -> END (retried until acked)
# to make an experiment with 4 flows, start 4 senders that all use the SAME --run-id.

from __future__ import annotations

import argparse
import socket
import time
import secrets

import protocol


class Args(argparse.Namespace):
    # typed view of the command line arguments defined in main()
    ip: str
    run_id: int | None
    port: int | None
    count: int
    size: int
    rate: float
    flow: str


def main() -> None:
    parser = argparse.ArgumentParser(description="udp sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--port", type=int)  #tho there's no default value,it fallbacks to 5001 itself
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--rate", type=float, default=0.1)
    parser.add_argument("--flow", default="high")
    args = parser.parse_args(namespace=Args())

    if args.flow not in protocol.flow_ids:
        parser.error("flow must be high, medium, low or background")

    flow_id: int = protocol.flow_ids[args.flow]
    port: int = args.port if args.port is not None else protocol.flow_ports[flow_id]

    if not 1 <= port <= 65535:
        parser.error("port must be between 1 and 65535")

    # the receiver identifies the flow by port, so the port and flow name must agree
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

    # --size is the whole datagram, so the filler is the size minus the 28-byte header
    data: bytes = b"x" * (args.size - protocol.data_header_size)

    # rate is in mbps so we need to convert it into bits per s
    # interval = ratio of the bits per packet and the bits sent per sec
    interval: float = (args.size * 8) / (args.rate * 1_000_000)
    interval_ns: int = int(interval * 1_000_000_000)

    run_id: int = args.run_id if args.run_id is not None else secrets.randbits(64) # this preserves our old behaviour when we dont specify --run-id on cli
    server: tuple[str, int] = (args.ip, port)

    sock: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)


    # sends a START or END and waits for the matching ACK, resending up to 5 times (0.5s timeout each)
    # control messages are retried because losing one would ruin the run
    # data packets are NOT retried since loss is what we measure
    def send_control(message_type: int, expected_ack: int) -> bool:
        
        message: bytes = protocol.pack_control(
            message_type, run_id, flow_id, args.count
        )

        # retry control messages in case an acknowledgement is lost
        for attempt in range(5):
            sock.sendto(message, server)

            try:
                response, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue

            control: tuple[int, int, int, int] | None = protocol.unpack_control(response)

            # only accept an ACK that came from the receiver and matches this run, flow and count
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

        # the pacing clock starts only after START is acknowledged
        start_time: int = time.monotonic_ns()

        for i in range(args.count):
            timestamp: int = time.monotonic_ns()

            packet: bytes = protocol.pack_data(
                flow_id, i, run_id, timestamp, data
            )

            sock.sendto(packet, server)
            print(f"sent {args.flow} packet {i}")

            # schedule packets at the requested approximate rate
            # rate is used to calculate interval,which is helping in the pace control
            next_send: int = start_time + (i + 1) * interval_ns
            delay: float = (next_send - time.monotonic_ns()) / 1_000_000_000

            if delay > 0 and i + 1 < args.count:
                time.sleep(delay) # responsible for gap b/w packets

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

