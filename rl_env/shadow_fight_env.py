#!/usr/bin/env python3
"""
Shadow Fight 2 — Unified RL Environment Interface.

Provides a clean, high-level API to interact with the game engine,
manage simulation timing, control combat flow, and observe game state:

    env = ShadowFightEnv()
    state = env.start()      # Starts fight, freezes ticks, advances 1 tick -> env.state
    env.step()               # Advances 1 tick
    env.step(10)             # Advances 10 ticks
    env.act("p")             # Executes native punch via engine_controller
    env.tick_speed(2.0)      # Sets simulation speed
    env.freeze()             # Freezes ticks
    env.get_state()          # Queries state -> env.state
    env.metadata             # Fight metadata (equipment, round count, scores)
    env.pause()              # Pauses in-game fight
    env.exit()               # Exits to map

CLI / Interactive Console:
    python rl_env/shadow_fight_env.py               # Launches interactive REPL
    python rl_env/shadow_fight_env.py start         # Starts fight and freezes
    python rl_env/shadow_fight_env.py step 10       # Steps 10 ticks
    python rl_env/shadow_fight_env.py act p         # Executes punch action
    python rl_env/shadow_fight_env.py state         # Queries current state
    python rl_env/shadow_fight_env.py meta          # Queries current metadata
    python rl_env/shadow_fight_env.py pause         # Pauses fight
    python rl_env/shadow_fight_env.py exit          # Exits fight to map
"""

import argparse
import json
import os
import sys
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from scripts.engine_controller import (
    ENGINE_ACTION_MAP,
    SF2EngineController,
)
from scripts.game_actions import SF2GameActions
from scripts.start_frida_service import ensure_frida_bridge, test_frida_connection
from scripts.stream_telemetry import SF2TelemetryStreamer
from scripts.tick_controller import SF2TickController


class ShadowFightEnv:
    """
    Unified environment for Shadow Fight 2.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        auto_start_frida: bool = True,
    ):
        self.host = host or os.environ.get("FRIDA_HOST", "127.0.0.1")
        self.port = port or int(os.environ.get("FRIDA_PORT", "27042"))
        self.auto_start_frida = auto_start_frida

        self.actions = SF2GameActions(host=self.host, port=self.port)
        self.clock = SF2TickController(host=self.host, port=self.port)
        self.engine = SF2EngineController(host=self.host, port=self.port)
        self.telemetry = SF2TelemetryStreamer(host=self.host, port=self.port)

        self.state: dict[str, Any] | None = None
        self._is_connected = False
        self._last_announced_round = 0

        # Live telemetry updates self.state on each STATE event frame
        self.telemetry.add_state_listener(self._on_state_update)
        # Listen to lifecycle events (EQUIPMENT_INFO, ROUND_START, ROUND_END)
        self.telemetry.add_event_listener(self._on_telemetry_event)

    def _on_state_update(self, new_state: dict[str, Any]):
        self.state = new_state

    def _on_telemetry_event(self, payload: dict[str, Any]):
        ev = payload.get("event")
        if ev == "ROUND_START":
            rnd = payload.get("round", self.metadata.get("current_round", 1))
            self._announce_round(rnd)
        elif ev == "EQUIPMENT_INFO":
            cur_rnd = self.round
            if self._last_announced_round < cur_rnd:
                self._announce_round(cur_rnd)

    def _announce_round(self, round_num: int):
        equip = self.equipment
        if not equip or not equip.get("player"):
            return
        if round_num <= self._last_announced_round:
            return
        self._last_announced_round = round_num
        print(format_round_announcement(round_num, equip))

    @property
    def metadata(self) -> dict[str, Any]:
        """Returns current fight metadata (equipment, round count, scores, round history)."""
        return self.telemetry.get_metadata()

    def get_metadata(self) -> dict[str, Any]:
        """Returns current fight metadata."""
        return self.telemetry.get_metadata()

    @property
    def equipment(self) -> dict[str, Any]:
        """Returns equipment loadout dictionary for both player and opponent."""
        return self.metadata.get("equipment", {})

    def get_equipment(self) -> dict[str, Any]:
        """Returns equipment loadout dictionary for both player and opponent."""
        return self.equipment

    @property
    def round(self) -> int:
        """Returns the current round number (1-indexed)."""
        return int(self.metadata.get("current_round", 1))

    @property
    def current_round(self) -> int:
        """Alias for env.round."""
        return self.round

    def is_server_on(self, timeout_sec: float = 1.5) -> bool:
        """Checks whether the Frida connection server is on and reachable."""
        online, _, _ = test_frida_connection(host=self.host, port=self.port, timeout_sec=timeout_sec)
        return online

    def ensure_server(self) -> bool:
        """
        Checks whether the Frida connection server is on.
        If it is not on, turns it on using scripts/start_frida_service.py.
        """
        return ensure_frida_bridge(host=self.host, port=self.port, auto_boot=True)

    def connect(self) -> bool:
        """
        Firstly checks whether the Frida connection server is on or not.
        If not on, turns on the server using start_frida_service,
        and only then establishes connections to action, clock, engine, and telemetry hooks.
        """
        if not self._is_connected:
            if self.auto_start_frida:
                server_ok = self.ensure_server()
                if not server_ok:
                    raise ConnectionError(
                        f"Unable to start or reach Frida server on {self.host}:{self.port}. "
                        "Ensure emulator/container is running and game is booted."
                    )

            ok_actions = self.actions.connect()
            ok_clock = self.clock.connect()
            ok_engine = self.engine.connect()
            ok_telemetry = self.telemetry.connect()
            self._is_connected = ok_actions and ok_clock and ok_engine and ok_telemetry
        return self._is_connected

    def start(self, timeout: float = 20.0) -> dict[str, Any] | None:
        """
        Starts the fight, freezes game ticks, and advances 1 tick.
        The resulting tick from telemetry becomes env.state.
        """
        if not self._is_connected:
            self.connect()

        self.telemetry.reset_metadata()
        self._last_announced_round = 0

        # Arm auto-freeze so physics freezes immediately on round start
        self.clock.enable_auto_freeze(True)

        # Trigger native fight start / restart
        self.actions.start_fight()

        # Wait until round start auto-freeze fires (or timeout)
        frozen = self.clock.wait_for_auto_freeze(timeout=timeout)

        # ── CRITICAL: Disarm immediately so subsequent round starts don't re-freeze ──
        self.clock.enable_auto_freeze(False)

        if not frozen:
            # Fallback guarantee: ensure freeze is active
            self.clock.freeze()

        # Advance exactly 1 tick
        self.clock.step(num_ticks=1)
        self.state = self.telemetry.wait_for_state(timeout=3.0)

        # Automatically display round and equipment info
        if self._last_announced_round < self.round:
            self._announce_round(self.round)

        return self.state

    def freeze(self) -> bool:
        """Freezes combat physics and match countdown clock in place."""
        if not self._is_connected:
            self.connect()
        frozen = self.clock.freeze()
        self.state = self.telemetry.get_state()
        return frozen

    def unfreeze(self) -> bool:
        """Unfreezes combat physics back to normal continuous real-time."""
        if not self._is_connected:
            self.connect()
        unfrozen = self.clock.unfreeze()
        self.state = self.telemetry.get_state()
        return unfrozen

    def step(self, steps: int | None = None, action: str | int | None = None) -> dict[str, Any] | None:
        """
        Advances the simulation by one tick if steps is None, else by 'steps' ticks.
        Optionally dispatches a combat action during the step.
        Updates and returns env.state from telemetry.
        """
        if not self._is_connected:
            self.connect()

        num_ticks = 1 if steps is None else max(1, int(steps))
        cur_tick = self.state.get("tick", 0) if self.state else 0
        target_tick = cur_tick + num_ticks

        if action is not None:
            self.engine.act(action)

        self.clock.step(num_ticks=num_ticks)
        self.state = self.telemetry.wait_for_state(target_tick=target_tick, timeout=3.0)
        return self.state

    def act(self, action: str | int) -> dict[str, Any] | None:
        """
        Executes a combat or movement action in the game engine via SF2EngineController.

        Args:
            action: Action string (e.g. 'p', 'pp', 'k', 'kk', 'dp', 'sp', 'wp', 'ap',
                    'dk', 'sk', 'wk', 'ak', 'w', 's', 'a', 'd', 'wd', 'wa', 'sd', 'sa',
                    'dd', 'aa', 'stop', 'hold d', 'hold a') or discrete integer index (0..34).

        Returns:
            The resulting state dictionary after the action is executed.
        """
        if not self._is_connected:
            self.connect()

        ok = self.engine.act(action)
        if not ok:
            print(f"[WARN] Engine action '{action}' was not recognized.")

        self.state = self.get_state()
        return self.state

    def get_state(self) -> dict[str, Any] | None:
        """
        Queries the current combat and simulation state without advancing ticks.
        Updates and returns env.state from telemetry.
        """
        if not self._is_connected:
            self.connect()
        self.state = self.telemetry.get_state()
        return self.state

    def tick_speed(self, speed: float | None = None) -> float:
        """
        Changes internal simulation speed (e.g. 1.0 = 1x, 2.0 = 2x, 5.0 = 5x).
        If called without arguments, returns the current speed.
        """
        if not self._is_connected:
            self.connect()

        if speed is None:
            status = self.clock.get_status()
            return status.get("speed", 1.0)

        new_speed = self.clock.set_speed(float(speed))
        return new_speed

    def pause(self) -> bool:
        """Pauses the fight in-engine via the native pause screen."""
        if not self._is_connected:
            self.connect()
        return self.actions.pause()

    def resume(self) -> bool:
        """Resumes the fight from pause in-engine."""
        if not self._is_connected:
            self.connect()
        return self.actions.resume()

    def exit(self) -> bool:
        """Exits the current fight back to the map screen."""
        if not self._is_connected:
            self.connect()
        try:
            self.clock.unfreeze()
        except Exception:
            pass
        ok = self.actions.exit_fight()
        self.state = None
        self._last_announced_round = 0
        return ok

    def set_rounds(self, n: int) -> int:
        """
        Dynamically patches the rounds-to-win threshold (1–65535) in the running engine.
        No APK repack needed. Takes effect from the next fight start.
        """
        if not self._is_connected:
            self.connect()
        return self.actions.set_rounds(n)

    def get_rounds(self) -> int | None:
        """Returns the currently patched rounds-to-win value for this session."""
        if not self._is_connected:
            self.connect()
        return self.actions.get_rounds()

    def close(self):
        """Cleanly detaches from the game process and restores default state."""
        try:
            if self._is_connected:
                self.clock.unfreeze()
                self.clock.set_speed(1.0)
        except Exception:
            pass
        self.actions.disconnect()
        self.clock.disconnect()
        self.engine.disconnect()
        self.telemetry.disconnect()
        self._is_connected = False


# Convenient alias
SF2Env = ShadowFightEnv


def format_telemetry_line(st: dict[str, Any] | None, prefix: str = "") -> str:
    """Formats a telemetry state frame into a concise live status line."""
    if not st:
        return f"{prefix}[No state recorded yet]"
    rnd = st.get("round", 1)
    tick = st.get("tick", 0)
    clock = st.get("time_left", 0.0)
    p = st.get("player", {}) if isinstance(st.get("player"), dict) else {}
    opp = st.get("opponent", {}) if isinstance(st.get("opponent"), dict) else {}
    p_hp = p.get("hp", 1.0) * 100
    opp_hp = opp.get("hp", 1.0) * 100
    p_act = p.get("action", "Idle")
    opp_act = opp.get("action", "Idle")
    dist = st.get("distance", 0.0)
    hits = st.get("hits", [])
    hits_str = f" | Hits: {len(hits)}" if hits else ""
    return f"{prefix}R{rnd} | Tick: {tick:4d} | Clock: {clock:5.1f}s | Dist: {dist:5.1f} | P1: {p_hp:5.1f}% ({p_act}) | P2: {opp_hp:5.1f}% ({opp_act}){hits_str}"


def format_round_announcement(round_num: int, equipment: dict[str, Any]) -> str:
    """Formats a pre-round equipment and round header box."""
    p = equipment.get("player", {})
    opp = equipment.get("opponent", {})

    p_name = p.get("name") or "Player"
    p_w = p.get("weapon") or "Fists"
    p_a = p.get("armor") or "None"
    p_h = p.get("helm") or "None"
    p_r = p.get("ranged") or "None"
    p_m = p.get("magic") or "None"

    o_name = opp.get("name") or "Opponent"
    o_w = opp.get("weapon") or "Fists"
    o_a = opp.get("armor") or "None"
    o_h = opp.get("helm") or "None"
    o_r = opp.get("ranged") or "None"
    o_m = opp.get("magic") or "None"

    title = f"ROUND {round_num} START"
    width = 78
    pad_title = width - 4 - len(title)

    lines = [
        "",
        "+" + "=" * (width - 2) + "+",
        f"|  {title}" + " " * max(0, pad_title) + "|",
        "+" + "=" * (width - 2) + "+",
        f"|  PLAYER: {p_name}".ljust(width - 1) + "|",
        f"|    Weapon : {p_w:<16} Armor : {p_a:<16} Helm : {p_h:<12}|",
        f"|    Ranged : {p_r:<16} Magic : {p_m:<16}" + " " * 20 + "|",
        "+" + "-" * (width - 2) + "+",
        f"|  OPPONENT: {o_name}".ljust(width - 1) + "|",
        f"|    Weapon : {o_w:<16} Armor : {o_a:<16} Helm : {o_h:<12}|",
        f"|    Ranged : {o_r:<16} Magic : {o_m:<16}" + " " * 20 + "|",
        "+" + "=" * (width - 2) + "+",
        "",
    ]
    return "\n".join(lines)


def run_interactive(env: ShadowFightEnv) -> None:
    """Interactive command console for simplified environment interaction."""
    st = env.get_state()
    timing = env.clock.get_status() if env._is_connected else {}

    print("\n" + "=" * 80)
    print(" SHADOW FIGHT 2 — SIMPLIFIED RL ENVIRONMENT CONSOLE")
    print("=" * 80)
    frozen = timing.get("frozen", False)
    spd = timing.get("speed", 1.0)
    print(f" TIMING : Frozen: {frozen} | Speed: {spd:.1f}x | Total Ticks: {timing.get('total_ticks', 0)}")
    print(f" STATE  : {format_telemetry_line(st)}")
    print("-" * 80)
    print(" Commands:")
    print("   /start            -> Start the game (start fight, freeze at tick 0)")
    print("   /metadata         -> Print fight metadata (equipment, current round, scores)")
    print("   /pause            -> Pause in-engine")
    print("   /resume           -> Resume in-engine")
    print("   /exit             -> Exit fight back to map")
    print("   /speed <n>        -> Change simulation speed (e.g. '/speed 2.0')")
    print("   /step <number>    -> Take tick steps (e.g. '/step 10', or [Enter] for 1 tick)")
    print("   /freeze           -> Freeze physics in place")
    print("   /unfreeze         -> Resume continuous 60Hz physics")
    print("   /state            -> Get the current combat state")
    print("   /q                -> Quit console")
    print(" Actions (type directly):")
    print("   p, k, dp, sp, wp, ap, dk, sk, wk, ak, dpp, app, spp, wpp, dkk, akk, skk, wkk,")
    print("   dd, aa, wd, wa, sd, sa, w, s, a, d, stop (or 'action <ticks>', e.g. 'dp 10')")
    print("=" * 80 + "\n")

    while True:
        try:
            status = env.clock.get_status() if env._is_connected else {}
            tag = "FROZEN" if status.get("frozen") else "RUNNING"
            cmd = input(f"env [{tag}] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not cmd:
            # Advance 1 tick
            st = env.step()
            print(format_telemetry_line(st, prefix=" -> [+1 tick]   "))
            continue

        lower = cmd.lower()
        parts = lower.split()
        first = parts[0]

        if first.startswith("/"):
            slash_cmd = first[1:]
            if slash_cmd in ("q", "quit"):
                break
            elif slash_cmd == "start":
                print("[*] Starting fight, arming auto-freeze, advancing 1 tick...")
                st = env.start()
                print(f"[OK] Fight started!\n{format_telemetry_line(st, prefix='     ')}")
            elif slash_cmd == "pause":
                ok = env.pause()
                print(f"[OK] Pause triggered: {ok}")
            elif slash_cmd == "resume":
                ok = env.resume()
                print(f"[OK] Resume triggered: {ok}")
            elif slash_cmd == "exit":
                ok = env.exit()
                print(f"[OK] Exit back to map triggered: {ok}")
            elif slash_cmd in ("f", "freeze"):
                env.freeze()
                print("[OK] Physics is FROZEN in place.")
            elif slash_cmd in ("u", "unfreeze"):
                env.unfreeze()
                print("[OK] Physics is RUNNING continuous 60Hz.")
            elif slash_cmd == "speed":
                if len(parts) >= 2:
                    try:
                        s = env.tick_speed(float(parts[1]))
                        print(f"[OK] Simulation speed set to {s:.1f}x")
                    except ValueError:
                        print("[ERROR] Speed must be a number.")
                else:
                    print(f"[OK] Current speed: {env.tick_speed():.1f}x")
            elif slash_cmd == "step":
                if len(parts) >= 2 and parts[1].isdigit():
                    n = int(parts[1])
                    act = parts[2] if len(parts) >= 3 else None
                    st = env.step(steps=n, action=act)
                    prefix = f" -> [+{n} ticks] " if not act else f" -> [+{n} {act.upper()}] "
                    print(format_telemetry_line(st, prefix=prefix))
                else:
                    st = env.step()
                    print(format_telemetry_line(st, prefix=" -> [+1 tick]   "))
            elif slash_cmd in ("state", "status"):
                st = env.get_state()
                print(json.dumps(st, indent=2))
            elif slash_cmd in ("meta", "metadata"):
                print(json.dumps(env.metadata, indent=2))
            elif slash_cmd == "rounds":
                if len(parts) >= 2 and parts[1].isdigit():
                    r = env.set_rounds(int(parts[1]))
                    print(f"[OK] Rounds to win set to {r} (takes effect on next fight start)")
                else:
                    cur = env.get_rounds()
                    print(f"[OK] Current rounds-to-win: {cur if cur is not None else 'default (unset this session)'}")
            elif slash_cmd == "act":
                if len(parts) >= 2:
                    arg = parts[1]
                    try:
                        act_val = int(arg) if arg.isdigit() else arg
                        st = env.act(act_val)
                        label = str(arg).upper()
                        print(format_telemetry_line(st, prefix=f" -> [ACT: {label:4s}] "))
                    except Exception as e:
                        print(f"[ERROR] Failed to act: {e}")
                else:
                    print("[ERROR] Usage: type the action directly (e.g. p, k, dp) or /act <action>")
            else:
                print(
                    f"Unknown command: '{first}'.\n"
                    f"  Available commands: /start, /pause, /resume, /exit, /speed <n>, /step <number>, /freeze, /unfreeze, /state, /metadata, /q"
                )
        elif first in ENGINE_ACTION_MAP:
            # Action typed directly! (e.g. 'p', 'k', 'dp', 'dpp', 'dd', 'aa', or 'p 10')
            if len(parts) >= 2 and parts[1].isdigit():
                n = int(parts[1])
                st = env.step(steps=n, action=first)
                label = str(first).upper()
                print(format_telemetry_line(st, prefix=f" -> [{label:4s} +{n} ticks] "))
            else:
                st = env.act(first)
                label = str(first).upper()
                print(format_telemetry_line(st, prefix=f" -> [ACT: {label:4s}] "))
        elif first in (
            "start", "pause", "resume", "exit", "speed", "step", "freeze",
            "unfreeze", "state", "status", "metadata", "meta", "act", "rounds",
            "quit", "q",
        ):
            print(f"[ERROR] Commands must start with '/'. Use '/{first}' instead.")
        else:
            print(
                f"Unknown action: '{cmd}'.\n"
                f"  Actions (type directly): p, k, dp, sp, wp, ap, dk, sk, wk, ak, dpp, app, dd, aa, w, s, a, d, stop (or 'action <ticks>', e.g. 'dp 10')\n"
                f"  Commands (must start with '/'): /start, /pause, /resume, /exit, /speed <n>, /step <n>, /freeze, /unfreeze, /state, /metadata, /q"
            )


def main():
    parser = argparse.ArgumentParser(description="Shadow Fight 2 Unified RL Environment")
    subparsers = parser.add_subparsers(dest="command")

    # Interactive
    subparsers.add_parser("interactive", help="Launch interactive control REPL (default)")

    # start
    subparsers.add_parser("start", help="env.start(): start fight, freeze, step 1 -> env.state")

    # freeze
    subparsers.add_parser("freeze", help="env.freeze(): freeze physics immediately")

    # unfreeze
    subparsers.add_parser("unfreeze", help="env.unfreeze(): resume normal continuous physics")

    # step
    step_p = subparsers.add_parser("step", help="env.step(steps=N, action=...)")
    step_p.add_argument("steps", type=int, nargs="?", default=1, help="Number of ticks to step (default: 1)")
    step_p.add_argument("action", type=str, nargs="?", default=None, help="Action code (e.g. p, k, dp, sp)")

    # act
    act_p = subparsers.add_parser(
        "act", help="env.act(action): execute combat or movement action via engine_controller"
    )
    act_p.add_argument(
        "action", help="Action name (p, k, dp, sp, wp, dk, sk, wk, dd, etc.) or discrete integer index (0..34)"
    )

    # speed
    speed_p = subparsers.add_parser("speed", help="env.tick_speed(speed)")
    speed_p.add_argument("scale", type=float, nargs="?", default=None, help="Speed multiplier (default: get current)")

    # metadata
    subparsers.add_parser(
        "metadata", help="env.get_metadata(): print current fight metadata (equipment, round count, scores)"
    )
    # meta (alias)
    subparsers.add_parser(
        "meta", help="env.get_metadata(): alias for metadata"
    )

    # pause
    subparsers.add_parser("pause", help="env.pause(): pause in-engine")

    # resume
    subparsers.add_parser("resume", help="env.resume(): resume in-engine")

    # exit
    subparsers.add_parser("exit", help="env.exit(): exit fight to map")

    # state
    subparsers.add_parser("state", help="env.get_state(): print current combat state")

    args = parser.parse_args()

    env = ShadowFightEnv()
    if not env.connect():
        print("[ERROR] Could not connect to game engine.", file=sys.stderr)
        sys.exit(1)

    try:
        if not args.command or args.command == "interactive":
            run_interactive(env)
        elif args.command == "start":
            st = env.start()
            print(json.dumps(st, indent=2))
        elif args.command == "freeze":
            frozen = env.freeze()
            print(f"[SUCCESS] Frozen: {frozen}")
        elif args.command == "unfreeze":
            unfrozen = env.unfreeze()
            print(f"[SUCCESS] Unfrozen: {unfrozen}")
        elif args.command == "step":
            st = env.step(steps=args.steps, action=args.action)
            print(json.dumps(st, indent=2))
        elif args.command == "act":
            act_val = int(args.action) if args.action.isdigit() else args.action
            st = env.act(act_val)
            print(json.dumps(st, indent=2))
        elif args.command == "speed":
            spd = env.tick_speed(args.scale)
            print(f"[SUCCESS] Simulation speed: {spd:.1f}x")
        elif args.command in ("metadata", "meta"):
            print(json.dumps(env.metadata, indent=2))
        elif args.command == "pause":
            ok = env.pause()
            print(f"[SUCCESS] Paused: {ok}")
        elif args.command == "resume":
            ok = env.resume()
            print(f"[SUCCESS] Resumed: {ok}")
        elif args.command == "exit":
            ok = env.exit()
            print(f"[SUCCESS] Exited to map: {ok}")
        elif args.command == "state":
            st = env.get_state()
            print(json.dumps(st, indent=2))
    finally:
        if args.command in ("start", "freeze", "step"):
            pass
        elif args.command in ("interactive", "exit"):
            env.close()


if __name__ == "__main__":
    main()
