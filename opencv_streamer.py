import base64
import cv2
import zmq
import socket
import time

def broadcast_ip(broadcast_time_limit=10):
    DISCOVERY_PORT = 5556
    BROADCAST_IP = socket.gethostbyname(socket.gethostname())

    broadcast_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    broadcast_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    connection_countdown = broadcast_time_limit  # seconds
    while connection_countdown > 0:
        broadcast_socket.sendto(f'IP_BROADCASTER:tcp://{BROADCAST_IP}:5555'.encode('utf8'), (BROADCAST_IP, DISCOVERY_PORT))
        time.sleep(1)
        connection_countdown -= 1

def broadcast_camera_data():
    context = zmq.Context()
    footage_socket = context.socket(zmq.PUB)
    footage_socket.bind('tcp://*:5555') # 172.20.10.3

    print("Streaming data on port 5555...")

    camera = cv2.VideoCapture(0)  # init the camera
    camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 640)

    while True:
        try:
            grabbed, frame = camera.read()  # grab the current frame
            # frame = cv2.resize(frame, (640, 480))  # resize the frame
            encoded, buffer = cv2.imencode('.jpg', frame)
            jpg_as_text = base64.b64encode(buffer)
            footage_socket.send(jpg_as_text)

        except KeyboardInterrupt:
            camera.release()
            cv2.destroyAllWindows()
            break

if __name__ == "__main__":
    print("Broadcasting IP for 10 seconds...")
    broadcast_ip(10)
    print("Starting camera stream...")
    broadcast_camera_data()