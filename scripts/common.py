#!/usr/bin/env python3
"""
scripts/common.py - Common ADB, Emulator, and Frida utilities.
Fully emulator- and platform-agnostic: supports ReDroid, BlueStacks, Waydroid, and AVDs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APK_NAME = os.environ.get("SF2_APK", "SF2_Modded_v9.apk")
DEFAULT_APK = str(REPO_ROOT / "bluestacks" / "apks" / APK_NAME)
DEFAULT_PACKAGE = "com.nekki.catblasters"
DEFAULT_ACTIVITY = "com.nekki.utils.activity.FCMNekkiUnityPlayerActivity"
DEFAULT_FRIDA_PORT = 27042
DEFAULT_ADB_CONNECT = os.environ.get("ADB_CONNECT", "127.0.0.1:5555")

WINDOWS_ADB_FALLBACKS = [
    r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
    r"C:\Program Files (x86)\BlueStacks_nxt\HD-Adb.exe",
]


def find_adb() -> str:
    """Locate ADB across Linux, macOS, Windows, and container runtimes."""
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
        for p in WINDOWS_ADB_FALLBACKS:
            if os.path.exists(p):
                return p

    return "adb"


def get_connected_device(adb: str | None = None) -> str | None:
    """Returns the first connected active device or connects to DEFAULT_ADB_CONNECT."""
    if not adb:
        adb = find_adb()

    target = os.environ.get("ANDROID_SERIAL")
    if target:
        return target

    try:
        # Try connecting to default socket if none connected
        subprocess.run([adb, "connect", DEFAULT_ADB_CONNECT], capture_output=True, timeout=5)
        res = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=5)
        devices = []
        for line in res.stdout.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])

        if devices:
            # Prefer localhost:5555 or emulator
            for d in devices:
                if "5555" in d or "emulator" in d:
                    return d
            return devices[0]
    except Exception:
        pass

    return DEFAULT_ADB_CONNECT


def is_installed(package_name: str = DEFAULT_PACKAGE, device: str | None = None) -> bool:
    """Checks if the package is installed on the device."""
    adb = find_adb()
    serial = device or get_connected_device(adb)
    try:
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        cmd += ["shell", "pm", "list", "packages", package_name]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        target = f"package:{package_name}"
        for line in res.stdout.splitlines():
            if line.strip() == target:
                return True
    except Exception:
        pass
    return False


def install_apk(apk_path: str = DEFAULT_APK, device: str | None = None) -> bool:
    """Installs or updates the specified APK on the target device."""
    if not os.path.exists(apk_path):
        print(f"[Error] APK file not found at: {apk_path}", file=sys.stderr)
        return False

    adb = find_adb()
    serial = device or get_connected_device(adb)
    print(f"[*] Installing {os.path.basename(apk_path)} on {serial or 'default device'}...")
    try:
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        cmd += ["install", "-r", apk_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        output = (res.stdout + res.stderr).strip()
        if "Success" in output:
            return True
        else:
            print(f"[Error] Install output:\n{output}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"[Error] Installation command failed: {e}", file=sys.stderr)
        return False


def is_running(package_name: str = DEFAULT_PACKAGE, device: str | None = None) -> bool:
    """Checks if the app package currently has a running process."""
    adb = find_adb()
    serial = device or get_connected_device(adb)
    try:
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        cmd += ["shell", "pidof", package_name]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        return bool(res.stdout.strip())
    except Exception:
        return False


def boot_app(package_name: str = DEFAULT_PACKAGE, device: str | None = None) -> bool:
    """Launches the app on the target device via monkey or am start."""
    adb = find_adb()
    serial = device or get_connected_device(adb)
    try:
        cmd = [adb]
        if serial:
            cmd += ["-s", serial]
        cmd += ["shell", "am", "start", "-n", f"{package_name}/{DEFAULT_ACTIVITY}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode == 0:
            return True
        print(f"[Warning] am start output:\n{res.stdout.strip()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[Error] Failed to boot app: {e}", file=sys.stderr)
        return False


def attach_to_game(device, package_name: str = DEFAULT_PACKAGE):
    """
    Attaches to the game process across all environments:
    1. BlueStacks: In-process Frida Gadget ("Gadget")
    2. ReDroid / Generic Android: Process name ("Cat Blasters 9k", package, or matching PID)
    """
    # 1. Try Gadget (BlueStacks mode)
    try:
        return device.attach("Gadget")
    except Exception:
        pass

    # 2. Try display / app name
    try:
        return device.attach("Cat Blasters 9k")
    except Exception:
        pass

    # 3. Try package name
    try:
        return device.attach(package_name)
    except Exception:
        pass

    # 4. Search enumerated processes for match
    try:
        procs = device.enumerate_processes()
        for p in procs:
            n = p.name.lower()
            if package_name in n or "catblasters" in n or "cat blasters" in n:
                return device.attach(p.pid)
    except Exception:
        pass

    raise RuntimeError(f"Could not locate running game process for '{package_name}' on Frida device")


def load_frida_script(script_filename: str) -> str:
    """
    Loads a Frida script from scripts/frida/, prepending scripts/frida/common.js
    so shared helper logic (findIl2CppBase, runtime architecture adaptors) is unified.
    """
    frida_dir = REPO_ROOT / "scripts" / "frida"
    common_js = frida_dir / "common.js"
    target_js = frida_dir / script_filename

    if not target_js.exists():
        raise FileNotFoundError(f"Frida script not found: {target_js}")

    chunks = []
    if common_js.exists():
        chunks.append(common_js.read_text(encoding="utf-8"))
    chunks.append(target_js.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)
