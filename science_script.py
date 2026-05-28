import cv2
import numpy as np
import subprocess
from pathlib import Path


# EXPOSURES = [50, 150, 300]
# EXPOSURES = list(range(50, 625, 50))
EXPOSURES = [11, 83, 250, 500, 830, 5000]
WARMUP_FRAMES = 5


def camera_is_usable(device):
	camera = cv2.VideoCapture(str(device))
	try:
		return camera.isOpened()
	finally:
		camera.release()


def find_cameras():
	devices = sorted(Path("/dev").glob("video*"))
	return [device for device in devices if camera_is_usable(device)]


def set_exposure(device, exposure):
	commands = [
		["v4l2-ctl", "-d", str(device), "-c", "auto_exposure=1", "-c", f"exposure_time_absolute={exposure}"],
		["v4l2-ctl", "-d", str(device), "-c", "auto_exposure=3", "-c", f"exposure_time_absolute={exposure}"],
	]
	last_error = None
	for command in commands:
		result = subprocess.run(command, capture_output=True, text=True)
		if result.returncode == 0:
			return
		last_error = result.stderr.strip() or result.stdout.strip() or "unknown v4l2-ctl error"
	raise RuntimeError(f"Failed to set exposure {exposure} on {device}: {last_error}")


def capture_photo(device):
	camera = cv2.VideoCapture(str(device))
	if not camera.isOpened():
		raise RuntimeError(f"Could not open camera {device}")

	try:
		for _ in range(WARMUP_FRAMES):
			camera.read()

		success, frame = camera.read()
		if not success:
			raise RuntimeError(f"Failed to capture frame from {device}")

		return frame.copy()
	finally:
		camera.release()

def combine_images(images):
	merge_mertens = cv2.createMergeMertens()
	hdr_fusion = merge_mertens.process(images)
	hdr_8bit = np.clip(hdr_fusion * 255, 0, 255).astype('uint8')
	return hdr_8bit

def main():
	photos_dir = Path("photos")
	photos_dir.mkdir(exist_ok=True)

	cameras = find_cameras()
	if not cameras:
		raise RuntimeError("No usable cameras found under /dev/video*")

	resulting_images = []
	for camera in cameras:
		for exposure in EXPOSURES:
			set_exposure(camera, exposure)
			resulting_images.append(capture_photo(camera))
			print(f"Captured photo from {camera} with exposure {exposure}")
	
	# clear photos dir
	for file in photos_dir.glob("*.jpg"):
		file.unlink()

	# Combine images incrementally and save results
	for i in range(len(resulting_images)):
		hdr_image = combine_images(resulting_images[:i+1])
		avg_brightness = np.mean(hdr_image)
		print(f"Combined {i+1} images, average brightness: {avg_brightness:.2f}")
		cv2.imwrite(str(photos_dir / f"hdr_image_{i+1}.jpg"), hdr_image)

if __name__ == "__main__":
	main()