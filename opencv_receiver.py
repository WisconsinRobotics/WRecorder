import cv2
import zmq
import base64
import numpy as np

context = zmq.Context()
footage_socket = context.socket(zmq.SUB)
footage_socket.connect('tcp://localhost:5555')
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
