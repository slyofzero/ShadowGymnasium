#!/usr/bin/env python3
"""
Shadow Fight 2 — Live In-Engine JSON Telemetry & Combat Event Streamer.
Connects directly to the embedded Frida Gadget runtime inside the game process.
Intercepts the master physics loop (FightScene.FixedUpdate) and combat event hooks to stream:
- Character & Opponent HP (decrypted IEEE 754 floats)
- Real-time 3D Vector coordinates (X, Y, Z) via native engine Vector3
- Distance & Spatial Facing direction
- Current movements & animations of both fighters (Player & Opponent)
- Precise Hit Types (Clean hit, Head hit, Critical hit, Blocked hit, Shock)
- Lifecycle events:
    * event: "STATE" (per tick state)
    * event: "EQUIPMENT_INFO" (fighter weapons, armors, helms, ranged, magic)
    * event: "ROUND_START" (round index and timestamp)
    * event: "ROUND_END" (winner, reason, round index)

Usage:
    python ./scripts/stream_telemetry.py             # Stream NDJSON (one JSON line per tick)
    python ./scripts/stream_telemetry.py --pretty    # Stream pretty-printed JSON blocks
    python ./scripts/stream_telemetry.py --hits-only # Only emit JSON when hit events occur
"""

import argparse
import json
import os
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


def get_frida_endpoint() -> tuple:
    host = os.environ.get("FRIDA_HOST", "127.0.0.1")
    port = int(os.environ.get("FRIDA_PORT", "27042"))
    return host, port


def ensure_frida_port_forward(port: int = 27042):
    if os.environ.get("FRIDA_DIRECT") == "1":
        return
    import shutil

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
TELEMETRY_JS_PATH = os.path.join(SCRIPT_DIR, "frida", "telemetry_streamer.js")


def load_telemetry_script() -> str:
    from scripts.common import load_frida_script
    return load_frida_script("telemetry_streamer.js")


class SF2TelemetryStreamer:
    """
    Live in-engine telemetry and combat event streamer for Shadow Fight 2.
    Connects to Frida Gadget, listens to native combat engine hooks, and maintains
    up-to-date state frames (event="STATE") and fight metadata (event="EQUIPMENT_INFO", "ROUND_START", "ROUND_END").
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        default_host, default_port = get_frida_endpoint()
        self.host = host or default_host
        self.port = port or default_port
        self.session = None
        self.script = None
        self.is_connected = False

        self.state: dict[str, Any] | None = None
        self.metadata: dict[str, Any] = {
            "equipment": {},
            "current_round": 1,
            "scores": {"player": 0, "opponent": 0},
            "round_history": [],
            "last_round_winner": None,
            "last_round_reason": None,
        }

        self._lock = threading.Lock()
        self._new_state_event = threading.Event()
        self._event_listeners: list[Any] = []
        self._state_listeners: list[Any] = []

    def add_event_listener(self, callback):
        """Register a callback for all event logs (STATE, EQUIPMENT_INFO, ROUND_START, ROUND_END)."""
        self._event_listeners.append(callback)

    def add_state_listener(self, callback):
        """Register a callback called exclusively on each new STATE tick."""
        self._state_listeners.append(callback)

    def connect(self) -> bool:
        if frida is None:
            raise RuntimeError("Frida package is not installed. Run: uv pip install frida")

        ensure_frida_port_forward(self.port)

        if not os.path.exists(TELEMETRY_JS_PATH):
            raise FileNotFoundError(f"Telemetry JS script not found at {TELEMETRY_JS_PATH}")

        js_code = load_telemetry_script()

        try:
            from scripts.common import attach_to_game
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
            print(f"[ERROR] Could not connect telemetry streamer to {self.host}:{self.port}: {e}", file=sys.stderr)
            self.is_connected = False
            return False

    def _on_message(self, message: dict[str, Any], data: Any):
        if message.get("type") == "send":
            payload = message.get("payload", {})
            if not isinstance(payload, dict):
                return
            ev = payload.get("event")

            if ev == "STATE":
                with self._lock:
                    self.state = payload
                    self._new_state_event.set()
                for cb in self._state_listeners:
                    try:
                        cb(payload)
                    except Exception:
                        pass

            elif ev == "EQUIPMENT_INFO":
                with self._lock:
                    self.metadata["equipment"] = {
                        "player": payload.get("player", {}),
                        "opponent": payload.get("opponent", {}),
                    }

            elif ev == "ROUND_START":
                with self._lock:
                    rnd = payload.get("round", 1)
                    self.metadata["current_round"] = rnd
                    self.metadata["round_history"].append(
                        {"event": "ROUND_START", "round": rnd, "timestamp": payload.get("timestamp", time.time())}
                    )

            elif ev == "ROUND_END":
                with self._lock:
                    rnd = payload.get("round", 1)
                    winner = payload.get("winner", "unknown")
                    reason = payload.get("reason", "unknown")
                    self.metadata["last_round_winner"] = winner
                    self.metadata["last_round_reason"] = reason
                    if winner == "player":
                        self.metadata["scores"]["player"] += 1
                    elif winner == "opponent":
                        self.metadata["scores"]["opponent"] += 1
                    self.metadata["round_history"].append(
                        {
                            "event": "ROUND_END",
                            "round": rnd,
                            "winner": winner,
                            "reason": reason,
                            "timestamp": payload.get("timestamp", time.time()),
                        }
                    )

            for cb in self._event_listeners:
                try:
                    cb(payload)
                except Exception:
                    pass

        elif message.get("type") == "error":
            print(f"[JS ERROR] {message.get('stack', message)}", file=sys.stderr)

    def get_state(self) -> dict[str, Any] | None:
        """Returns the latest combat state dictionary."""
        with self._lock:
            if self.state is not None:
                return dict(self.state)
        # Fallback to RPC call if script loaded
        if self.script:
            try:
                st = self.script.exports_sync.get_state()
                if st:
                    with self._lock:
                        self.state = st
                    return st
            except Exception:
                pass
        return None

    def get_metadata(self) -> dict[str, Any]:
        """Returns a snapshot of current fight metadata (equipment, round count, scores, history)."""
        with self._lock:
            if not self.metadata["equipment"] and self.script:
                try:
                    eq = self.script.exports_sync.get_equipment()
                    if eq:
                        self.metadata["equipment"] = eq
                except Exception:
                    pass
            return dict(self.metadata)

    def wait_for_state(self, target_tick: int | None = None, timeout: float = 3.0) -> dict[str, Any] | None:
        """
        Blocks until a state frame is received.
        If target_tick is specified, blocks until state with tick >= target_tick arrives.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self.state is not None:
                    if target_tick is None or self.state.get("tick", 0) >= target_tick:
                        return dict(self.state)
            self._new_state_event.wait(timeout=min(0.1, max(0.01, deadline - time.time())))
            self._new_state_event.clear()

        return self.get_state()

    def reset_metadata(self):
        """Resets round counters, scores, and round history for a new match."""
        with self._lock:
            self.metadata = {
                "equipment": {},
                "current_round": 1,
                "scores": {"player": 0, "opponent": 0},
                "round_history": [],
                "last_round_winner": None,
                "last_round_reason": None,
            }

    def disconnect(self):
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
    default_host, default_port = get_frida_endpoint()
    parser = argparse.ArgumentParser(description="Live JSON Telemetry Streamer for Shadow Fight 2.")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Stream interval in seconds (default: 1.0s). Use 0 for unthrottled live.",
    )
    parser.add_argument("--rate", type=float, default=None, help="Updates per second (Hz). E.g. --rate 1 or --rate 5.")
    parser.add_argument(
        "--live", action="store_true", help="Shortcut for unthrottled 60Hz tick-wise stream (every single physics tick)"
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON objects instead of single-line NDJSON")
    parser.add_argument("--hits-only", action="store_true", help="Only stream frames when a hit event occurs")
    parser.add_argument(
        "--log",
        "--save-log",
        dest="save_log",
        nargs="?",
        const="auto",
        default=None,
        help="Store live telemetry in a JSONL file under game-logs/ (auto timestamped if filename omitted)",
    )
    parser.add_argument(
        "--host", type=str, default=default_host, help="Frida Gadget host (default: from FRIDA_HOST or 127.0.0.1)"
    )
    parser.add_argument(
        "--port", type=int, default=default_port, help="Frida Gadget port (default: from FRIDA_PORT or 27042)"
    )
    args = parser.parse_args()

    # Determine effective interval
    if args.live:
        effective_interval = 0.0
    elif args.rate is not None and args.rate > 0:
        effective_interval = 1.0 / args.rate
    else:
        effective_interval = max(0.0, args.interval)

    # Initialize live JSONL logging if requested
    log_file = None
    if args.save_log is not None:
        log_dir = os.path.join(os.getcwd(), "game-logs")
        os.makedirs(log_dir, exist_ok=True)
        if args.save_log == "auto":
            log_filename = f"telemetry_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
        else:
            log_filename = args.save_log if args.save_log.endswith(".jsonl") else f"{args.save_log}.jsonl"
        log_path = os.path.join(log_dir, log_filename)
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        print(f"[*] Live logging active: {log_path}", file=sys.stderr)

    streamer = SF2TelemetryStreamer(host=args.host, port=args.port)
    print(f"Connecting to Frida Gadget on {args.host}:{args.port}...", file=sys.stderr)
    if not streamer.connect():
        if log_file:
            log_file.close()
        print(
            "Ensure Shadow Fight 2 (SF2_Modded_v8.apk) is running on the target Android device/container.",
            file=sys.stderr,
        )
        sys.exit(1)

    latest_frame = None
    accumulated_hits = []
    lock = threading.Lock()

    def log_event(event_dict):
        """Always outputs lifecycle events (round_start, round_end, equipment_info) regardless of --hits-only."""
        if args.pretty:
            json_text = json.dumps(event_dict, indent=2)
            print(json_text)
            if log_file is not None:
                log_file.write(json_text + "\n\n")
                log_file.flush()
        else:
            json_text = json.dumps(event_dict)
            print(json_text)
            if log_file is not None:
                log_file.write(json_text + "\n")
                log_file.flush()
        sys.stdout.flush()

    def print_frame(frame, hits):
        if args.hits_only and not hits:
            return
        frame_copy = dict(frame)
        frame_copy["hits"] = hits
        if args.pretty:
            json_text = json.dumps(frame_copy, indent=2)
            print(json_text)
            if log_file is not None:
                log_file.write(json_text + "\n\n")
                log_file.flush()
        else:
            json_text = json.dumps(frame_copy)
            print(json_text)
            if log_file is not None:
                log_file.write(json_text + "\n")
                log_file.flush()
        sys.stdout.flush()

    def on_event(payload):
        nonlocal latest_frame
        ev = payload.get("event")
        if ev in ("EQUIPMENT_INFO", "ROUND_START", "ROUND_END"):
            log_event(payload)
        elif ev == "STATE":
            new_hits = payload.get("hits", [])
            if effective_interval == 0.0:
                print_frame(payload, new_hits)
            else:
                with lock:
                    if new_hits:
                        accumulated_hits.extend(new_hits)
                    latest_frame = payload

    streamer.add_event_listener(on_event)

    mode_desc = "unthrottled 60Hz tick-wise stream" if effective_interval == 0.0 else f"every {effective_interval}s"
    print(f"[SUCCESS] Telemetry streaming ({mode_desc}). Press Ctrl+C to stop.\n", file=sys.stderr)

    try:
        if effective_interval == 0.0:
            while True:
                time.sleep(1.0)
        else:
            waiting_shown = False
            last_printed_tick = -1
            while True:
                time.sleep(effective_interval)
                with lock:
                    if latest_frame is not None:
                        cur_tick = latest_frame.get("tick", -1)
                        if cur_tick != last_printed_tick:
                            hits_snapshot = list(accumulated_hits)
                            accumulated_hits.clear()
                            print_frame(latest_frame, hits_snapshot)
                            last_printed_tick = cur_tick
                            waiting_shown = False
                    elif not waiting_shown:
                        print("[INFO] Connected to engine. Waiting for combat scene to tick...", file=sys.stderr)
                        waiting_shown = True
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping telemetry stream. Goodbye!", file=sys.stderr)
        streamer.disconnect()
    finally:
        if log_file is not None and not log_file.closed:
            log_file.close()


if __name__ == "__main__":
    main()
