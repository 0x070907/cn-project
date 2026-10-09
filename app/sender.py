
import argparse
import socket
import time
import struct

def main():
    parser = argparse.ArgumentParser(description="udp sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--rate", type=float, default=0.1)
    parser.add_argument("--flow", default="high")
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    if args.count < 1:
        parser.error("count must be at least 1")

    # the metadata header takes 20 bytes
    if not 20 <= args.size <= 65507:
        parser.error("size must be between 20 and 65507 bytes")

    if args.rate <= 0:
        parser.error("rate must be greater than 0")

    flow_ids = {"high": 1, "medium": 2, "low": 3, "background": 4} # assign each flow a num id for packet header

    if args.flow not in flow_ids:
        parser.error("flow must be high, medium, low or background")

    flow_id = flow_ids[args.flow]

    header_format = "!IQq"  # ! -> network byte order(big-endian), I: uint_32 for flowid, Q: uint_64 for seqno and q: 8 byte signed int for timestamp
    header_size = struct.calcsize(header_format)

    data_size = args.size - header_size
    data = b"x" * data_size

    # rate is in mbps so we need to convert it into bits per s
    # interval = ratio of the bits per packet and the bits sent per sec
    interval = (args.size * 8) / (args.rate * 1_000_000)
    interval_ns = int(interval * 1_000_000_000)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        start_time = time.monotonic_ns() # we use a monotonic clock to schedule packets without depending on changes to the system clock

        for i in range(args.count):
            timestamp = time.monotonic_ns()
            header = struct.pack(header_format, flow_id, i, timestamp)
            packet = header + data

            sock.sendto(packet, (args.ip, args.port))
            print(f"sent {args.flow} packet {i}")

            # schedule packets at the requested approximate rate
            next_send = start_time + (i + 1) * interval_ns
            delay = (next_send - time.monotonic_ns()) / 1_000_000_000

            if delay > 0 and i + 1 < args.count:
                time.sleep(delay)

        print(f"sent {args.count} packets to {args.ip}:{args.port}")
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

