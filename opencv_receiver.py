import cv2
import zmq
import base64
import numpy as np
import socket

BROADCASTER_IP = None

def receive_ip(receiving_time_limit=30):
    global BROADCASTER_IP
    DISCOVERY_PORT = 5556

    receiver_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver_socket.bind(('', DISCOVERY_PORT))
    receiver_socket.settimeout(receiving_time_limit)

    try:
        while True:
            data, addr = receiver_socket.recvfrom(1024)
            BROADCASTER_IP = data.decode().split('//')[-1].split(':')[0]
            break
    except socket.timeout:
        print("No broadcaster found.")

def receive_camera_data():
    context = zmq.Context()
    footage_socket = context.socket(zmq.SUB)
    footage_socket.connect(f'tcp://{BROADCASTER_IP}:5555')
    footage_socket.setsockopt_string(zmq.SUBSCRIBE, str(''))

    print("Receiving data on port 5555...")
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
    print("Receiving IP for 10 seconds...")
    receive_ip(10)
    if BROADCASTER_IP:
        print(f"Found broadcaster at {BROADCASTER_IP}")
        print("Starting camera stream...")
        receive_camera_data()
    else:
        print("No broadcaster found.")