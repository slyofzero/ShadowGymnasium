'use strict';

/**
 * Shadow Fight 2 — Native In-Engine Game Actions Hook.
 * Executes pause, resume, and exit_fight synchronously inside Unity's main-thread Update loop.
 * Zero ADB / screen taps required. Fully headless compatible.
 */



if (!il2cppBase) {
    send({ error: "Could not locate libil2cpp.so base in memory" });
} else {
    send({ status: "READY", base: il2cppBase.toString() });

    // FCJBEKHDLAF.OANGGKCBAOJ(battleCtrl, int actionId) - RVA 0x33F3AD8
    // actionId: 0 = ButtonPause, 1 = ButtonPauseSurrender, 2 = ButtonPausePlay
    var onButtonAction = new NativeFunction(il2cppBase.add(0x33F3AD8), 'void', ['pointer', 'int']);

    // FCJBEKHDLAF.LPIEJMLPFBF(battleCtrl, int gameOverType) - RVA 0x33EE838
    // gameOverType: -1 = GAME_OVER_SURRENDER
    var surrenderAction = new NativeFunction(il2cppBase.add(0x33EE838), 'void', ['pointer', 'int']);

    // InfoBattle.OnFightButtonClick(infoBattle) - RVA 0x3417184
    var onFightButtonClick = new NativeFunction(il2cppBase.add(0x3417184), 'void', ['pointer']);

    // FightScene.RestartFight(fightScene) - RVA 0x3237250
    var restartFight = new NativeFunction(il2cppBase.add(0x3237250), 'void', ['pointer']);

    var currentScene = null;
    var currentSceneName = "Unknown";
    var battleCtrl = null;
    var preFight = null;
    var isPaused = false;
    var inFight = false;
    var lastUpdateTick = 0;
    var pendingAction = null;
    var actionResult = null;

    // Shared BSS address for persisting active battle pointers across script connections
    var sharedBss = il2cppBase.add(0x445f020);
    // [0x0]: battleCtrl, [0x8]: currentScene, [0x10]: preFight
    try {
        var savedCtrl = sharedBss.readPointer();
        if (!savedCtrl.isNull()) {
            battleCtrl = savedCtrl;
            currentScene = sharedBss.add(8).readPointer();
            preFight = sharedBss.add(16).readPointer();
            inFight = true;
            currentSceneName = "FightScene";
            if (!preFight.isNull()) {
                var pauseScreenPtr = preFight.add(0x90).readPointer();
                isPaused = (!pauseScreenPtr.isNull());
            }
        }
    } catch(e) {}

    function processFightFrame(scenePtr) {
        currentScene = scenePtr;
        currentSceneName = "FightScene";
        inFight = true;
        lastUpdateTick++;

        try {
            var bCtrl = currentScene.add(0x90).readPointer();
            var pFight = currentScene.add(0x98).readPointer();

            if (!bCtrl.isNull() && !pFight.isNull()) {
                battleCtrl = bCtrl;
                preFight = pFight;

                // Save to persistent BSS
                sharedBss.writePointer(bCtrl);
                sharedBss.add(8).writePointer(currentScene);
                sharedBss.add(16).writePointer(pFight);

                var pauseScreenPtr = preFight.add(0x90).readPointer();
                isPaused = (!pauseScreenPtr.isNull());

                if (pendingAction !== null) {
                    executeQueuedAction();
                }
            }
        } catch(e) {
            battleCtrl = null;
            preFight = null;
        }
    }

    function processMapFrame(scenePtr) {
        currentScene = scenePtr;
        currentSceneName = "MapScene";
        inFight = false;
        isPaused = false;
        battleCtrl = null;
        preFight = null;
        lastUpdateTick++;

        // Clear BSS when on Map
        try {
            sharedBss.writePointer(ptr(0));
        } catch(e) {}

        if (pendingAction === 'start_fight') {
            pendingAction = null;
            try {
                var infoBattle = currentScene.add(0xD0).readPointer();
                if (!infoBattle.isNull()) {
                    onFightButtonClick(infoBattle);
                    actionResult = { action: 'start_fight', success: true };
                    send({ event: 'action_completed', action: 'start_fight', success: true });
                } else {
                    send({ event: 'action_completed', action: 'start_fight', success: false, error: "InfoBattle pointer is null" });
                }
            } catch(e) {
                send({ event: 'action_completed', action: 'start_fight', success: false, error: e.toString() });
            }
        }
    }

    function executeQueuedAction() {
        if (!pendingAction) return;
        var act = pendingAction;
        pendingAction = null;

        if (act === 'pause') {
            if (battleCtrl && !battleCtrl.isNull()) {
                onButtonAction(battleCtrl, 0);
                actionResult = { action: 'pause', success: true };
                send({ event: 'action_completed', action: 'pause', success: true });
            }
        } else if (act === 'resume') {
            if (battleCtrl && !battleCtrl.isNull()) {
                onButtonAction(battleCtrl, 2);
                isPaused = false;
                actionResult = { action: 'resume', success: true };
                send({ event: 'action_completed', action: 'resume', success: true });
            }
        } else if (act === 'exit') {
            if (battleCtrl && !battleCtrl.isNull()) {
                surrenderAction(battleCtrl, -1);
                inFight = false;
                isPaused = false;
                actionResult = { action: 'exit', success: true };
                send({ event: 'action_completed', action: 'exit', success: true });
            }
        } else if (act === 'start_fight') {
            if (currentScene && !currentScene.isNull()) {
                restartFight(currentScene);
                actionResult = { action: 'start_fight', success: true, restarted: true };
                send({ event: 'action_completed', action: 'start_fight', success: true, restarted: true });
            }
        }
    }

    // 1. Hook FightScene.FixedUpdate (RVA 0x3237268)
    var fightFixedUpdateAddr = il2cppBase.add(0x3237268);
    Interceptor.attach(fightFixedUpdateAddr, {
        onEnter: function(args) {
            processFightFrame(args[0]);
        }
    });

    // 2. Hook MapScene.Update (RVA 0x3589494)
    var mapUpdateAddr = il2cppBase.add(0x3589494);
    Interceptor.attach(mapUpdateAddr, {
        onEnter: function(args) {
            processMapFrame(args[0]);
        }
    });

    // 3. Hook GlobalTimer.Update (RVA 0x1D03DD0) - Runs at 60Hz even while combat is paused
    var globalTimerUpdateAddr = il2cppBase.add(0x1D03DD0);
    Interceptor.attach(globalTimerUpdateAddr, {
        onEnter: function(args) {
            if (pendingAction !== null && battleCtrl && !battleCtrl.isNull()) {
                executeQueuedAction();
            }
        }
    });

    // ── Round Count Patcher ──────────────────────────────────────────────────
    // The ELF file offsets in APK (0x33E7D80, 0x33E9444, 0x33EA8E4) are mapped
    // into virtual runtime memory with a +0x4000 ELF load bias (p_vaddr - p_offset):
    //   0x33EBD80 - FCJBEKHDLAF.DHKCOFBMIEL  -> mov w22, #N  (match victory target)
    //   0x33ED444 - FCJBEKHDLAF.AIFOMGABBBA  -> mov w8,  #N  (round end threshold)
    //   0x33EE8E4 - FCJBEKHDLAF.LPIEJMLPFBF  -> mov w1,  #N  (RoundModel target)
    var roundPatches = [
        { rva: 0x33EBD80, reg: 22 },  // mov w22, #N
        { rva: 0x33ED444, reg: 8  },  // mov w8,  #N
        { rva: 0x33EE8E4, reg: 1  },  // mov w1,  #N
    ];
    var currentRounds = null;

    // Safety: restore original instructions if previously patched at raw file offsets
    try {
        Memory.patchCode(il2cppBase.add(0x33e7d80), 4, function(code) { code.writeU32(0xb0006b00); });
        Memory.patchCode(il2cppBase.add(0x33e9444), 4, function(code) { code.writeU32(0x52800021); });
        Memory.patchCode(il2cppBase.add(0x33ea8e4), 4, function(code) { code.writeU32(0x979b03f9); });
    } catch(e) {}

    function arm64Movz(reg, imm) {
        // MOVZ Wd, #imm  =>  0x52800000 | (imm << 5) | reg
        // ARM64 MOVZ immediate is 16-bit (0..65535). Clamp to 65535 to avoid bit-overflow.
        var val = Math.max(1, Math.min(65535, imm));
        return (0x52800000 | ((val & 0xFFFF) << 5) | (reg & 0x1F)) >>> 0;
    }

    function patchRounds(n) {
        for (var i = 0; i < roundPatches.length; i++) {
            (function(addr, instr) {
                Memory.patchCode(addr, 4, function(code) {
                    code.writeU32(instr);
                });
            })(il2cppBase.add(roundPatches[i].rva), arm64Movz(roundPatches[i].reg, n));
        }
        currentRounds = n;
    }

    rpc.exports = {
        setRounds: function(n) {
            try {
                patchRounds(n);
                return { success: true, rounds: currentRounds };
            } catch(e) {
                return { success: false, error: e.toString() };
            }
        },
        getRounds: function() {
            return { rounds: currentRounds };
        },
        pause: function() {
            if (!inFight || !battleCtrl || battleCtrl.isNull()) {
                return { success: false, error: "Not in active fight (current scene: " + currentSceneName + ")" };
            }
            actionResult = null;
            pendingAction = 'pause';
            return { success: true, queued: true };
        },
        resume: function() {
            if (!inFight || !battleCtrl || battleCtrl.isNull()) {
                return { success: false, error: "Not in active fight (current scene: " + currentSceneName + ")" };
            }
            actionResult = null;
            pendingAction = 'resume';
            return { success: true, queued: true };
        },
        exitFight: function() {
            if (!inFight || !battleCtrl || battleCtrl.isNull()) {
                return { success: false, error: "Not in active fight (current scene: " + currentSceneName + ")" };
            }
            actionResult = null;
            pendingAction = 'exit';
            return { success: true, queued: true };
        },
        startFight: function() {
            if (currentSceneName !== "MapScene" && currentSceneName !== "FightScene") {
                return { success: false, error: "Cannot start fight from scene: " + currentSceneName };
            }
            actionResult = null;
            pendingAction = 'start_fight';
            return { success: true, queued: true };
        },
        getStatus: function() {
            return {
                scene: currentSceneName,
                in_fight: inFight,
                is_paused: isPaused,
                ticks: lastUpdateTick
            };
        }
    };
}
