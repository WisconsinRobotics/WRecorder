import cv2
import zmq
import base64
import numpy as np
import socket
import argparse

def receive_ip(discovery_port, discovery_timeout):
    receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_socket.bind(('',discovery_port))
    receiver_socket.settimeout(discovery_timeout)

    try:
        while True:
            data, addr = receiver_socket.recvfrom(1024)
            return data.decode().split('//')[-1].split(':')[0]
    except socket.timeout:
        print("No broadcaster found.")

def receive_camera_data(ip, port):
    context = zmq.Context()
    footage_socket = context.socket(zmq.SUB)
    footage_socket.connect(f'tcp://{ip}:{port}')
    footage_socket.setsockopt_string(zmq.SUBSCRIBE, str(''))

    print(f"Receiving data on port {port}...")
    print("Press 'q' to quit")

    while True:
        try:
            frame = footage_socket.recv_string()
            img = base64.b64decode(frame)
            npimg = np.fromstring(img, dtype=np.uint8)
            source = cv2.imdecode(npimg, 1)
            cv2.imshow("Stream", source)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

        except KeyboardInterrupt:
            cv2.destroyAllWindows()
            break

if __name__ == "__main__":
    # Necessary arguments:
    # - auto-ip-discovery
    # - discovery-port
    # - discovery-timeout
    # - broadcast-port
    parser = argparse.ArgumentParser(prog='opencv_receiver', description='Receiving camera data using opencv2')
    parser.add_argument('--auto-ip-discovery', default="off")
    parser.add_argument('--discovery-port', type=int, default=5556)
    parser.add_argument('--discovery-timeout', type=int, default=15)
    parser.add_argument('--broadcast-port', type=int, default=5555)

    args = parser.parse_args()
    auto_ip_discovery = args.auto_ip_discovery == "on"
    discovery_port = args.discovery_port
    discovery_timeout = args.discovery_timeout
    broadcast_port = args.broadcast_port

    broadcast_ip = None
    if (auto_ip_discovery):
        print(f"Receiving IP for {discovery_timeout} seconds...")
        broadcast_ip = receive_ip(discovery_port, discovery_timeout)
        if broadcast_ip:
            print(f"Found broadcaster at {broadcast_ip}!")
        else:
            raise RuntimeError("No broadcasting IP found!")
    print("Starting camera stream...")
    receive_camera_data(broadcast_ip, broadcast_port)