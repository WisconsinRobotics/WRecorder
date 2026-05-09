.PHONY: setup setup-headed setup-headless

setup-headed:
	@echo "Installing GStreamer and system dependencies (headed)..."
	sudo apt-get update
	sudo apt-get install -y build-essential cmake pkg-config \
		libgtk2.0-dev libgtk-3-dev qtbase5-dev \
		libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
		libgstreamer-plugins-bad1.0-dev gstreamer1.0-plugins-base \
		gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
		gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools \
		gstreamer1.0-x gstreamer1.0-alsa gstreamer1.0-gl gstreamer1.0-gtk3 \
		gstreamer1.0-qt5 gstreamer1.0-pulseaudio python3-dev \
		python3-gst-1.0 gir1.2-gstreamer-1.0 libgirepository1.0-dev

	@echo "Creating virtual environment..."
	python3.13 -m venv .venv

	@echo "Installing Python dependencies..."
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r headed_requirements.txt

	@echo "Setup complete! To activate the virtual environment, run: source .venv/bin/activate"

setup-headless:
	@echo "Installing GStreamer and system dependencies (headless)..."
	sudo apt-get update
	sudo apt-get install -y build-essential cmake pkg-config \
		libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
		libgstreamer-plugins-bad1.0-dev gstreamer1.0-plugins-base \
		gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
		gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools \
		gstreamer1.0-pulseaudio python3-dev \
		python3-gst-1.0 gir1.2-gstreamer-1.0 libgirepository1.0-dev

	@echo "Creating virtual environment..."
	python3.13 -m venv .venv

	@echo "Installing Python dependencies..."
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r headless_requirements.txt

	@echo "Setup complete! To activate the virtual environment, run: source .venv/bin/activate"

setup: setup-headed
