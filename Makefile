.PHONY: deps source-build-headed source-build-headless prebuilt-headed prebuilt-headless check

VENV_BIN ?= $(shell if [ -d ".venv" ]; then echo ".venv/bin"; elif [ -d "env" ]; then echo "env/bin"; else echo ".venv/bin"; fi)

run_python_cmd = $(shell $(PY) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

# Use absolute venv bin path to avoid relative-path issues after `cd`
VENV_BIN_ABS := $(abspath $(VENV_BIN))
PIP := $(if $(wildcard $(VENV_BIN_ABS)/pip),$(VENV_BIN_ABS)/pip,pip3)
PY := $(if $(wildcard $(VENV_BIN_ABS)/python),$(VENV_BIN_ABS)/python,python3)

GITHUB_REPO := DefiantBurger/gstreamer-opencv-wheel-builder

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

source-build-headed: deps
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(PIP) uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Building opencv-python from source with GStreamer support..."
	@echo "NOTE: This compiles OpenCV from scratch and may take 15-60+ minutes depending on your CPU."
	$(PIP) install wheel scikit-build numpy
	CMAKE_ARGS="-DWITH_GSTREAMER=ON -DWITH_GTK=ON -DPYTHON3_EXECUTABLE=$(PY)" $(PIP) install --no-cache-dir --no-binary opencv-python opencv-python

source-build-headless: deps
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(PIP) uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Building opencv-python-headless from source with GStreamer support..."
	@echo "NOTE: This compiles OpenCV from scratch and may take 15-60+ minutes depending on your CPU."
	$(PIP) install wheel scikit-build numpy
	CMAKE_ARGS="-DWITH_GSTREAMER=ON -DPYTHON3_EXECUTABLE=$(PY)" $(PIP) install --no-binary opencv-python-headless opencv-python-headless

prebuilt-headed-x86_64:
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(PIP) uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Downloading latest prebuilt opencv-python wheel (x86_64) from GitHub..."
	mkdir -p /tmp/opencv-wheels
	cd /tmp/opencv-wheels && \
	url=$$(curl -s https://api.github.com/repos/$(GITHUB_REPO)/releases/tags/latest-x86_64 \
		| grep 'browser_download_url' \
		| grep '\.whl' \
		| head -n1 \
		| cut -d '"' -f4); \
	if [ -z "$$url" ]; then echo "No wheel asset found for latest-x86_64"; exit 1; fi; \
	wget -q --show-progress "$$url"; \
	$(PIP) install /tmp/opencv-wheels/$$(basename "$$url")

prebuilt-headless-arm64:
	@echo "Uninstalling any existing OpenCV packages from the virtual environment..."
	$(PIP) uninstall -y opencv-python opencv-contrib-python opencv-python-headless || true
	@echo "Downloading latest prebuilt opencv-python-headless wheel (arm64) from GitHub..."
	mkdir -p /tmp/opencv-wheels
	cd /tmp/opencv-wheels && \
	url=$$(curl -s https://api.github.com/repos/$(GITHUB_REPO)/releases/tags/latest-arm64 \
		| grep 'browser_download_url' \
		| grep '\.whl' \
		| head -n1 \
		| cut -d '"' -f4); \
	if [ -z "$$url" ]; then echo "No wheel asset found for latest-arm64"; exit 1; fi; \
	wget -q --show-progress "$$url"; \
	$(PIP) install /tmp/opencv-wheels/$$(basename "$$url")

check:
	@echo "Checking OpenCV GStreamer support..."
	@$(PY) -c "\
	import cv2; \
	import sys; \
	lines = [line for line in cv2.getBuildInformation().split('\n') if 'GStreamer' in line]; \
	has_gstreamer = bool(lines and ('YES' in lines[0].upper() or '1' in lines[0])); \
	print(lines[0].strip() if lines else 'GStreamer info not found'); \
	if has_gstreamer: print('✅ OpenCV was built with GStreamer support.'); sys.exit(0); \
	else: print('❌ OpenCV was NOT built with GStreamer support.'); sys.exit(1)"
