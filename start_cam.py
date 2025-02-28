import os
import sys
import ipaddress
 
def ip_is_valid(ip_string):
    try:
        ipaddress.ip_address(ip_string)
        return True
    except:
        return False
 
def port_is_valid(port_string):
    try:
        num = int(port_string)
        return 1 <= num <= 65535
    except:
        return False
    
def video_is_valid(video_string):
    try:
        num = int(video_string)
        return True
    except:
        return False
 
if __name__ == "__main__":
    ip = None
    port = None
    video = None
    
    if len(sys.argv) != 4:
        raise ValueError("Invalid number of argumets. Must inclue \"-ip\", \"-port\" and \"-video\"!")
    
    for arg in sys.argv[1:]:
        if arg.startswith("-ip="):
            ip = arg.removeprefix("-ip=")
            # print("The ip is: " + ip)
        elif arg.startswith("-port="):
            port = arg.removeprefix("-port=")
            # print("The port is: " + port)
        elif arg.startswith("-video="):
            video = arg.removeprefix("-video=")
        else:
            raise ValueError("Invalid argument(s). Must be \"-ip\" or \"-port\" or \"-video\"!")
 
    if not ip_is_valid(ip):
        raise ValueError("Invalid ip argument: " + ip)
 
    if not port_is_valid(port):
        raise ValueError("Invalid port argument: " + port)
        
    if not video_is_valid(video):
        raise ValueError("Invalid video source: " + video)

    os.system(f'gst-launch-1.0 v4l2src device="/dev/video{video}" ! video/x-raw, width=640, height=480 ! videoconvert ! openh264enc bitrate=500000 ! rtph264pay config-interval=1 ! udpsink host={ip} port={port} sync=false async=false')
