# Project Deep-Dive & Engineering Interview Guide
## Transforming *Shadow Fight 2* into an OpenAI Gymnasium Reinforcement Learning Environment

> **Author**: Ishan Shishodiya  
> **Target Roles**: AI Research Engineer, Systems Engineer, Native/Security Reverse Engineer, Robotics & Simulation Engineer  
> **Key Focus Areas**: Systems Architecture, Android/Unity Internals, Dynamic Binary Instrumentation, Low-Latency IPC, Deterministic Simulation, Reinforcement Learning

---

## 📑 Quick Navigation

0. [Foundational Glossary & Reverse Engineering Primer](#0-foundational-glossary--reverse-engineering-primer)
1. [Executive Summary & High-Level Pitch](#1-executive-summary--high-level-pitch)
2. [High-Level Architectural Overview](#2-high-level-architectural-overview)
3. [End-to-End Build Journey: How I Built It](#3-end-to-end-build-journey-how-i-built-it)
4. [The 8 Major Engineering Blockers & Technical Resolutions](#4-the-8-major-engineering-blockers--technical-resolutions)
5. [In-Depth Technical Q&A ("The Grill-Me Questions")](#5-in-depth-technical-qa-the-grill-me-questions)
6. [Quantitative Impact & Benchmark Metrics](#6-quantitative-impact--benchmark-metrics)
7. [Engineering Reflections & Future Roadmap](#7-engineering-reflections--future-roadmap)

---

## 0. Foundational Glossary & Reverse Engineering Primer

> [!NOTE]
> **Use this section to ground your understanding.** If an interviewer asks *"What does ADB stand for?"*, *"What is IL2CPP?"*, or *"Walk me through the exact steps of repacking an APK"*, everything you need is right here.

### 📚 Master Keyword Dictionary

| Term / Acronym | Full Form | What It Is & What It Does in This Project |
| :--- | :--- | :--- |
| **ADB** | **Android Debug Bridge** | A command-line tool that lets your host computer (Windows/Linux) communicate with an Android device or emulator. Used to install APKs, run shell commands (`screencap`), inspect logs via `logcat`, and **forward TCP ports** (e.g. `adb forward tcp:27042 tcp:27042`) so Python can talk to the game. |
| **APK** | **Android Package Kit** | The file format Android uses to distribute and install apps. Under the hood, an APK is just a **ZIP archive** containing compiled Dalvik bytecode (`classes.dex`), native C/C++ libraries (`lib/`), assets (`assets/`), and an XML manifest (`AndroidManifest.xml`). |
| **XAPK** | **eXtended Application Package** | An unofficial container format created by sites like APKPure. It bundles the main base APK along with architecture-specific **split APKs** and external asset packs (`.obb`). Standard Android installers and BlueStacks reject `.xapk` files directly. |
| **Split APK** | **Split Android Package** | A modern Google Play deployment mechanism where an app is split into pieces (e.g., `base.apk`, `config.arm64_v8a.apk`, `config.xxhdpi.apk`) to save user download bandwidth. We had to merge these splits into a single **monolithic APK** so the emulator could install it cleanly. |
| **DEX** | **Dalvik Executable** | The compiled bytecode format (`classes.dex`) that Android's runtime (ART / Dalvik) executes. It is compiled from Java or Kotlin code. |
| **Smali** | *Assembly for DEX* (Named after the Icelandic word for "assembler") | The human-readable assembly language representation of `.dex` bytecode. When you "decompile" an Android app's Java code without source, you get Smali code. We wrote our startup hook in Smali (`AssetExtractor.smali`). |
| **Apktool** | **APK Reverse Engineering Tool** | An open-source tool used to decode an APK into its project source files (Smali bytecode, decoded `AndroidManifest.xml`, graphics assets) and rebuild them back into an installable `.apk`. |
| **Zipalign** | **Zip Archive Alignment Tool** | An Android SDK optimization tool that ensures all uncompressed assets inside the APK ZIP file are aligned to 4-byte memory boundaries. This allows the Android OS to memory-map (`mmap`) files directly into RAM without copying. |
| **APK Signer** | **Android Cryptographic Signature Tool** | Android requires every APK to be cryptographically signed by a developer key before installation. Tools like `uber-apk-signer` apply **v1 (JAR signing)**, **v2 (APK Signature Scheme)**, and **v3 schemes** to prove the file has not been corrupted or tampered with. |
| **Unity** | **Unity Game Engine** | The cross-platform game development engine that *Shadow Fight 2* is built on. Unity manages rendering, asset serialization, physics update loops, and scene transitions. |
| **IL2CPP** | **Intermediate Language to C++** | Unity's proprietary Ahead-of-Time (AOT) compiler. It converts .NET C# bytecode (Intermediate Language / IL) into high-performance **native C++ code**, which is then compiled directly into machine code (`libil2cpp.so`). This makes reverse engineering much harder because there are no readable C# DLLs. |
| **`libil2cpp.so`** | **IL2CPP Shared Object Library** | The compiled ELF Linux shared library (`.so`) containing the entire game's compiled C# gameplay logic, math routines, combat controllers, and anti-cheat checks in raw ARM64 machine code. |
| **`global-metadata.dat`** | **IL2CPP Global Metadata Table** | Because C++ machine code strips away high-level C# reflection data, Unity stores all class names, method signatures, field offsets, and string literals in this single binary file. Without it, `libil2cpp.so` is just raw anonymous assembly instructions. |
| **Il2CppDumper** | **IL2CPP Metadata Recovery Tool** | An open-source reverse engineering tool. By feeding it `libil2cpp.so` and `global-metadata.dat`, it parses the tables and outputs a readable C# dummy project (`dump.cs`) with exact function names and Relative Virtual Addresses (RVAs). |
| **ARM64 / AArch64** | **ARM 64-bit Architecture** | The CPU architecture used by modern mobile processors and emulated by Android emulators. All instructions in `libil2cpp.so` are 32-bit fixed-length ARM64 opcodes (e.g., `mov`, `ldr`, `str`, `b`, `ret`). |
| **RVA** | **Relative Virtual Address** | The memory offset of a function or variable relative to the base address where a shared library is loaded in RAM. For example, if `libil2cpp.so` is loaded at `0x70000000` and a function has RVA `0x3237268`, its active memory address is `0x73237268`. |
| **Assembly Patching** | **Binary Bytecode Modification** | Altering compiled machine code instructions in an existing binary without recompiling from source. For example, replacing a complex anti-cheat function with the 4-byte ARM64 opcode `mov w0, #1; ret` to force it to return "validation succeeded". |
| **Frida** | **Dynamic Binary Instrumentation (DBI)** | A framework that allows researchers to inject custom JavaScript scripts into a running native process. It can hook functions, read/write memory pointers, intercept parameters, and call internal C++ methods live. |
| **Frida Gadget** | **Embedded Native Frida Shared Library (`libfrida-gadget.so`)** | A standalone dynamic library version of Frida. Instead of requiring a rooted device running a background `frida-server` daemon, you place `libfrida-gadget.so` inside the APK itself and load it on app boot. |
| **JSON-RPC** | **JSON Remote Procedure Call** | A remote execution protocol using JSON format. Frida exposes Python-to-JavaScript communication (`rpc.exports`) over a local TCP socket so Python can say `api.step(10)` or `api.read_state()`. |
| **Gymnasium** | **OpenAI Gymnasium (formerly Gym)** | The standard Python interface for Reinforcement Learning environments. It formalizes agent-environment interactions through a standardized loop: `obs, reward, terminated, truncated, info = env.step(action)`. |
| **`FixedUpdate`** | **Unity Physics Update Loop** | A native Unity engine lifecycle method called at fixed time intervals (usually 60 Hz = every 16.6ms). Used for physics simulation, hitbox collision, and animation stepping. We hijacked this function to create our deterministic tick controller. |
| **ObscuredFloat** | **CodeStage Anti-Cheat Protected Float** | An encrypted floating-point struct used in games to prevent memory tampering (e.g. Cheat Engine). It stores an XOR encryption key alongside scrambled byte unions. We hooked the native engine decryptor `ALBJPLAPOBO` to read live HP values. |
| **evdev** | **Linux Event Device Subsystem (`/dev/input/event*`)** | The Linux kernel interface that receives raw multi-touch and keyboard hardware events. We used this to stream binary input packets directly to the Android OS. |

---

### 🔍 How APK Creation & Reverse Engineering Actually Happened: A Plain-English Walkthrough

If an interviewer asks you: *"How did you actually get inside this game and modify it?"*, here is the exact mental model to describe:

#### Step 1: Realizing an APK is Just a ZIP File
Many people think an APK is an impenetrable black box. In reality:
* Rename `ShadowFight2.apk` to `ShadowFight2.zip`, and you can extract it right on your desktop.
* Inside, you find `AndroidManifest.xml` (the permissions & activities map), `classes.dex` (the compiled Java code for Android lifecycle), `assets/` (textures, sounds, game data), and `lib/arm64-v8a/` (the native compiled C++ game engine: `libunity.so` and `libil2cpp.so`).

#### Step 2: The Unity IL2CPP Challenge
In regular Android apps or older Unity games, you can decompile `.dex` or `.dll` files back into readable Java or C# code. But *Shadow Fight 2* uses **IL2CPP**:
* Unity took the developers' C# scripts, converted them to C++, and compiled them into raw machine code (`libil2cpp.so`).
* However, Unity still needs to know class names and variable types at runtime for reflection. It stores all of these names in `assets/bin/Data/Managed/Metadata/global-metadata.dat`.
* By running **Il2CppDumper** on `libil2cpp.so` + `global-metadata.dat`, we generated `dump.cs` — a complete header map revealing thousands of class names, function names, and their exact hexadecimal memory addresses (RVAs).

#### Step 3: Finding and Patching the Anti-Cheat
* In `dump.cs`, we searched for keywords like `AntiCheat`, `HackTitle`, `ValidateSave`, and `Health`.
* We found `EOOEOHKLOFJ` (the corruption detector) and `CFJANHACGMG.AMELFFLEPNF` (the save hash validator).
* We opened `libil2cpp.so` in **Ghidra** (a free reverse engineering tool by the NSA) or a hex editor at those exact RVAs.
* Instead of letting the function run its complex validation checks, we overwrote the first instruction with:
  * `mov w0, #1` (Put the number 1 / `true` into the return register `w0`)
  * `ret` (Immediately return from the function)
* The game now blindly believes every save file is authentic and zero hacks were detected!

#### Step 4: Injecting the Startup Smali Hook (`AssetExtractor.smali`)
* The game required 156 MB of extra assets from a remote CDN that would block fresh installations.
* We unpacked the APK using **Apktool**, which converted `classes.dex` into readable **Smali assembly files**.
* We located Android's `Application.onCreate()` method — the very first piece of code that runs when the user taps the app icon, even before the Unity engine boots.
* We wrote a custom class in Smali (`AssetExtractor.smali`) and hooked it into `onCreate()`.
* On launch, it checks external storage. If files are missing, it extracts all 34 pre-bundled asset packs from inside the APK's `assets/` folder to `/sdcard/Android/data/...` in under 250 milliseconds.
* It also calls `System.loadLibrary("frida-gadget")` to start our debugging server.

#### Step 5: Repacking, Aligning, and Signing
* Android will refuse to run modified files unless they are packaged correctly:
  1. **Rebuild**: `apktool b` compresses the Smali code and assets back into an `.apk`.
  2. **Zipalign**: `zipalign -v 4` aligns uncompressed data to 4-byte boundaries so the Linux kernel can memory-map it directly.
  3. **Sign**: `uber-apk-signer` attaches cryptographic v1/v2/v3 signatures using a 2048-bit RSA key. Android now accepts it as a completely valid, trusted app.

#### Step 6: Frida Gadget — The "In-Process Spy"
* Once the app runs, the embedded `libfrida-gadget.so` opens a TCP socket on port `27042`.
* Our Python script connects to this socket over ADB.
* Frida gives us the power to inject JavaScript directly into the running game's memory space:
  * We can intercept functions (`Interceptor.replace`).
  * We can read variables and XOR-decrypted floats in RAM.
  * We can call native C++ functions like `battleCtrl.FixedUpdate()` on command.
* This transformed a closed mobile game into a transparent, scriptable Reinforcement Learning environment!

---

---

## 1. Executive Summary & High-Level Pitch

### 🎤 The 60-Second Elevator Pitch
> *"I transformed **Shadow Fight 2**, a commercial, closed-source Unity mobile fighting game, into an **OpenAI Gymnasium environment** capable of training autonomous Reinforcement Learning agents.*
>
> *Commercial mobile games are traditionally terrible environments for RL because they act as non-deterministic black boxes: they require real-time human interaction, run unskippable 15-second victory/defeat animations, lock simulation clocks to real-world time, and restrict state observation to noisy screen captures.
>
> *To solve this, I reverse engineered the game's compiled **ARM64 IL2CPP native binaries**, bypassed multi-tiered anti-cheat and DLC gating, embedded a **rootless Frida Gadget daemon** directly into a repackaged APK, hijacked Unity's internal physics clock to enable **discrete deterministic tick stepping**, and hooked in-memory combat state routines.
>
> *The result is a production-grade Gymnasium interface that delivers **sub-millisecond spatial telemetry**, enables **instant < 10ms episode soft-resets** (a 1,500× speedup over UI reloads), and dispatches actions directly into the engine's combo solver synchronously with zero input latency."*

---

## 2. High-Level Architectural Overview

### The Problem with Vision-Only RL in Commercial Games
The standard naive approach to applying RL to mobile games is **screen scraping**: grabbing video frames via ADB or screen capture, passing them through a CNN, and injecting touches via ADB (`adb shell input tap`).

This fails for three reasons:
1. **Perception Latency & Noise**: Grabbing 1080p frames and processing them through OCR or vision models adds 50–150ms of latency, dropping frames and obscuring true physics velocity.
2. **Episode Reset Bottleneck**: Fighting games have unskippable victory cutscenes, level-up dialogs, stamina depletion, and interstitial ads. Resetting an episode takes 15–20 seconds, meaning 90% of training time is spent waiting on menus rather than learning policies.
3. **Non-Deterministic Clocks**: Fighting games run in real-time (60 FPS). If the agent's inference takes 25ms, the game continues simulating, introducing frame desync and non-deterministic state transitions.

### The Solution: 3-Tier In-Engine Architecture
Rather than treating the game as a black box, the system hooks directly into the engine's internal memory bus and update cycle.

```
┌────────────────────────────────────────────────────────────────────────┐
│                        TIER 1: PYTHON & GYMNASIUM                      │
│   ShadowFightEnv (gymnasium.Env)  │  SF2EngineController (Actions)     │
│   SF2TickController (Time/Freeze) │  SF2TelemetryStreamer (Telemetry)  │
│   Unified CLI Suite: sf2-env, sf2-frida, sf2-tick, sf2-telemetry       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ JSON-RPC over TCP Socket (:27042)
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        TIER 2: INSTRUMENTATION BRIDGE                  │
│   start_frida_service.py (Watchdog & Health Monitor)                   │
│   ADB Client Port Forwarding (adb forward tcp:27042 tcp:27042)         │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Direct Process Memory Access
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                   TIER 3: MODDED ANDROID & IL2CPP ENGINE               │
│  SF2_Modded_v8.apk (Universal Standalone Monolithic APK)               │
│  ├── AssetExtractor.smali: Injects libfrida-gadget.so on boot          │
│  ├── libil2cpp.so: Patched ARM64 assembly (anti-cheat & save bypass)   │
│  └── Injected Frida Hooks:                                             │
│      ├── FixedUpdate Hijack: Discrete tick budget & frozen physics     │
│      ├── Action Dispatcher: Direct invocation of OLKKAIFGGAK           │
│      ├── In-Memory State Reader: XOR-decrypted ObscuredFloat HP,       │
│      │   3D Vector3 transforms, hit badge events, facing resolution    │
│      └── Instant Soft-Reset: In-memory HP rewrite bypassing game-over  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 3. End-to-End Build Journey: How I Built It

### Phase 1: APK Reverse Engineering & Monolithic Standalone Packaging
1. **Deconstructing Split XAPKs**: The upstream game was distributed as a multi-split XAPK (`base.apk` + `config.arm64_v8a.apk`). Android emulators like BlueStacks rejected this as an "unsupported file format". I wrote an automated pipeline (`build_standalone_apk.py`) to merge split native libraries into a single universal, monolithic APK with v1, v2, and v3 Android signatures.
2. **Application Rebranding & Side-by-Side Coexistence**: Changed the package identifier to `com.nekki.catblasters` to allow testing alongside the unmodified production game. Replaced all provider authorities and performed an exact-byte 21-character string replacement in `global-metadata.dat` to maintain Unity metadata alignment.

### Phase 2: Neutralizing Client-Side Barriers (Offline Provisioning & Anti-Cheat)
1. **The 13.12 MB / 31.58 MB CDN Download Lock**: Fresh game installs refused to fight without pulling chapter assets and versional configs from remote CDNs. I pre-bundled all 34 offline bundles (~156 MB) directly into the APK's `assets/gamedata/` directory.
2. **Dalvik Startup Hook (`AssetExtractor.smali`)**: Mobile games decompress assets via native file APIs that cannot read directly from compressed APK zip streams. I wrote a smali bytecode hook targeting Android's `Application.onCreate()`. On first launch, before Unity initializes, it auto-extracts all assets and completed user saves to `/sdcard/Android/data/...` in under 250ms.
3. **Multi-Tier Anti-Cheat Neutralization**: Modifying save files triggered the game's `EOOEOHKLOFJ` anti-cheat controller, resulting in a fatal "Data Corrupted" dialog. Using Ghidra and `Il2CppDumper`, I traced the validation call stack and patched three ARM64 subroutines to unconditional returns (`mov w0, #1; ret`), permanently disabling integrity checks.

### Phase 3: Rootless Dynamic Binary Instrumentation via Frida Gadget
1. **Rootless Injection**: Rather than requiring a rooted emulator with a fragile background `frida-server` daemon, I embedded ARM64 `libfrida-gadget.so` directly into `lib/arm64-v8a/` inside the APK.
2. **Automated Gadget Bootstrap**: Extended `AssetExtractor.smali` with `System.loadLibrary("frida-gadget")`. When the game boots, Frida Gadget initializes inside the process space, binding an in-memory JSON-RPC server on TCP port `27042`.

### Phase 4: Hijacking Physics & Clock Determinism
1. **The Real-Time Dilemma**: Fighting games update animations and physics inside Unity's `FixedUpdate` (at 60 Hz). If the Python environment takes time to calculate actions, physics continues moving.
2. **Tick Stepping Engine**: Replaced `battleCtrl.FixedUpdate` (`0x33F2A54`) using Frida's `Interceptor.replace`. Physics computation only advances when a discrete tick budget (`ticksBudget > 0`) is granted by the Python harness. Calling `env.step(N)` advances the simulation by exactly $N$ ticks and freezes immediately, giving the agent infinite time to think without physics drift.

### Phase 5: Direct In-Memory Telemetry & Action Dispatch
1. **Sub-Millisecond State Extraction**: Reversed character transforms and health pointers. Created an in-memory XOR decryptor to extract real-time `ObscuredFloat` health values, 3D `Vector3` spatial coordinates, animation hashes, and hit badge triggers directly from memory in < 1ms.
2. **Bypassing the Touch Subsystem**: Emulating multi-touch via OS events takes 400ms+ and suffers from race conditions. I reversed the game's input receiver (`OLKKAIFGGAK.IKPHKLHDNMA` / `IJDCCIGHPHJ`) and hooked action dispatches directly into the character's combo state machine synchronously during the physics tick.

### Phase 6: Gym Environment Architecture & Instant Soft-Reset
1. **Gymnasium Environment (`ShadowFightEnv`)**: Wrapped the low-level hooks into standard Gymnasium abstractions (`step()`, `reset()`, `action_space`, `observation_space`).
2. **Sub-10ms Soft-Reset**: When a fighter's HP drops to zero, instead of waiting 15 seconds for victory animations and menu transitions, the harness intercepts the death event and rewrites character health pointers directly in memory, resetting the match in **< 10ms** (a 1,500× speedup).

---

## 4. The 8 Major Engineering Blockers & Technical Resolutions

Here are the most significant technical hurdles encountered during development and how they were systematically diagnosed and resolved.

---

### 🔴 Blocker 1: The "Unsupported File Type" & Conflicting Provider Installs
* **Symptom**: Dragging the official game `.xapk` into BlueStacks failed with "unsupported file format". When extracted manually, installing `base.apk` failed because architecture-specific native libraries were split across separate `.apk` files (`config.arm64_v8a.apk`). Furthermore, attempting to install a modded test APK alongside the original failed with Android's `INSTALL_FAILED_CONFLICTING_PROVIDER`.
* **Root Cause**: Modern Android distributions use split APKs to reduce bandwidth, which emulators cannot mount as monolithic packages. Additionally, Android enforces system-wide uniqueness on `ContentProvider` authorities across all installed apps.
* **Resolution**:
  1. Developed `modding/pipeline/build_standalone_apk.py` to extract `lib/arm64-v8a/` and `lib/armeabi-v7a/` from the split packages, merge them directly into the base package, and re-sign with `uber-apk-signer.jar` (v1, v2, v3 schemes).
  2. For side-by-side installation, decoded the APK with `apktool`, renamed all package strings and provider authorities to `com.nekki.catblasters`, and performed an **exact-byte 21-character replacement** (`com.nekki.shadowfight` $\to$ `com.nekki.catblasters`) in `global-metadata.dat` to ensure the compiled IL2CPP symbol tables remained byte-aligned without corruption.

---

### 🔴 Blocker 2: The 13.12 MB / 31.58 MB CDN Download Barrier on Fresh Installs
* **Symptom**: Fresh APK installations immediately halted at boot with a modal: `"LOADING REQUIRED! SIZE: 13.12 MB"` (and later 31.58 MB for live events). Combat could not start without an active internet connection to download asset bundles.
* **Root Cause**: Unity IL2CPP stores core animation hashes and versional configs in persistent external storage (`/sdcard/Android/data/<package>/files/gamedata/bundles/`). When an APK is freshly installed, this folder is empty, triggering an unskippable CDN download sequence in `BEPJMNAHCAD$$GIENGNEGHPJ`.
* **Resolution**:
  1. Built an automated CDN scraper (`download_all_story_packs.py`) to discover and pull all 34 remote asset bundles (~156 MB).
  2. Embedded these bundles directly into the APK under `assets/gamedata/`.
  3. Engineered `AssetExtractor.smali`, an assembly hook injected into `classes.dex` on `MultiDexApplication.onCreate()`. On first launch, before Unity or IL2CPP boots, the Java layer decompresses all bundles directly to external storage in under 250ms and writes a `.provisioned_v4` marker file. Subsequent launches read the marker and proceed with zero extraction overhead.

---

### 🔴 Blocker 3: The Fatal "Data Corrupted" Anti-Cheat Panic (`EOOEOHKLOFJ`)
* **Symptom**: Providing a pre-configured save profile (`users.xml`) to bypass tutorials and unlock tournaments caused the game to trigger a modal: `"Your game data may be corrupted"` (`HackTitle`) and force-quit to the Android desktop.
* **Root Cause**: The game implements a multi-layer anti-cheat system. Layer 1 checks an MD5 hash of `users.xml`. Layer 2 (`EOOEOHKLOFJ`) inspects currency, unlocked weapons, and level bounds at runtime. If tampered values are detected, `EOOEOHKLOFJ$$MACGEGDGBOI` (`0x30c1250`) is called to display the corruption screen and terminate the process.
* **Resolution**:
  1. Reverse engineered `libil2cpp.so` using `Il2CppDumper` and Ghidra.
  2. Neutralized all three layers of the anti-cheat controller via binary ARM64 assembly patches:
     * `EOOEOHKLOFJ$$EOJDBAHODGK` (`0x30c3344`): Patched to `mov w0, #1; ret` (reports validation success).
     * `EOOEOHKLOFJ$$MACGEGDGBOI` (`0x30c1250`): Patched to `ret; nop` (neutralizes dialog creation).
     * `EOOEOHKLOFJ$$HBGCPAEJCJJ` (`0x30c11d8`): Patched to `mov w0, #0; ret` (reports 0 hacks detected).
     * XML Hash Validator `CFJANHACGMG.AMELFFLEPNF` (`0x3598568`): Patched to `mov w0, #1; ret`.

---

### 🔴 Blocker 4: 400ms Input Latency & Accidental Walking (OS Touch Bottleneck)
* **Symptom**: Early input automation used shell-based commands (`adb shell input tap` or `sendevent`). High-speed combat combos failed completely: the character would take an unintended walking step forward before punching, and rapid double-taps (`D, J+J` for weapon super slashes) failed to register.
* **Root Cause**:
  * Shell `sendevent` requires 9 to 15 OS system calls per touch, spawning child processes with ~427ms of latency.
  * In fighting games, combos require holding a directional quadrant simultaneously with a strike button within a 200ms animation buffer window. If the joystick press is delayed by even 50ms, the engine treats it as a sequential walk input followed by a neutral strike.
* **Resolution**:
  1. *Intermediate Fix*: Created a binary event streamer (`action_client.py`) that packed raw Linux `struct input_event` (24 bytes: `<qqHHi`) into an unbuffered persistent pipe (`cat > /dev/input/event4`), reducing OS event dispatch from 427ms to 0.7ms.
  2. *Ultimate In-Engine Fix*: Completely bypassed the Android touch subsystem. Reverse engineered the IL2CPP combat input pipeline (`OLKKAIFGGAK.IKPHKLHDNMA` for `ActionDown` and `OLKKAIFGGAK.IJDCCIGHPHJ` for `ActionUp`). Exposed native C++ function calls directly through Frida, injecting actions directly into the character's internal combo buffer synchronously with the physics tick.

---

### 🔴 Blocker 5: Static Health Reading & CodeStage `ObscuredFloat` Encryption
* **Symptom**: During live combat, reading character health from memory offsets returned a static, unchanging float (`0.5176`), even when the character took massive damage.
* **Root Cause**: The developers protected sensitive variables using CodeStage's `Anti-Cheat Toolkit`. Variables like HP are stored as `ObscuredFloat`, an encrypted struct consisting of a 4-byte `currentCryptoKey` and an obfuscated union of byte arrays. Reading the memory address directly yielded raw ciphertext.
* **Resolution**:
  1. Traced the native memory layout of `PJKHAJKEHEL` (`fighter + 0x148`): Max HP is at offset `+0xF4` and Current HP is at offset `+0x208`.
  2. Located the engine's internal decryption subroutine `ALBJPLAPOBO` at RVA `0x1BC1F2C`.
  3. Rather than reimplementing complex rolling XOR crypto in JavaScript, wrote a Frida hook that invokes `ALBJPLAPOBO` directly using `NativeFunction`, passing the encrypted memory pointer and extracting true, decrypted IEEE 754 floating-point health values at 60 Hz with zero overhead.

---

### 🔴 Blocker 6: Physics Desync at High Simulation Speeds
* **Symptom**: Accelerating simulation speed via `Time.timeScale = 10.0` caused the pre-round countdown tick index to drift wildly: 1x speed reached fight start at tick 444, 10x reached it at tick 550, and 100x reached it at tick 4001.
* **Root Cause**: Unity decouples physics simulation from real-world wall-clock time. Pre-round animations, UI tweens, and announcer audio streams use `Time.unscaledDeltaTime` (wall-clock time, ~1.5s). Because `FixedUpdate` runs concurrently at a rate of $\frac{\Delta t_{\text{real}} \times \text{timeScale}}{\text{fixedDeltaTime}}$, higher multipliers churn thousands of physics frames while waiting for the audio and UI animations to finish.
* **Resolution**:
  1. Decoupled episode tracking from raw tick counts.
  2. Implemented event-driven lifecycle synchronization: hooked `ViewerFight.Play` (`0x35BE050`) to capture the exact `ROUND_START` event.
  3. Suspended physics stepping during pre-round cutscenes and only activated the RL stepping loop once `ROUND_START` fired, ensuring 100% deterministic, speed-invariant state transitions across all simulation speeds.

---

### 🔴 Blocker 7: Frida Trampoline Collision on 4-Byte Compiler Stubs
* **Symptom**: When trying to hook the main-thread update loop (`Scene<object>.Update` at `0x296FD44`) to queue safe UI actions, Frida crashed immediately with: `Error: unable to intercept function at 0x...; please file a bug`, preventing `rpc.exports` from registering.
* **Root Cause**: On ARM64, Frida's `Interceptor` replaces the entry of a target function with an inline 16-byte trampoline (`ldr x16, ...; br x16`). However, `Scene<object>.Update` was an empty compiler stub consisting of only 4 bytes (`ret` at `0x296FD44`, immediately followed by the next function at `0x296FD48`). Frida's 16-byte hook overwritten the prologue of the adjacent method, corrupting memory and crashing the runtime.
* **Resolution**:
  * Moved away from generic base class stubs. Targeted concrete, fully-implemented scene update loops: `FightScene.FixedUpdate` (`0x3237268`) for combat actions and `MapScene.Update` (`0x3589494`) for navigation actions. Both routines have hundreds of bytes of instructions, providing ample room for relocatable inline trampolines.

---

### 🔴 Blocker 8: ELF Virtual Address Load Bias Delta (`+0x4000`)
* **Symptom**: Attempting to dynamically patch combat victory thresholds in memory (`Memory.patchCode`) using raw file offsets from `libil2cpp.so` (`0x33E7D80`) caused silent failures; matches still ended after 2 rounds despite patching to 5.
* **Root Cause**: Static disassemblers (like Ghidra) often display raw file offsets (`p_offset`). However, when Android's 64-bit dynamic linker (`linker64`) maps ELF shared libraries into memory, it aligns segments according to virtual memory pages (`p_vaddr`). There was a `+0x4000` load bias delta (`p_vaddr - p_offset = 0x18B699C - 0x18B299C = 0x4000`).
* **Resolution**:
  * Corrected all dynamic memory patch addresses by adding the `+0x4000` load bias delta:
    * `DHKCOFBMIEL` victory target: `0x33E7D80` $\to$ `0x33EBD80`
    * `AIFOMGABBBA` stats threshold: `0x33E9444` $\to$ `0x33ED444`
    * `LPIEJMLPFBF` RoundModel target: `0x33EA8E4` $\to$ `0x33EE8E4`
  * Applied via `Memory.patchCode()`, successfully enabling arbitrary match lengths up to 65,535 rounds.

---

## 5. In-Depth Technical Q&A ("The Grill-Me Questions")

### Q1: Why use an embedded Frida Gadget instead of a standard `frida-server` on a rooted emulator?
**Answer**:
> *"Running `frida-server` on a rooted emulator is fine for interactive exploratory reverse engineering, but it is fundamentally unsuitable for scalable, production-grade RL.
>
> 1. **Zero External Daemon Dependencies**: `frida-server` requires root access, background daemon management, adb port-forwarding scripts, and often dies silently if the emulator reboots.
> 2. **Cloud & Container Portability**: By embedding `libfrida-gadget.so` directly inside the APK's native library directory and initializing it via Dalvik bytecode on startup, the application becomes completely self-contained. It can run on rootless Android runtimes, ReDroid headless Docker containers in Linux clusters, or physical test devices without special OS permissions."*

---

### Q2: How did you handle facing direction and directional attack inversion?
**Answer**:
> *"In Shadow Fight 2, player orientations flip dynamically when fighters jump over each other. If the player is on the left, pressing 'Right' moves forward; if on the right, pressing 'Right' moves backward.
>
> Initially, relying on the engine's internal `isFacingLeft` flags caused race conditions during aerial crossovers. I resolved this by pulling native 3D `Vector3` coordinates directly from the root transform (`playerPtr + 0x250` and `opponentPtr + 0x250`) on every single 60 Hz physics tick.
>
> In the Python environment, I exposed **absolute screen-space actions** (Left, Right, Up, Down). The low-level controller dynamically evaluates $X_{\text{player}} > X_{\text{enemy}}$ inside the tick hook to translate absolute commands into the correct contextual strikes (Forward Slash vs Back Slash) with zero frame lag."*

---

### Q3: Why is an in-memory soft-reset superior to reloading the match via UI?
**Answer**:
> *"In fighting games, RL agents take millions of steps to converge. When a round ends in vanilla Shadow Fight 2:
> 1. A 3-second slow-motion knockout animation plays.
> 2. The match outcome banner renders (2 seconds).
> 3. Experience and coin tally screens appear (4 seconds).
> 4. You must click 'Continue' to return to the map and reload the combat scene (6 seconds).
> Total round-trip time: **~15 seconds**.
>
> With a standard 1,000,000-step training run where episodes last ~200 steps (5,000 resets), UI reloads would waste **75,000 seconds (over 20 hours)** purely waiting on menus!
>
> By intercepting the defeat callback (`BBCAMJDDOII`) and rewriting character health pointers directly in memory, the environment resets in **under 10 milliseconds**. The scene never unloads, textures stay resident in VRAM, and the agent begins the next episode instantly. This yields a **1,500× speedup in training iteration velocity**."*

---

### Q4: How do you guarantee deterministic physics stepping over an asynchronous JSON-RPC protocol like Frida?
**Answer**:
> *"If Frida simply sent an asynchronous message saying 'step', the engine would continue ticking in the background while the message traversed the TCP socket.
>
> To make it strictly deterministic, I used Frida's `Interceptor.replace` on `battleCtrl.FixedUpdate`. Inside native memory, I allocated an in-process integer counter (`ticksBudget`). When `ticksBudget == 0`, the replacement hook skips the original `FixedUpdate` call entirely, effectively freezing physics, hitboxes, and timers in place.
>
> When the Python environment calls `env.step(N)`, an atomic RPC call writes $N$ to `ticksBudget`. The engine executes exactly $N$ frames of physics, decrements the counter to zero, freezes again, and returns the resulting state synchronously. The Python client has all the time in the world to perform neural network inference without worrying about time dilation or missed frames."*

---

## 6. Quantitative Impact & Benchmark Metrics

| Metric | Vanilla Game / Baseline | Our Modded Gymnasium Harness | Impact Factor |
| :--- | :--- | :--- | :--- |
| **State Observation Latency** | 60–120ms (ADB screencap + OCR) | **< 1.0ms** (Direct memory reading) | **60×–120× Faster** |
| **Action Dispatch Latency** | ~427ms (OS shell `sendevent`) | **Synchronous / 0ms** (Native IL2CPP hook) | **Zero Lag** |
| **Episode Reset Latency** | ~15,000ms (UI reload & animations) | **< 10ms** (In-memory pointer rewrite) | **1,500× Speedup** |
| **Simulation Speed Range** | Strictly 1.0× real-time (60 FPS) | **1.0× to 10.0× accelerated** | **10× Sample Rate** |
| **Observation Fidelity** | Noisy 8-bit pixels | True 32-bit floats ($x, y, z$, HP, hit badges) | **Lossless State** |
| **Offline First Launch** | Blocked by 156 MB CDN download | **< 250ms** (Automated smali extractor) | **100% Offline** |

---

## 7. Engineering Reflections & Future Roadmap

### Key Architectural Takeaways
1. **Dynamic Binary Instrumentation (DBI) is a Superpower for AI**: Treating closed-source commercial applications as black boxes severely limits RL research. Using DBI (Frida) and reverse engineering unlocks superhuman observation and control fidelity without needing access to original source code.
2. **Eliminate OS Middleware in Simulation Loops**: Operating system abstractions (touch dispatchers, window compositors, video decoders) are designed for human perception, not machine intelligence. Bypassing them and hooking directly into engine logic produces massive performance gains.

### Research Roadmap
- [x] **Milestone 1**: Reverse engineer IL2CPP binaries, bypass CDN locks, and construct monolithic standalone APK.
- [x] **Milestone 2**: In-engine hooking via embedded Frida Gadget; implement discrete physics tick stepping.
- [x] **Milestone 3**: High-level Gymnasium environment (`ShadowFightEnv`), sub-millisecond telemetry extraction, and instant soft-reset.
- [ ] **Milestone 4 (In Progress)**: Scaled headless training via **ReDroid (Android 11 inside Docker)** on a multi-GPU Linux cluster.
- [ ] **Milestone 5**: Train baseline PPO/SAC policies with curriculum learning progressing from sparring dummies to Act 6 Bosses (Lynx, Hermit, Butcher, Wasp, Widow, Shogun).

---

> *"Transforming closed interactive systems into open, deterministic environments for machine intelligence."*  
> **Repository**: [GitHub - slyofzero/shadow-fight-2](https://github.com/slyofzero)  
> **Contact**: [Ishan Shishodiya on LinkedIn](https://www.linkedin.com/in/ishan-shishodiya-5100061b9/) | [sly.of.zero@gmail.com](mailto:sly.of.zero@gmail.com)
