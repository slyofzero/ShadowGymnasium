#!/usr/bin/env python3
r"""
Shadow Fight 2 — Universal In-Engine Action Controller & Speed Harness.
Directly invokes native C++ / IL2CPP functions inside the game engine:
- Master Loop Hook: battleCtrl.FixedUpdate (RVA 0x33F2A54) -> Universal across Dojo & all Fights!
- Player Pointer: battleCtrl + 0xB0 (LAMENNMGPBN)
- Exact Spatial Facing: Calculated via player_x vs opponent_x coordinates
- Action Down: OLKKAIFGGAK.IKPHKLHDNMA (RVA 0x34E90D0)
- Action Up: OLKKAIFGGAK.IJDCCIGHPHJ (RVA 0x34F57E0)
- Movement Halt: IPCACEBFONO.DGLJCGNHLIG (RVA 0x34E1B54) + Stick.ReleaseInput (RVA 0x306A558)
- Simulation Speed: UnityEngine.Time.set_timeScale (RVA 0x3BFB9C0)

Run this interactively in your terminal while in the Dojo or any match:
    python ./scripts/engine_controller.py
"""

import os
import shutil
import subprocess
import sys
import threading
import time

import frida

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


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
HARNESS_JS_PATH = os.path.join(SCRIPT_DIR, "frida", "engine_harness.js")


def load_harness_script() -> str:
    from scripts.common import load_frida_script
    return load_frida_script("engine_harness.js")


class SF2EngineController:
    def __init__(self, host=None, port=None):
        default_host, default_port = get_frida_endpoint()
        self.host = host or default_host
        self.port = port or default_port
        self.session = None
        self.script = None
        self.is_connected = False
        self.step_done_event = threading.Event()
        self.dash_done_event = threading.Event()
        self.attack_done_event = threading.Event()

    def connect(self) -> bool:
        # Auto-establish ADB port forward (no-op if direct IP / container)
        ensure_frida_port_forward(self.port)

        print(f"Connecting to Frida Gadget on {self.host}:{self.port}...")
        try:
            from scripts.common import attach_to_game
            device_manager = frida.get_device_manager()  # type: ignore[attr-defined]
            device = device_manager.add_remote_device(f"{self.host}:{self.port}")
            self.session = attach_to_game(device)
            self.script = self.session.create_script(load_harness_script())
            self.script.on("message", self._on_message)
            self.script.load()
            time.sleep(0.4)
            self.is_connected = True
            print("[SUCCESS] Attached to live game engine!\n")
            return True
        except Exception as e:
            print(f"[ERROR] Could not connect to game on port {self.port}: {e}")
            return False

    def _on_message(self, message, data):
        if message["type"] == "send":
            payload = message.get("payload", {})
            p_type = payload.get("type")
            if p_type == "STEP_DONE":
                self.step_done_event.set()
            elif p_type == "DASH_DONE":
                self.dash_done_event.set()
            elif p_type == "ATTACK_EXECUTED":
                self.attack_done_event.set()
        elif message["type"] == "error":
            print(f"[ENGINE-ERR] {message.get('stack', message)}")

    def step(self, quad, ticks=18):
        if not self.is_connected or not self.script:
            return False
        try:
            self.step_done_event.clear()
            self.script.exports_sync.step_move(quad, ticks)
            timeout = (ticks * 0.016) + 0.35
            self.step_done_event.wait(timeout=timeout)
            return True
        except Exception as e:
            print(f"[ERROR] Step failed: {e}")
            return False

    def dash(self, quad=3):
        if not self.is_connected or not self.script:
            return False
        try:
            self.dash_done_event.clear()
            self.script.exports_sync.trigger_dash(quad)
            self.dash_done_event.wait(timeout=0.9)
            return True
        except Exception as e:
            print(f"[ERROR] Dash failed: {e}")
            return False

    def hold(self, quad):
        if not self.is_connected or not self.script:
            return False
        try:
            self.script.exports_sync.hold_move(quad)
            return True
        except Exception as e:
            print(f"[ERROR] Hold failed: {e}")
            return False

    def stop(self):
        if not self.is_connected or not self.script:
            return False
        try:
            self.script.exports_sync.stop_move()
            return True
        except Exception as e:
            print(f"[ERROR] Stop failed: {e}")
            return False

    def attack(self, act_id, quad=0, hits=1):
        if not self.is_connected or not self.script:
            return False
        try:
            self.attack_done_event.clear()
            self.script.exports_sync.trigger_attack(act_id, quad, hits)
            timeout = 0.45 + (hits * 0.35)
            self.attack_done_event.wait(timeout=timeout)
            return True
        except Exception as e:
            print(f"[ERROR] Attack failed: {e}")
            return False

    def set_speed(self, scale):
        if not self.is_connected or not self.script:
            return False
        try:
            self.script.exports_sync.set_time_scale(float(scale))
            return True
        except Exception as e:
            print(f"[ERROR] Set speed failed: {e}")
            return False

    def act(self, action) -> bool:
        """Executes a combat or movement action by name or integer index."""
        if isinstance(action, int):
            if 0 <= action < len(ENGINE_ACTION_LIST):
                action = ENGINE_ACTION_LIST[action]
            else:
                return False

        key = str(action).lower().strip().replace(" ", "")
        handler = ENGINE_ACTION_MAP.get(key)
        if handler:
            return bool(handler(self))
        return False

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

    def get_status(self):
        if not self.is_connected or not self.script:
            return None
        try:
            return self.script.exports_sync.get_status()
        except Exception:
            return None


ENGINE_ACTION_MAP = {
    # Basic Attacks (act_id: 9=punch, 10=kick)
    "p": lambda c: c.attack(9, quad=0, hits=1),
    "punch": lambda c: c.attack(9, quad=0, hits=1),
    "pp": lambda c: c.attack(9, quad=0, hits=2),
    "k": lambda c: c.attack(10, quad=0, hits=1),
    "kick": lambda c: c.attack(10, quad=0, hits=1),
    "kk": lambda c: c.attack(10, quad=0, hits=2),
    # Compound Attacks
    "dp": lambda c: c.attack(9, quad=3, hits=1),
    "rightp": lambda c: c.attack(9, quad=3, hits=1),
    "ap": lambda c: c.attack(9, quad=7, hits=1),
    "leftp": lambda c: c.attack(9, quad=7, hits=1),
    "sp": lambda c: c.attack(9, quad=5, hits=1),
    "lowp": lambda c: c.attack(9, quad=5, hits=1),
    "wp": lambda c: c.attack(9, quad=1, hits=1),
    "upp": lambda c: c.attack(9, quad=1, hits=1),
    "dk": lambda c: c.attack(10, quad=3, hits=1),
    "rightk": lambda c: c.attack(10, quad=3, hits=1),
    "ak": lambda c: c.attack(10, quad=7, hits=1),
    "leftk": lambda c: c.attack(10, quad=7, hits=1),
    "sk": lambda c: c.attack(10, quad=5, hits=1),
    "lowk": lambda c: c.attack(10, quad=5, hits=1),
    "wk": lambda c: c.attack(10, quad=1, hits=1),
    "upk": lambda c: c.attack(10, quad=1, hits=1),
    # Double Attacks
    "dpp": lambda c: c.attack(9, quad=3, hits=2),
    "app": lambda c: c.attack(9, quad=7, hits=2),
    "spp": lambda c: c.attack(9, quad=5, hits=2),
    "wpp": lambda c: c.attack(9, quad=1, hits=2),
    "dkk": lambda c: c.attack(10, quad=3, hits=2),
    "akk": lambda c: c.attack(10, quad=7, hits=2),
    "skk": lambda c: c.attack(10, quad=5, hits=2),
    "wkk": lambda c: c.attack(10, quad=1, hits=2),
    # Diagonal Kicks
    "wdk": lambda c: c.attack(10, quad=2, hits=1),
    "wak": lambda c: c.attack(10, quad=8, hits=1),
    "sdk": lambda c: c.attack(10, quad=4, hits=1),
    "sak": lambda c: c.attack(10, quad=6, hits=1),
    # Fast Movements (Dashes)
    "dd": lambda c: c.dash(3),
    "dash": lambda c: c.dash(3),
    "dash_right": lambda c: c.dash(3),
    "aa": lambda c: c.dash(7),
    "dash_left": lambda c: c.dash(7),
    "backflip": lambda c: c.dash(7),
    # Compound Movements
    "wd": lambda c: c.step(2, ticks=18),
    "dw": lambda c: c.step(2, ticks=18),
    "wa": lambda c: c.step(8, ticks=18),
    "aw": lambda c: c.step(8, ticks=18),
    "sd": lambda c: c.step(4, ticks=22),
    "ds": lambda c: c.step(4, ticks=22),
    "sa": lambda c: c.step(6, ticks=22),
    "as": lambda c: c.step(6, ticks=22),
    # Basic Movements
    "w": lambda c: c.step(1, ticks=14),
    "up": lambda c: c.step(1, ticks=14),
    "jump": lambda c: c.step(1, ticks=14),
    "s": lambda c: c.step(5, ticks=16),
    "down": lambda c: c.step(5, ticks=16),
    "duck": lambda c: c.step(5, ticks=16),
    "a": lambda c: c.step(7, ticks=18),
    "left": lambda c: c.step(7, ticks=18),
    "d": lambda c: c.step(3, ticks=18),
    "right": lambda c: c.step(3, ticks=18),
    # Continuous & Stop
    "stop": lambda c: c.stop(),
    "noop": lambda c: c.stop(),
    "neutral": lambda c: c.stop(),
    "holdd": lambda c: c.hold(3),
    "holdright": lambda c: c.hold(3),
    "holda": lambda c: c.hold(7),
    "holdleft": lambda c: c.hold(7),
}

ENGINE_ACTION_LIST = [
    "noop",
    "p",
    "k",
    "w",
    "s",
    "a",
    "d",
    "dp",
    "sp",
    "wp",
    "ap",
    "dk",
    "sk",
    "wk",
    "ak",
    "wd",
    "wa",
    "sd",
    "sa",
    "dd",
    "aa",
    "pp",
    "kk",
    "dpp",
    "spp",
    "wpp",
    "app",
    "dkk",
    "skk",
    "wkk",
    "akk",
    "wdk",
    "wak",
    "sdk",
    "sak",
]


def print_help():
    print("""
============================================================
 Shadow Fight 2 — Universal In-Engine Controller
============================================================
 Works in the DOJO, Tournaments, Survival, & Bosses!
 Direct C++ IL2CPP Engine Execution (Zero Touch Lag)

 Basic Movements:
   w                       - Jump Up
   s                       - Duck / Crouch
   a                       - Step Left
   d                       - Step Right

 Compound Movements:
   wd                      - Jump Right (Up + Right)
   wa                      - Jump Left (Up + Left)
   sd                      - Roll Right (Down + Right)
   sa                      - Roll Left (Down + Left)

 Fast Movements:
   dd                      - Dash Right
   aa                      - Dash Left

 Basic Attacks:
   p                       - Single Neutral Punch / Knife Slash
   pp                      - Double Knife Slash Combo
   k                       - Single Kick
   kk                      - Double Kick Combo

 Compound Attacks: (w/a/s/d + p/k)
   dp                      - Right Knife Slash
   ap                      - Left Knife Slash
   sp                      - Low Knife Slash
   wp                      - Upward Rising Slash

   dk                      - Right Step Kick
   ak                      - Left High Kick
   sk                      - Low Sweep Kick
   wk                      - Flying Jump Kick

 Double Attacks:
   dpp                     - Right Double Slash (Super Slash!)
   app                     - Left Double Slash
   spp                     - Low Double Slash
   wpp                     - Upward Double Slash

   dkk                     - Right Double Kick
   akk                     - Left Double Kick
   skk                     - Low Double Sweep
   wkk                     - Flying Double Kick

 Diagonal Kicks: (w/s)(d/a) + k
   wdk                     - Up-Right Jump Kick
   wak                     - Up-Left Jump Kick
   sdk                     - Down-Right Slide Kick
   sak                     - Down-Left Dodge Kick

 Continuous & Speed:
   hold d / hold a         - Continuous walk
   stop                    - Stop immediately
   speed <val>             - Simulation speed scale (e.g. speed 1.0, speed 2.0)
   status                  - Shadow HP & Facing status
   help                    - Show this menu
   q / quit / exit         - Exit
============================================================
""")


def repl():
    controller = SF2EngineController()
    if not controller.connect():
        sys.exit(1)

    print_help()

    while True:
        try:
            user_input = input("SF2-Engine > ").strip().lower()
            if not user_input:
                continue

            # Standardized command without spaces
            compact = user_input.replace(" ", "")

            if compact in ["q", "quit", "exit"]:
                controller.stop()
                print("Exiting engine controller. Goodbye!")
                break

            if compact == "help":
                print_help()
                continue

            if compact == "stop":
                controller.stop()
                print(" -> Movement Stopped & Halted (Neutral)")
                continue

            if compact == "status":
                st = controller.get_status()
                if st:
                    p1_hp = f"{st.get('player_hp', 1.0) * 100:.1f}%"
                    facing = "LEFT" if st.get("facing_left") else "RIGHT"
                    print(f" -> Status: Engine Active | Shadow HP: {p1_hp} | Facing: {facing}")
                continue

            if user_input.startswith("speed "):
                parts = user_input.split()
                if len(parts) >= 2:
                    try:
                        val = float(parts[1])
                        controller.set_speed(val)
                        print(f" -> Simulation Speed set to {val}x")
                    except ValueError:
                        print(" -> Invalid speed value. Example: speed 1.5")
                continue

            # Continuous Hold
            if user_input in ["hold right", "hold d"] or compact == "holdd":
                controller.hold(3)
                print(" -> Holding RIGHT (continuous walk). Type 'stop' to halt.")
                continue
            elif user_input in ["hold left", "hold a"] or compact == "holda":
                controller.hold(7)
                print(" -> Holding LEFT (continuous walk). Type 'stop' to halt.")
                continue

            # 1. Fast Movements (dd = Dash Right, aa = Dash Left)
            if compact in ["dd", "dash_right", "dash"]:
                controller.dash(3)
                print(" -> Fast Movement: DASH RIGHT (dd)")
                continue
            elif compact in ["aa", "dash_left", "backflip"]:
                controller.dash(7)
                print(" -> Fast Movement: DASH LEFT (aa)")
                continue

            # 2. Compound Movements (wa, wd, sa, sd)
            if compact in ["wd", "dw", "jump_right"]:
                controller.step(2, ticks=18)
                print(" -> Compound Movement: JUMP RIGHT (wd)")
                continue
            elif compact in ["wa", "aw", "jump_left"]:
                controller.step(8, ticks=18)
                print(" -> Compound Movement: JUMP LEFT (wa)")
                continue
            elif compact in ["sd", "ds", "roll_right"]:
                controller.step(4, ticks=22)
                print(" -> Compound Movement: ROLL RIGHT (sd)")
                continue
            elif compact in ["sa", "as", "roll_left"]:
                controller.step(6, ticks=22)
                print(" -> Compound Movement: ROLL LEFT (sa)")
                continue

            # 3. Basic Movements (w, a, s, d)
            if compact in ["w", "up", "jump"]:
                controller.step(1, ticks=14)
                print(" -> Basic Movement: JUMP UP (w)")
                continue
            elif compact in ["s", "down", "duck"]:
                controller.step(5, ticks=16)
                print(" -> Basic Movement: DUCK / CROUCH (s)")
                continue
            elif compact in ["a", "left"]:
                controller.step(7, ticks=18)
                print(" -> Basic Movement: STEP LEFT (a)")
                continue
            elif compact in ["d", "right"]:
                controller.step(3, ticks=18)
                print(" -> Basic Movement: STEP RIGHT (d)")
                continue

            # 4. Double Attacks (dpp, app, skk, wkk, etc.)
            if compact in ["dpp", "rightpp", "superslash", "super_slash"]:
                controller.attack(9, quad=3, hits=2)
                print(" -> Double Attack: RIGHT DOUBLE SLASH (dpp)")
                continue
            elif compact in ["app", "leftpp"]:
                controller.attack(9, quad=7, hits=2)
                print(" -> Double Attack: LEFT DOUBLE SLASH (app)")
                continue
            elif compact in ["spp", "lowpp", "downpp"]:
                controller.attack(9, quad=5, hits=2)
                print(" -> Double Attack: LOW DOUBLE SLASH (spp)")
                continue
            elif compact in ["wpp", "uppp"]:
                controller.attack(9, quad=1, hits=2)
                print(" -> Double Attack: UPWARD DOUBLE SLASH (wpp)")
                continue

            elif compact in ["dkk", "rightkk"]:
                controller.attack(10, quad=3, hits=2)
                print(" -> Double Attack: RIGHT DOUBLE KICK (dkk)")
                continue
            elif compact in ["akk", "leftkk"]:
                controller.attack(10, quad=7, hits=2)
                print(" -> Double Attack: LEFT DOUBLE KICK (akk)")
                continue
            elif compact in ["skk", "lowkk", "downkk", "doublesweep"]:
                controller.attack(10, quad=5, hits=2)
                print(" -> Double Attack: LOW DOUBLE SWEEP (skk)")
                continue
            elif compact in ["wkk", "upkk", "jumpdoublekick"]:
                controller.attack(10, quad=1, hits=2)
                print(" -> Double Attack: FLYING DOUBLE KICK (wkk)")
                continue

            # 5. Compound Attacks (wp, ap, sp, dp, wk, ak, sk, dk)
            if compact in ["dp", "rightp", "right_punch"]:
                controller.attack(9, quad=3, hits=1)
                print(" -> Compound Attack: RIGHT KNIFE SLASH (dp)")
                continue
            elif compact in ["ap", "leftp", "left_punch"]:
                controller.attack(9, quad=7, hits=1)
                print(" -> Compound Attack: LEFT KNIFE SLASH (ap)")
                continue
            elif compact in ["sp", "downp", "lowpunch", "lowp"]:
                controller.attack(9, quad=5, hits=1)
                print(" -> Compound Attack: LOW KNIFE SLASH (sp)")
                continue
            elif compact in ["wp", "upp", "up_punch", "uppunch"]:
                controller.attack(9, quad=1, hits=1)
                print(" -> Compound Attack: UPWARD RISING SLASH (wp)")
                continue

            elif compact in ["dk", "rightk", "right_kick"]:
                controller.attack(10, quad=3, hits=1)
                print(" -> Compound Attack: RIGHT STEP KICK (dk)")
                continue
            elif compact in ["ak", "leftk", "left_kick"]:
                controller.attack(10, quad=7, hits=1)
                print(" -> Compound Attack: LEFT HIGH KICK (ak)")
                continue
            elif compact in ["sk", "downk", "lowkick", "lowk", "sweep"]:
                controller.attack(10, quad=5, hits=1)
                print(" -> Compound Attack: LOW SWEEP KICK (sk)")
                continue
            elif compact in ["wk", "upk", "up_kick", "jumpkick"]:
                controller.attack(10, quad=1, hits=1)
                print(" -> Compound Attack: FLYING JUMP KICK (wk)")
                continue

            # 5b. Diagonal Kicks: (w/s)(d/a)k — Up-Right, Up-Left, Down-Right, Down-Left
            elif compact in ["wdk", "uprightk", "frontjumpkick"]:
                controller.attack(10, quad=2, hits=1)
                print(" -> Diagonal Kick: UP-RIGHT JUMP KICK (wdk)")
                continue
            elif compact in ["wak", "upleftk"]:
                controller.attack(10, quad=8, hits=1)
                print(" -> Diagonal Kick: UP-LEFT JUMP KICK (wak)")
                continue
            elif compact in ["sdk", "downrightk", "slidekick"]:
                controller.attack(10, quad=4, hits=1)
                print(" -> Diagonal Kick: DOWN-RIGHT SLIDE KICK (sdk)")
                continue
            elif compact in ["sak", "downleftk", "dodgekick"]:
                controller.attack(10, quad=6, hits=1)
                print(" -> Diagonal Kick: DOWN-LEFT DODGE KICK (sak)")
                continue

            # 6. Basic Attacks & Combos (p, pp, k, kk)
            if compact in ["pp", "doublepunch"]:
                controller.attack(9, quad=0, hits=2)
                print(" -> Basic Combo: DOUBLE PUNCH (pp)")
                continue
            elif compact in ["p", "punch"]:
                controller.attack(9, quad=0, hits=1)
                print(" -> Basic Attack: NEUTRAL PUNCH (p)")
                continue

            elif compact in ["kk", "doublekick"]:
                controller.attack(10, quad=0, hits=2)
                print(" -> Basic Combo: DOUBLE KICK (kk)")
                continue
            elif compact in ["k", "kick"]:
                controller.attack(10, quad=0, hits=1)
                print(" -> Basic Attack: NEUTRAL KICK (k)")
                continue

            else:
                print(f" -> Unknown command: '{user_input}'. Type 'help' for instructions.")

        except (KeyboardInterrupt, EOFError):
            controller.stop()
            print("\nExiting.")
            break


if __name__ == "__main__":
    repl()
