#!/usr/bin/env python3
r"""
Shadow Fight 2 — Master Tick Controller.
Consolidates all combat timing, stepping, freezing, and simulation speed controls:

1. Tick freeze when the fight starts (auto-freeze on round start or immediate freeze)
2. Option to increase tick speed (speed N increases speed to Nx)
3. Tick unfreeze (resume normal 60Hz real-time)
4. Tick step (step N advances exactly N ticks and stays frozen)

Usage:
    Interactive Console (Default):
        python scripts/tick_controller.py

    Direct Commands:
        python scripts/tick_controller.py freeze          # Freeze combat physics immediately
        python scripts/tick_controller.py unfreeze        # Unfreeze / resume real-time
        python scripts/tick_controller.py step 10         # Step exactly 10 ticks
        python scripts/tick_controller.py speed 5         # Set speed to 5x
        python scripts/tick_controller.py auto-freeze     # Wait and auto-freeze the moment fight starts
        python scripts/tick_controller.py status          # Query simulation timing status

    Python API:
        from scripts.tick_controller import SF2TickController

        clock = SF2TickController()
        clock.connect()
        clock.freeze()

        # Step 4 ticks
        total_ticks = clock.step(num_ticks=4)
        print(f"Total Ticks: {total_ticks}")

        # Set speed
        clock.set_speed(5.0)

        clock.unfreeze()
        clock.disconnect()
"""

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

try:
    import frida
except ImportError:
    frida = None


def get_frida_endpoint() -> tuple[str, int]:
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
HOOK_JS_PATH = os.path.join(SCRIPT_DIR, "frida", "tick_controller.js")

ACTION_MAP: dict[str, tuple[int, int]] = {
    "p": (0, 9),  # Punch
    "k": (0, 10),  # Kick
    "w": (1, 0),  # Jump Up
    "d": (3, 0),  # Forward
    "s": (5, 0),  # Duck Down
    "a": (7, 0),  # Back
    "dp": (3, 9),  # Forward Knife Slash / Punch
    "sp": (5, 9),  # Low Punch
    "wp": (1, 9),  # Upper Slash
    "ap": (7, 9),  # Spinning Back Punch
    "dk": (3, 10),  # Forward Kick
    "sk": (5, 10),  # Low Sweep Kick
    "wk": (1, 10),  # Jumping Kick
    "ak": (7, 10),  # Backward Crescent Kick
}


class SF2TickController:
    """Master controller for Shadow Fight 2 game clock, tick freeze, speed, and stepping."""

    def __init__(self, host: str | None = None, port: int | None = None):
        default_host, default_port = get_frida_endpoint()
        self.host = host or default_host
        self.port = port or default_port
        self.session = None
        self.script = None
        self.is_connected = False
        self._step_done_event = threading.Event()
        self._auto_freeze_event = threading.Event()
        self._last_ticks: int = 0

    def connect(self) -> bool:
        """Connects to Frida Gadget and loads the tick controller hook."""
        if frida is None:
            raise RuntimeError("Frida package is not installed. Run: uv pip install frida")

        ensure_frida_port_forward(self.port)

        from scripts.common import attach_to_game, load_frida_script
        js_code = load_frida_script("tick_controller.js")

        try:
            device_manager = frida.get_device_manager()  # type: ignore[attr-defined]
            device = device_manager.add_remote_device(f"{self.host}:{self.port}")
            self.session = attach_to_game(device)
            self.script = self.session.create_script(js_code)
            self.script.on("message", self._on_message)
            self.script.load()
            time.sleep(0.3)
            self.is_connected = True
            return True
        except Exception as e:
            print(f"[ERROR] Could not connect to game engine on {self.host}:{self.port}: {e}")
            self.is_connected = False
            return False

    def _on_message(self, message: dict[str, Any], data: Any):
        if message.get("type") == "send":
            payload = message.get("payload", {})
            if isinstance(payload, dict):
                ev = payload.get("event")
                if ev == "step_done":
                    self._last_ticks = payload.get("total_ticks", 0)
                    self._step_done_event.set()
                elif ev == "auto_frozen_on_round_start":
                    self._last_ticks = payload.get("total_ticks", 0)
                    self._auto_freeze_event.set()
        elif message.get("type") == "error":
            print(f"[JS ERROR] {message.get('stack', message)}", file=sys.stderr)

    def freeze(self) -> bool:
        """Freezes combat physics and match countdown clock in place."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        res = self.script.exports_sync.freeze()
        return res.get("frozen", False)

    def unfreeze(self) -> bool:
        """Unfreezes combat physics back to normal continuous real-time."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        res = self.script.exports_sync.unfreeze()
        return not res.get("frozen", True)

    def step(self, num_ticks: int = 1, action: str | None = None, timeout: float = 5.0) -> int:
        """
        Advances the simulation by exactly num_ticks while keeping the game frozen.
        Optionally dispatches a combat action during the step.
        Returns total ticks executed.
        """
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")

        quad, button = 0, 0
        if action:
            act_lower = action.lower().strip()
            if act_lower in ACTION_MAP:
                quad, button = ACTION_MAP[act_lower]

        self._step_done_event.clear()
        self.script.exports_sync.step(num_ticks, quad, button)

        done = self._step_done_event.wait(timeout=timeout)
        if not done:
            st = self.get_status()
            self._last_ticks = st.get("total_ticks", 0)

        return self._last_ticks

    def set_speed(self, scale: float = 1.0) -> float:
        """Sets internal simulation speed (1.0 = 1x, 5.0 = 5x, 10.0 = 10x, etc.)."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        res = self.script.exports_sync.set_speed(float(scale))
        return res.get("speed", scale)

    def enable_auto_freeze(self, enabled: bool = True):
        """Enables auto-freezing the instant a fight/round starts."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        self.script.exports_sync.set_auto_freeze(enabled)

    def wait_for_auto_freeze(self, timeout: float = 30.0) -> bool:
        """Blocks until the round start auto-freeze trigger fires."""
        self._auto_freeze_event.clear()
        self.enable_auto_freeze(True)
        return self._auto_freeze_event.wait(timeout=timeout)

    def get_status(self) -> dict[str, Any]:
        """Queries current simulation timing status (frozen, total_ticks, budget, speed)."""
        if not self.is_connected or not self.script:
            raise RuntimeError("Not connected to game engine.")
        return self.script.exports_sync.get_status()

    def get_state(self) -> dict[str, Any]:
        """Compatibility helper returning simulation timing status."""
        return self.get_status()

    def disconnect(self):
        """Restores normal real-time mode, resets speed to 1x, and unhooks."""
        if self.script:
            try:
                self.script.exports_sync.set_speed(1.0)
                self.script.exports_sync.unfreeze()
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


def run_interactive(controller: SF2TickController):
    """Interactive command console."""
    controller.freeze()
    status = controller.get_status()

    print("\n" + "=" * 70)
    print(" SHADOW FIGHT 2 -- MASTER TICK CONTROLLER")
    print("=" * 70)
    print(" STATUS : [FROZEN]")
    print(f" TIMING : Speed: {status.get('speed', 1.0):.1f}x | Total Ticks: {status.get('total_ticks', 0)}")
    print("-" * 70)
    print(" Controls:")
    print("   [Enter]         -> Step exactly 1 tick")
    print("   step <N> or <N> -> Step N ticks (e.g. '10', 'step 30')")
    print("   <move>          -> Step 6 ticks with move (p, k, dp, sp, wp, dk, sk)")
    print("   speed <N>       -> Change tick speed to Nx (e.g. 'speed 5', 'speed 10')")
    print("   freeze          -> Freeze combat physics")
    print("   unfreeze        -> Resume normal continuous physics")
    print("   auto            -> Wait to auto-freeze the moment next round starts")
    print("   status          -> Query current timing status")
    print("   q / quit        -> Reset speed to 1x, unfreeze, and exit")
    print("=" * 70 + "\n")

    is_frozen = True
    current_speed = status.get("speed", 1.0)

    while True:
        try:
            status_tag = f"FROZEN @ {current_speed:.0f}x" if is_frozen else f"RUNNING @ {current_speed:.0f}x"
            line = input(f"tick [{status_tag}] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if line in ("q", "quit", "exit"):
            break
        elif line == "":
            ticks = controller.step(1)
            is_frozen = True
            print(f" -> [+1 tick] Total Ticks: {ticks}")
        elif line.startswith("step "):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                n = int(parts[1])
                act = parts[2] if len(parts) >= 3 else None
                ticks = controller.step(n, action=act)
                is_frozen = True
                print(f" -> [+{n} ticks] Total Ticks: {ticks}")
        elif line.isdigit():
            n = int(line)
            ticks = controller.step(n)
            is_frozen = True
            print(f" -> [+{n} ticks] Total Ticks: {ticks}")
        elif line.startswith("speed "):
            parts = line.split()
            try:
                val = float(parts[1])
                current_speed = controller.set_speed(val)
                print(f"[OK] Simulation speed set to {current_speed:.1f}x")
            except Exception as e:
                print(f"[ERROR] Invalid speed: {e}")
        elif line in ("f", "freeze"):
            controller.freeze()
            is_frozen = True
            print("[OK] Combat physics is FROZEN.")
        elif line in ("u", "unfreeze", "resume"):
            controller.unfreeze()
            is_frozen = False
            print(f"[OK] Combat physics is RUNNING (speed {current_speed:.1f}x).")
        elif line == "auto":
            print("[*] Waiting for next fight / round start to auto-freeze...")
            if controller.wait_for_auto_freeze(timeout=30.0):
                is_frozen = True
                print("[SUCCESS] Round started! Combat automatically FROZEN at tick 0.")
            else:
                print("[WARN] Timed out waiting for round start.")
        elif line == "status":
            st = controller.get_status()
            print(
                f"[STATUS] Frozen: {st.get('frozen')} | Speed: {st.get('speed')}x | Total Ticks: {st.get('total_ticks')}"
            )
        elif line in ACTION_MAP:
            ticks = controller.step(6, action=line)
            is_frozen = True
            print(f" -> [Action: {line.upper()}] Total Ticks: {ticks}")
        else:
            print("Commands: Enter (1 tick), <N> (N ticks), speed <N>, freeze, unfreeze, auto, status, q (quit)")


def main():
    parser = argparse.ArgumentParser(description="Shadow Fight 2 Master Tick Controller")
    subparsers = parser.add_subparsers(dest="command")

    # freeze
    subparsers.add_parser("freeze", help="Freeze combat physics immediately")

    # unfreeze
    subparsers.add_parser("unfreeze", help="Unfreeze / resume continuous physics")

    # step
    step_p = subparsers.add_parser("step", help="Step N ticks")
    step_p.add_argument("ticks", type=int, nargs="?", default=1, help="Number of ticks (default: 1)")
    step_p.add_argument("action", type=str, nargs="?", default=None, help="Action code (e.g. p, k, dp)")
    step_p.add_argument("--hold", type=float, default=None, help="Hold frozen for N seconds before resuming")

    # speed
    speed_p = subparsers.add_parser("speed", help="Set simulation speed")
    speed_p.add_argument("scale", type=float, help="Speed multiplier (e.g. 2, 5, 10)")

    # auto-freeze
    subparsers.add_parser("auto-freeze", help="Wait for fight/round start and freeze immediately")

    # status
    subparsers.add_parser("status", help="Query simulation timing status")

    # interactive
    subparsers.add_parser("interactive", help="Launch interactive control console")

    args = parser.parse_args()

    controller = SF2TickController()
    print(f"[*] Connecting to game engine on {controller.host}:{controller.port}...")
    if not controller.connect():
        sys.exit(1)

    try:
        if not args.command or args.command == "interactive":
            run_interactive(controller)
        elif args.command == "freeze":
            controller.freeze()
            print("[SUCCESS] Combat physics is now 100% FROZEN in place.")
            try:
                print("Press Ctrl+C to unfreeze and restore real-time...")
                while True:
                    time.sleep(1.0)
            except KeyboardInterrupt:
                pass
        elif args.command == "unfreeze":
            controller.unfreeze()
            print("[SUCCESS] Real-time 60Hz combat physics resumed.")
        elif args.command == "step":
            controller.freeze()
            ticks = controller.step(args.ticks, action=args.action)
            print(f"[STEP] Advanced {args.ticks} tick(s) -> Total Ticks: {ticks}")
            if args.hold:
                print(f"[*] Holding frozen for {args.hold:.1f}s...")
                time.sleep(args.hold)
        elif args.command == "speed":
            new_spd = controller.set_speed(args.scale)
            print(f"[SUCCESS] Game simulation speed set to {new_spd:.1f}x.")
        elif args.command == "status":
            st = controller.get_status()
            print(
                f"[STATUS] Frozen: {st.get('frozen')} | Speed: {st.get('speed')}x | Total Ticks: {st.get('total_ticks')}"
            )
        elif args.command == "auto-freeze":
            print("[*] Waiting for fight / round to start...")
            if controller.wait_for_auto_freeze(timeout=60.0):
                print("[SUCCESS] Round started! Combat automatically FROZEN.")
                try:
                    print("Game is frozen. Press Ctrl+C to unfreeze and exit...")
                    while True:
                        time.sleep(1.0)
                except KeyboardInterrupt:
                    pass
            else:
                print("[WARN] Timed out waiting for fight start.")
    finally:
        print("\n[*] Restoring 1x speed, unfreezing physics, and detaching...")
        controller.disconnect()
        print("[OK] Real-time 60Hz physics restored. Done.")


if __name__ == "__main__":
    main()
