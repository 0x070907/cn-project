
import argparse
import socket


def main():
    parser = argparse.ArgumentParser(description="udp sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    if args.count < 1:
        parser.error("count must be at least 1")

    message = b"hello from udp client"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        for _ in range(args.count):
            sock.sendto(message, (args.ip, args.port))
            print("sent packet")

        print(f"sent {args.count} packets to {args.ip}:{args.port}")

    except OSError as error:
        print(f"failed to send packets: {error}")

    except KeyboardInterrupt:
        print("\nstopping sender")

    finally:
        sock.close()


if __name__ == "__main__":
    main()

