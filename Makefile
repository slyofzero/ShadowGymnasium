# ==============================================================================
# Makefile — Shadow Fight 2 (Cat Blasters 9k) Automation Harness
# ==============================================================================
# Usage:
#   make install    - Checks for installation; installs APK if missing
#   make boot       - Checks for installation; boots the game if available
#   make reinstall  - Force re-installs the APK even if already present
#   make restart    - Force stops and reboots the game
#   make screenshot - Captures an instant sub-second BlueStacks screenshot
#   make all        - Runs install, then boots the game
# ==============================================================================

# Select virtualenv python if available, otherwise system python
PYTHON := .venv/Scripts/python.exe
ifeq ($(wildcard $(PYTHON)),)
    PYTHON := python
endif

.PHONY: all install boot reinstall restart screenshot status help frida frida-watch actions telemetry engine tick

# Default target: check/install and boot
all: install boot

## install: Check if package is installed; install APK if not found
install:
	@$(PYTHON) scripts/install.py

## boot: Check if package is installed; boot the app if available
boot:
	@$(PYTHON) scripts/boot.py

## reinstall: Force reinstallation of the APK
reinstall:
	@$(PYTHON) scripts/install.py --force

## restart: Force-stop and reboot the app
restart:
	@$(PYTHON) scripts/boot.py --restart

## screenshot: Capture instant ADB screenshot to artifact directory
screenshot:
	@$(PYTHON) .agents/skills/bluestacks-instant-screenshot/scripts/screenshot.py --artifact current_screen

## status: Check device connection, installation state, and running process
status:
	@$(PYTHON) -c "from scripts.start_frida_service import find_adb, get_connected_device, is_app_running, DEFAULT_PACKAGE; adb = find_adb(); dev = get_connected_device(adb); run, pid = is_app_running(adb, dev, DEFAULT_PACKAGE) if dev else (False, None); print(f'ADB Device : {dev}'); print(f'App Running: {run} (PID: {pid})')"

## frida: Verify and establish Frida bridge and port forward
frida:
	@$(PYTHON) scripts/start_frida_service.py

## frida-watch: Run continuous Frida watchdog daemon
frida-watch:
	@$(PYTHON) scripts/start_frida_service.py --watch

## actions: Execute in-engine game actions (make actions ACTION=status)
actions:
	@$(PYTHON) scripts/game_actions.py $(ACTION)

## telemetry: Stream live JSON telemetry
telemetry:
	@$(PYTHON) scripts/stream_telemetry.py

## engine: Launch combat engine controller
engine:
	@$(PYTHON) scripts/engine_controller.py

## tick: Master simulation timing controller (make tick CMD=status)
tick:
	@$(PYTHON) scripts/tick_controller.py $(CMD)

## help: Display this help message
help:
	@echo "Shadow Fight 2 Automation Makefile"
	@echo "=================================="
	@echo "Targets:"
	@echo "  make frida       - Verify and establish Frida bridge & port forwarding"
	@echo "  make frida-watch - Run continuous Frida connection watchdog daemon"
	@echo "  make screenshot  - Instant sub-second screen capture to artifacts"
	@echo "  make status      - Check device, installation, and running status"
	@echo "  make actions     - In-engine combat flow (e.g. make actions ACTION=status)"
	@echo "  make telemetry   - Live 60Hz JSON state streamer"
	@echo "  make engine      - Interactive combat action controller"
	@echo "  make tick        - Simulation timing controller (e.g. make tick CMD=status)"

