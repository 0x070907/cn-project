
import argparse
import socket
import time

def main():
    parser = argparse.ArgumentParser(description="udp sender")
    parser.add_argument("--ip", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--rate", type=float, default=0.1)
    args = parser.parse_args()

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")

    if args.count < 1:
        parser.error("count must be at least 1")

    if not 1 <= args.size <= 65507:
        parser.error("size must be between 1 and 65507 bytes")

    if args.rate <= 0:
        parser.error("rate must be greater than 0")
    
    # we pad the msg so that every packet has the requested payload size
    message = b"hello from udp client"
    message=message.ljust(args.size, b"x")

    # rate is in mbps so we need to convert it into bits per s 
    # interval = ratio of the bits per packet and the bits sent per sec
    interval= (args.size*8)/(args.rate * 1_000_000)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        start_time= time.monotonic() # we use a monotonic clock to schedule packets without depending on changes to the system clock
        for i in range(args.count):
            sock.sendto(message, (args.ip, args.port))
            printf(f"sent packet {i+1}/{args.count}")

            next_send= start_time+(i+1)*interval # calc when this packet's sending interval ends
            delay = next_send - time.monotonic() 

            if delay>0 and i+1<args.count: # wait if we are ahead of target schedule
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

