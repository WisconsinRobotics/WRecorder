.PHONY: deps install-headed install-headless clean check

VENV_BIN ?= $(shell if [ -d ".venv" ]; then echo ".venv/bin"; elif [ -d "env" ]; then echo "env/bin"; else echo ".venv/bin"; fi)

run_python_cmd = $(shell $(VENV_BIN)/python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

deps:
	@echo "Installing GStreamer and OpenCV build dependencies..."
	sudo apt-get update
	sudo apt-get install -y build-essential cmake pkg-config \
		libgtk2.0-dev libgtk-3-dev qtbase5-dev \
		libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
		libgstreamer-plugins-bad1.0-dev gstreamer1.0-plugins-base \
		gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
		gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools \
		gstreamer1.0-x gstreamer1.0-alsa gstreamer1.0-gl gstreamer1.0-gtk3 \
		gstreamer1.0-qt5 gstreamer1.0-pulseaudio python3-dev python$(run_python_cmd)-dev

install-headed: deps
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(VENV_BIN)/pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Building opencv-python from source with GStreamer support..."
	@echo "NOTE: This compiles OpenCV from scratch and may take 15-60+ minutes depending on your CPU."
	$(VENV_BIN)/pip install wheel scikit-build numpy
	CMAKE_ARGS="-DWITH_GSTREAMER=ON -DWITH_GTK=ON -DPYTHON3_EXECUTABLE=$$(pwd)/$(VENV_BIN)/python" $(VENV_BIN)/pip install --no-cache-dir --no-binary opencv-python opencv-python

install-headless: deps
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(VENV_BIN)/pip uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Building opencv-python-headless from source with GStreamer support..."
	@echo "NOTE: This compiles OpenCV from scratch and may take 15-60+ minutes depending on your CPU."
	$(VENV_BIN)/pip install wheel scikit-build numpy
	CMAKE_ARGS="-DWITH_GSTREAMER=ON -DPYTHON3_EXECUTABLE=$$(pwd)/$(VENV_BIN)/python" $(VENV_BIN)/pip install --no-binary opencv-python-headless opencv-python-headless

check:
	@echo "Checking OpenCV GStreamer support..."
	@$(VENV_BIN)/python -c "\
	import cv2; \
	import sys; \
	lines = [line for line in cv2.getBuildInformation().split('\n') if 'GStreamer' in line]; \
	has_gstreamer = bool(lines and ('YES' in lines[0].upper() or '1' in lines[0])); \
	print(lines[0].strip() if lines else 'GStreamer info not found'); \
	if has_gstreamer: print('✅ OpenCV was built with GStreamer support.'); sys.exit(0); \
	else: print('❌ OpenCV was NOT built with GStreamer support.'); sys.exit(1)"
