import socket
import time

DISCOVERY_PORT = 5556
BROADCAST_IP = socket.gethostbyname(socket.gethostname())

broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

connection_countdown = 10  # seconds
while connection_countdown > 0:
    broadcast_socket.sendto(f'IP_BROADCASTER:tcp://{BROADCAST_IP}:5555'.encode('utf8'), (BROADCAST_IP, DISCOVERY_PORT))
    time.sleep(1)
    connection_countdown -= 1
