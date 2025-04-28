import socket

DISCOVERY_PORT = 5556

receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
receiver_socket.bind(('', DISCOVERY_PORT))
receiver_socket.settimeout(30)

DISCOVERED_IP = None

try:
    while True:
        data, addr = receiver_socket.recvfrom(1024)
        DISCOVERED_IP = data.decode().split('//')[-1].split(':')[0]
        break
        # parse and connect to the publisher
except socket.timeout:
    print("No broadcaster found.")