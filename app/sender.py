import socket

server_host = "127.0.0.1"
server_port = 5001
message = b"hello from udp client"

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

try:
    for _ in range(10):
        sock.sendto(message, (server_host, server_port))
        print("sent packet")

except KeyboardInterrupt:
    print("\nstopping sender")

finally:
    sock.close()
