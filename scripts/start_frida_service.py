#!/usr/bin/env python3
r"""
Shadow Fight 2 — Frida Bridge & Service Manager.

Ensures that the Frida server / Frida Gadget bridge is active, connected, and
ready for in-engine automation scripts:
- scripts/game_actions.py      (start fight, pause, resume, exit)
- scripts/engine_controller.py (punches, kicks, directional movement)
- scripts/tick_controller.py   (simulation freeze, Nx speed, frame stepping)
- scripts/stream_telemetry.py  (live 60Hz HP, vectors, badges, round telemetry)

Usage:
    One-shot check & setup (verifies ADB, sets port forward, pings Frida):
        python scripts/start_frida_service.py

    Auto-boot the game if it is not running:
        python scripts/start_frida_service.py --boot

    Continuous Watchdog / Daemon Mode (keeps bridge alive across restarts):
        python scripts/start_frida_service.py --watch

    Or double-click: start_frida.bat
"""

import argparse
import os
import shutil
import subprocess
import sys
import time

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import frida
except ImportError:
    frida = None

DEFAULT_PORT = int(os.environ.get("FRIDA_PORT", "27042"))
DEFAULT_PACKAGE = "com.nekki.catblasters"
DEFAULT_ADB_TARGET = os.environ.get("ADB_CONNECT", "127.0.0.1:5555")

WINDOWS_ADB_PATHS = [
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files (x86)\BlueStacks_nxt\HD-Adb.exe",
]


def find_adb() -> str:
    """Locates ADB executable across OS environments and BlueStacks defaults."""
    env_adb = os.environ.get("ADB_PATH") or os.environ.get("ADB_BIN")
    if env_adb and os.path.exists(env_adb):
        return env_adb

    path_adb = shutil.which("adb")
    if path_adb:
        return path_adb

    sdk_root = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if sdk_root:
        candidate = os.path.join(sdk_root, "platform-tools", "adb.exe" if sys.platform == "win32" else "adb")
        if os.path.exists(candidate):
            return candidate

    if sys.platform == "win32":
        for p in WINDOWS_ADB_PATHS:
            if os.path.exists(p):
                return p

    return "adb"


def get_connected_device(adb: str) -> str | None:
    """Finds an attached online device serial, connecting to 127.0.0.1:5555 if needed."""
    env_serial = os.environ.get("ANDROID_SERIAL") or os.environ.get("ADB_DEVICE")
    if env_serial:
        return env_serial

    try:
        res = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        lines = res.stdout.strip().splitlines()
        devices = []
        for line in lines[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])

        if devices:
            return devices[0]

        # Try connecting to default BlueStacks port
        subprocess.run([adb, "connect", DEFAULT_ADB_TARGET], capture_output=True, text=True, timeout=5)
        res = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
    except Exception:
        pass

    return None


def is_app_running(adb: str, device: str, package: str = DEFAULT_PACKAGE) -> tuple[bool, str | None]:
    """Checks if the game process is running and returns its PID."""
    try:
        cmd = [adb, "-s", device, "shell", f"pidof {package}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        pid = res.stdout.strip()
        if pid and pid.isdigit():
            return True, pid

        # Fallback to ps inspection
        cmd = [adb, "-s", device, "shell", "ps -A"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        for line in res.stdout.splitlines():
            if package in line:
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    return True, parts[1]
    except Exception:
        pass
    return False, None


def boot_app(adb: str, device: str, package: str = DEFAULT_PACKAGE) -> bool:
    """Launches the target app via monkey launcher intent."""
    try:
        cmd = [adb, "-s", device, "shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
        subprocess.run(cmd, capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def ensure_frida_server(adb: str, device: str) -> bool:
    """Ensures frida-server is running as root on port 27042 if available on device."""
    try:
        res = subprocess.run([adb, "-s", device, "shell", "pgrep -l frida"], capture_output=True, text=True, timeout=3)
        if "frida" in res.stdout:
            return True

        # Check if frida-server binary is present on device
        check = subprocess.run([adb, "-s", device, "shell", "test -f /data/local/tmp/frida-server && echo 1"], capture_output=True, text=True, timeout=3)
        if "1" in check.stdout:
            subprocess.run([adb, "-s", device, "root"], capture_output=True, timeout=3)
            time.sleep(0.5)
            subprocess.run([adb, "-s", device, "shell", "nohup /data/local/tmp/frida-server -l 0.0.0.0:27042 >/dev/null 2>&1 &"], capture_output=True, timeout=3)
            time.sleep(1.0)
            return True
    except Exception:
        pass
    return False


def setup_port_forward(adb: str, device: str, port: int = DEFAULT_PORT) -> bool:
    """Sets up ADB TCP port forwarding for the Frida server / gadget."""
    try:
        cmd = [adb, "-s", device, "forward", f"tcp:{port}", f"tcp:{port}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return True
        list_cmd = [adb, "-s", device, "forward", "--list"]
        list_res = subprocess.run(list_cmd, capture_output=True, text=True, timeout=5)
        if f"tcp:{port}" in list_res.stdout:
            return True
        # In Docker / ReDroid setups, host port 27042 is already published directly
        return True
    except Exception:
        return False


def test_frida_connection(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    timeout_sec: float = 3.0,
) -> tuple[bool, str, int | None]:
    """Pings Frida on the target host/port and retrieves process / gadget info."""
    if frida is None:
        return False, "frida Python package not installed (run uv pip install frida)", None

    if host is None:
        host = os.environ.get("FRIDA_HOST", "127.0.0.1")

    start_time = time.time()
    last_err = ""
    while time.time() - start_time < timeout_sec:
        try:
            device_manager = frida.get_device_manager()  # type: ignore[attr-defined]
            remote_dev = device_manager.add_remote_device(f"{host}:{port}")
            procs = remote_dev.enumerate_processes()
            if procs:
                p = procs[0]
                return True, f"{p.name} (PID: {p.pid})", p.pid
            return True, f"Remote Frida device active ({remote_dev.name})", None
        except Exception as e:
            last_err = str(e)
            time.sleep(0.5)

    return False, last_err, None


def ensure_frida_bridge(
    host: str | None = None,
    port: int = DEFAULT_PORT,
    auto_boot: bool = True,
    package: str = DEFAULT_PACKAGE,
    verbose: bool = True,
) -> bool:
    """
    Checks if Frida connection server/gadget is online.
    If not, automatically finds ADB, resolves device, boots the game if needed,
    sets up port forwarding, and verifies connection.
    """
    if host is None:
        host = os.environ.get("FRIDA_HOST", "127.0.0.1")

    # 1. Quick probe: is Frida already responding?
    online, info, _ = test_frida_connection(host=host, port=port, timeout_sec=1.5)
    if online:
        if verbose:
            print(f"[Frida] Service is already ONLINE on {host}:{port} ({info})")
        return True

    if verbose:
        print(f"[Frida] Service is not responding on {host}:{port}. Initializing bridge...")

    # 2. Find ADB & Device
    adb = find_adb()
    device = get_connected_device(adb)
    if not device:
        adb_target = os.environ.get("ADB_CONNECT", DEFAULT_ADB_TARGET)
        if verbose:
            print(f"[Frida] Connecting to ADB target {adb_target}...")
        subprocess.run([adb, "connect", adb_target], capture_output=True, timeout=5)
        device = get_connected_device(adb)

    if not device:
        if verbose:
            print("[Frida ERROR] No active Android device found via ADB.", file=sys.stderr)
        return False

    # 3. Check and optionally boot the game
    running, pid = is_app_running(adb, device, package)
    if not running and auto_boot:
        if verbose:
            print(f"[Frida] Game '{package}' is not running. Booting now...")
        boot_app(adb, device, package)
        time.sleep(5)
        running, pid = is_app_running(adb, device, package)

    # 4. Ensure frida-server is running if available on device
    ensure_frida_server(adb, device)

    # 5. Port forward if not direct mode
    if os.environ.get("FRIDA_DIRECT") != "1":
        setup_port_forward(adb, device, port)

    # 5. Final verification check with retry
    online, info, _ = test_frida_connection(host=host, port=port, timeout_sec=5.0)
    if online:
        if verbose:
            print(f"[Frida] Successfully connected to {host}:{port}: {info}")
        return True
    else:
        if verbose:
            print(f"[Frida ERROR] Could not establish connection to {host}:{port}: {info}", file=sys.stderr)
        return False


def run_check(adb: str, port: int, auto_boot: bool = False) -> bool:
    """Performs a complete diagnostic and setup cycle."""
    print("=" * 65)
    print(" Shadow Fight 2 - Frida Service & Bridge Manager")
    print("=" * 65)

    # 1. Discover ADB & Device
    print(f"[*] ADB Binary      : {adb}")
    device = get_connected_device(adb)
    if not device:
        print("[!] No active Android devices found.")
        print(f"[*] Attempting ADB connect to {DEFAULT_ADB_TARGET}...")
        subprocess.run([adb, "connect", DEFAULT_ADB_TARGET], capture_output=True, timeout=5)
        device = get_connected_device(adb)

    if not device:
        print("[ERROR] Could not connect to BlueStacks or Android device.", file=sys.stderr)
        print("        Ensure BlueStacks is open with ADB enabled (Settings -> Advanced -> ADB ON).", file=sys.stderr)
        return False

    print(f"[OK] Android Device  : {device}")

    # 2. Check Game Running
    running, pid = is_app_running(adb, device, DEFAULT_PACKAGE)
    if not running:
        if auto_boot:
            print(f"[*] Game '{DEFAULT_PACKAGE}' is NOT running. Booting now...")
            boot_app(adb, device, DEFAULT_PACKAGE)
            print("[*] Waiting 5 seconds for Frida Gadget initialization...")
            time.sleep(5)
            running, pid = is_app_running(adb, device, DEFAULT_PACKAGE)
        else:
            print(f"[WARNING] Game '{DEFAULT_PACKAGE}' is not running.")
            print("          The Frida Gadget runs inside the game engine.")
            print("          Start the game or re-run with --boot to launch automatically.")

    if running:
        print(f"[OK] Game Running   : {DEFAULT_PACKAGE} (PID: {pid})")

    # 3. Ensure Frida Server is running if available on device
    ensure_frida_server(adb, device)

    # 4. Setup Port Forward
    ok_fwd = setup_port_forward(adb, device, port)
    if ok_fwd:
        print(f"[OK] Port Forward   : 127.0.0.1:{port} -> Android:{port}")
    else:
        print(f"[WARNING] Failed to set port forward tcp:{port}. Continuing check...")

    # 4. Ping Frida
    print(f"[*] Testing Frida Connection on 127.0.0.1:{port}...")
    connected, info, _ = test_frida_connection(port=port, timeout_sec=3.0)

    if connected:
        print(f"[SUCCESS] Frida Server/Gadget is ONLINE: {info}")
        print("-" * 65)
        print(" All Frida-powered automation scripts are ready to use:")
        print("   - python scripts/game_actions.py start   (start / restart fight)")
        print("   - python scripts/game_actions.py pause   (in-engine pause)")
        print("   - python scripts/game_actions.py resume  (in-engine resume)")
        print("   - python scripts/game_actions.py exit    (exit fight to map)")
        print("   - python scripts/engine_controller.py    (direct combat actions)")
        print("   - python scripts/tick_controller.py      (freeze / step simulation)")
        print("   - python scripts/stream_telemetry.py     (live 60Hz state stream)")
        print("=" * 65)
        return True
    else:
        print(f"[FAIL] Could not connect to Frida Gadget on 127.0.0.1:{port}.", file=sys.stderr)
        print(f"       Details: {info}", file=sys.stderr)
        if not running:
            print("       Hint: Launch Shadow Fight 2 on BlueStacks first, then run this again.", file=sys.stderr)
        else:
            print(
                "       Hint: If the game just launched, wait 2-3 seconds for Unity to load and retry.", file=sys.stderr
            )
        print("=" * 65, file=sys.stderr)
        return False


def run_watchdog(adb: str, port: int, interval: float = 3.0, auto_boot: bool = False):
    """Runs a continuous watchdog daemon keeping the bridge alive and healthy."""
    print("=" * 65)
    print(" Shadow Fight 2 - Frida Bridge Watchdog Daemon")
    print(f" Monitoring 127.0.0.1:{port} every {interval}s. Press Ctrl+C to stop.")
    print("=" * 65)

    last_state = None

    try:
        while True:
            device = get_connected_device(adb)
            if not device:
                if last_state != "no_device":
                    print(f"[{time.strftime('%H:%M:%S')}] [!] No ADB device detected. Retrying connection...")
                    last_state = "no_device"
                subprocess.run([adb, "connect", DEFAULT_ADB_TARGET], capture_output=True, timeout=5)
                time.sleep(interval)
                continue

            # Ensure port forward is maintained
            setup_port_forward(adb, device, port)

            # Check running state
            running, pid = is_app_running(adb, device, DEFAULT_PACKAGE)
            if not running and auto_boot:
                print(f"[{time.strftime('%H:%M:%S')}] [*] Game stopped. Auto-booting {DEFAULT_PACKAGE}...")
                boot_app(adb, device, DEFAULT_PACKAGE)
                time.sleep(4)
                continue

            connected, info, _ = test_frida_connection(port=port, timeout_sec=1.5)

            if connected:
                if last_state != "connected":
                    print(f"[{time.strftime('%H:%M:%S')}] [OK] Frida Bridge ONLINE ({info}) on {device}")
                    last_state = "connected"
            else:
                if last_state != "disconnected":
                    status_note = f"Game PID: {pid}" if running else "Game not running"
                    print(f"[{time.strftime('%H:%M:%S')}] [!] Frida offline ({status_note}). Waiting...")
                    last_state = "disconnected"

            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n[{time.strftime('%H:%M:%S')}] Watchdog stopped by user.")


def main():
    parser = argparse.ArgumentParser(description="Shadow Fight 2 Frida Bridge & Service Manager")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Frida port (default: {DEFAULT_PORT})")
    parser.add_argument("--boot", action="store_true", help="Automatically launch the game if not running")
    parser.add_argument(
        "--watch", "--daemon", action="store_true", help="Run continuous watchdog daemon to maintain bridge"
    )
    parser.add_argument("--interval", type=float, default=3.0, help="Watchdog poll interval in seconds (default: 3.0)")
    args = parser.parse_args()

    adb = find_adb()

    if args.watch:
        run_watchdog(adb=adb, port=args.port, interval=args.interval, auto_boot=args.boot)
    else:
        success = run_check(adb=adb, port=args.port, auto_boot=args.boot)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
