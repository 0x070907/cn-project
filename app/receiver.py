import socket

host = "0.0.0.0"
port = 5001

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((host, port))

print(f"listening on {host}:{port}")

try:
    while True:
        # wai for the next udp datagram
        data, addr = sock.recvfrom(65535)
        print(f"received {len(data)} bytes from {addr}: {data!r}")

except KeyboardInterrupt:
    print("\nstopping receiver")

finally:
    sock.close()
