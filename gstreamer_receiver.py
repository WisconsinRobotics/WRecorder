import os
import sys

def port_is_valid(port_string):
    try:
        num = int(port_string)
        return 1 <= num <= 65535
    except:
        return False
    
if __name__ == "__main__":
    port = None

    if len(sys.argv) != 2:
        raise ValueError("Invalid number of arguments. Must include \"-port\"!")

    for arg in sys.argv[1:]:
        if arg.startswith("-port="):
            port = arg.removeprefix("-port=")
        else:
            raise ValueError("Invalid argument(s). Must be \"-port\"!")
    
    if not port_is_valid(port):
        raise ValueError("Invalid port argument: " + port)

    os.system(f'gst-launch-1.0 -vvv udpsrc port={port} ! application/x-rtp,encoding-name=H264,payload=96 ! rtph264depay ! queue ! avdec_h264 ! autovideosink sync=false -e')