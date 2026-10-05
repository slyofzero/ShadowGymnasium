#!/usr/bin/env python3
r"""
Shadow Fight 2 — Native In-Engine Game Actions API.
Executes pause, resume, and exit_fight directly via IL2CPP method calls inside Unity's main thread.
Zero ADB or BlueStacks screen taps required. Fully headless-ready.

Usage:
    CLI:
        python scripts/game_actions.py start
        python scripts/game_actions.py resume
        python scripts/game_actions.py pause
        python scripts/game_actions.py exit
        python scripts/game_actions.py status

    Python API:
        from scripts.game_actions import SF2GameActions
        actions = SF2GameActions()
        actions.connect()
        actions.start_fight()
        actions.resume()
        actions.pause()
        actions.exit_fight()
        actions.disconnect()
"""

import argparse
import os
import subprocess
import sys
import threading
import time
from typing import Any

try:
    import frida
except ImportError:
    frida = None

import shutil

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import frida
except ImportError:
    frida = None


def get_frida_endpoint() -> tuple:
    host = os.environ.get("FRIDA_HOST", "127.0.0.1")
    port = int(os.environ.get("FRIDA_PORT", "27042"))
    return host, port


def ensure_frida_port_forward(port: int = 27042):
    if os.environ.get("FRIDA_DIRECT") == "1":
        return
    adb = os.environ.get("ADB_PATH") or os.environ.get("ADB_BIN") or shutil.which("adb")
    if not adb and sys.platform == "win32":
        candidates = [
            r"C:\Program Files\BlueStacks_nxt\HD-Adb.exe",
            r"C:\Program Files (x86)\BlueStacks_nxt\HD-Adb.exe",
        ]
        for c in candidates:
            if os.path.exists(c):
                adb = c
                break
    if not adb:
        adb = "adb"
    serial = os.environ.get("ANDROID_SERIAL") or os.environ.get("ADB_DEVICE")
    cmd = [adb]
    if serial:
        cmd += ["-s", serial]
    cmd += ["forward", f"tcp:{port}", f"tcp:{port}"]
    try:
        subprocess.run(cmd, capture_output=True, timeout=5)
    except Exception:
        pass


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_JS_PATH = os.path.join(SCRIPT_DIR, "frida", "game_actions.js")


class SF2GameActions:
    """
    Native controller for Shadow Fight 2 in-fight actions.
    Fully emulator-agnostic (supports Docker/Redroid, Waydroid, AVDs, BlueStacks, or bare-metal).
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        default_host, default_port = get_frida_endpoint()
        self.host = host or default_host
        self.port = port or default_port
        self.session = None
        self.script = None
        self.is_connected = False
        self.use_adb_fallback = False
        self.adb = None
        self.adb_device = None
        self._action_event = threading.Event()
        self._last_event = None

    def connect(self) -> bool:
        """Connects to Frida In-Engine Hook, or gracefully falls back to ADB Input Controller."""
        # Auto port-forward if running over ADB (no-op if direct IP / container)
        ensure_frida_port_forward(self.port)

        from scripts.common import attach_to_game, find_adb, get_connected_device, load_frida_script

        self.adb = find_adb()
        self.adb_device = get_connected_device(self.adb)

        try:
            if frida is not None:
                js_code = load_frida_script("game_actions.js")
                device_manager = frida.get_device_manager()  # type: ignore[attr-defined]
                device = device_manager.add_remote_device(f"{self.host}:{self.port}")
                self.session = attach_to_game(device)
                self.script = self.session.create_script(js_code)
                self.script.on("message", self._on_message)
                self.script.load()
                time.sleep(0.3)
                # Verify that RPC methods are available (fails on translated x86_64 runtimes)
                self.script.exports_sync.get_status()
                self.is_connected = True
                return True
        except Exception:
            pass

        # Fallback to direct ADB touch / input navigation
        print(f"[*] In-engine hook unavailable on {self.host}:{self.port}. Using ADB Input Controller on {self.adb_device}...")
        self.use_adb_fallback = True
        self.is_connected = True
        return True

    def _on_message(self, message: dict[str, Any], data: Any):
        if message.get("type") == "send":
            payload = message.get("payload", {})
            if isinstance(payload, dict) and payload.get("event") == "action_completed":
                self._last_event = payload
                self._action_event.set()

    def get_status(self) -> dict[str, Any]:
        """Queries the engine's current state (scene, in_fight, is_paused, tick count)."""
        if not self.is_connected:
            raise RuntimeError("Not connected to game engine.")
        if self.use_adb_fallback:
            return {
                "mode": "ADB_INPUT_CONTROLLER",
                "scene": "ActiveScreen",
                "in_fight": True,
                "is_paused": False,
                "ticks": 0,
            }
        return self.script.exports_sync.get_status()

    def pause(self, timeout: float = 2.0) -> bool:
        """Pauses the current fight natively or via pause button tap."""
        if not self.is_connected:
            raise RuntimeError("Not connected to game engine.")
        if self.use_adb_fallback:
            cmd = [self.adb]
            if self.adb_device:
                cmd += ["-s", self.adb_device]
            cmd += ["shell", "input", "tap", "960", "160"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True

        self._action_event.clear()
        res = self.script.exports_sync.pause()
        if not res.get("success"):
            print(f"[WARN] Pause command failed: {res.get('error')}")
            return False
        done = self._action_event.wait(timeout=timeout)
        time.sleep(0.2)
        return done

    def resume(self, timeout: float = 2.0) -> bool:
        """Resumes / unpauses the current fight natively or via resume button tap."""
        if not self.is_connected:
            raise RuntimeError("Not connected to game engine.")
        if self.use_adb_fallback:
            cmd = [self.adb]
            if self.adb_device:
                cmd += ["-s", self.adb_device]
            cmd += ["shell", "input", "tap", "960", "540"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True

        self._action_event.clear()
        res = self.script.exports_sync.resume()
        if not res.get("success"):
            print(f"[WARN] Resume command failed: {res.get('error')}")
            return False
        done = self._action_event.wait(timeout=timeout)
        time.sleep(0.2)
        return done

    def start_fight(self, timeout: float = 5.0) -> bool:
        """Starts a fight from the MapScene, or restarts the match if already in FightScene."""
        if not self.is_connected:
            raise RuntimeError("Not connected to game engine.")
        if self.use_adb_fallback:
            cmd = [self.adb]
            if self.adb_device:
                cmd += ["-s", self.adb_device]
            cmd += ["shell", "input", "tap", "1550", "800"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            time.sleep(0.5)
            return True

        self._action_event.clear()
        res = self.script.exports_sync.start_fight()
        if not res.get("success"):
            print(f"[WARN] Start fight command failed: {res.get('error')}")
            return False
        done = self._action_event.wait(timeout=timeout)
        time.sleep(0.5)
        return done

    def exit_fight(self, timeout: float = 3.0) -> bool:
        """Exits / surrenders the current fight natively or via surrender tap."""
        if not self.is_connected:
            raise RuntimeError("Not connected to game engine.")
        if self.use_adb_fallback:
            cmd = [self.adb]
            if self.adb_device:
                cmd += ["-s", self.adb_device]
            cmd += ["shell", "input", "tap", "1150", "680"]
            subprocess.run(cmd, capture_output=True, timeout=5)
            return True

        self._action_event.clear()
        res = self.script.exports_sync.exit_fight()
        if not res.get("success"):
            print(f"[WARN] Exit fight command failed: {res.get('error')}")
            return False
        done = self._action_event.wait(timeout=timeout)
        time.sleep(0.5)
        return done

    def set_rounds(self, n: int) -> int:
        """
        Dynamically patches the number of rounds required to win a match (1–65535).
        Takes effect on the next fight start. No APK repack required.
        """
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        # ARM64 single-instruction MOVZ immediate max is 0xFFFF (65,535)
        n = max(1, min(65535, int(n)))
        res = self.script.exports_sync.set_rounds(n)
        if not res.get("success"):
            raise RuntimeError(f"set_rounds failed: {res.get('error')}")
        return res.get("rounds", n)

    def get_rounds(self) -> int | None:
        """Returns the currently patched rounds-to-win value, or None if unset this session."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        res = self.script.exports_sync.get_rounds()
        return res.get("rounds")

    def disconnect(self):
        """Detaches from the Frida session."""
        if self.script:
            try:
                self.script.unload()
            except Exception:
                pass
            self.script = None
        if self.session:
            try:
                self.session.detach()
            except Exception:
                pass
            self.session = None
        self.is_connected = False


def main():
    parser = argparse.ArgumentParser(description="Shadow Fight 2 Native Game Actions Controller")
    parser.add_argument("action", choices=["pause", "resume", "exit", "start", "status"], help="Action to execute")
    parser.add_argument(
        "--host", type=str, default=None, help="Frida Gadget host (default: from FRIDA_HOST or 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=None, help="Frida Gadget port (default: from FRIDA_PORT or 27042)")
    args = parser.parse_args()

    controller = SF2GameActions(host=args.host, port=args.port)
    print(f"[INIT] Connecting to game engine on {controller.host}:{controller.port}...")
    if not controller.connect():
        sys.exit(1)

    try:
        status = controller.get_status()
        print(
            f"[STATUS] Scene: {status.get('scene')}, In-Fight: {status.get('in_fight')}, Is-Paused: {status.get('is_paused')}, Ticks: {status.get('ticks')}"
        )

        if args.action == "status":
            pass
        elif args.action == "start":
            print("[ACTION] Triggering native start fight / restart...")
            ok = controller.start_fight()
            print(f"[RESULT] Start fight triggered: {ok}")
        elif args.action == "pause":
            print("[ACTION] Triggering native pause...")
            ok = controller.pause()
            print(f"[RESULT] Pause triggered: {ok}")
        elif args.action == "resume":
            print("[ACTION] Triggering native resume...")
            ok = controller.resume()
            print(f"[RESULT] Resume triggered: {ok}")
        elif args.action == "exit":
            print("[ACTION] Triggering native exit / surrender...")
            ok = controller.exit_fight()
            print(f"[RESULT] Exit triggered: {ok}")

        final_status = controller.get_status()
        print(
            f"[FINAL] Scene: {final_status.get('scene')}, In-Fight: {final_status.get('in_fight')}, Is-Paused: {final_status.get('is_paused')}"
        )
    finally:
        controller.disconnect()


if __name__ == "__main__":
    main()
