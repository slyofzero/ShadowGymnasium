'use strict';

/**
 * Shadow Fight 2 — Master Tick Controller Hook.
 * Consolidates all physics timing, stepping, freezing, and simulation speed controls:
 * 1. Tick freeze / unfreeze via Interceptor.replace on battleCtrl.FixedUpdate (RVA 0x33F2A54).
 * 2. Deterministic step-by-step frame execution (step N).
 * 3. Simulation speedup (1x - 100x) via UnityEngine.Time.set_timeScale (RVA 0x3BFB9C0).
 * 4. Auto-freeze on fight/round start via ViewerFight.Play (RVA 0x35BE050).
 * 5. Synchronizes tick state via shared in-memory slot (RVA 0x445f000) for telemetry.
 */



if (!il2cppBase) {
    send({ error: "Could not locate libil2cpp.so base in memory" });
} else {
    send({ status: "READY", base: il2cppBase.toString() });

    // 1. Native Action Functions (optional step queued action)
    var actDownAddr = il2cppBase.add(0x34E90D0);
    var actDown = new NativeFunction(actDownAddr, 'void', ['pointer', 'int']);

    // 2. Simulation Speed (UnityEngine.Time.set_timeScale - RVA 0x3BFB9C0)
    var setTimeScaleAddr = il2cppBase.add(0x3BFB9C0);
    var setTimeScale = new NativeFunction(setTimeScaleAddr, 'void', ['float']);

    // 3. Master Combat Loop (battleCtrl.FixedUpdate - RVA 0x33F2A54)
    var fixedUpdateAddr = il2cppBase.add(0x33F2A54);
    var origFixedUpdate = new NativeFunction(fixedUpdateAddr, 'void', ['pointer']);

    // State
    var isFrozen = false;
    var ticksBudget = 0;
    var totalTicksExecuted = 0;
    var currentSpeed = 1.0;
    var autoFreezeOnRoundStart = false;

    var battleCtrlPtr = null;
    var pendingAction = null;

    // Shared In-Memory Process Synchronization Slot for telemetry_streamer
    var sharedTickStateAddr = il2cppBase.add(0x445f000);
    // [0x0]: is_frozen (int32), [0x4]: total_ticks (int32), [0x8]: speed (float)
    sharedTickStateAddr.writeS32(0);
    sharedTickStateAddr.add(4).writeS32(0);
    sharedTickStateAddr.add(8).writeFloat(1.0);

    // Intercept Master Combat Loop
    var customFixedUpdate = new NativeCallback(function(thisPtr) {
        battleCtrlPtr = thisPtr;

        // If not frozen, execute normally at 60 Hz
        if (!isFrozen) {
            totalTicksExecuted++;
            sharedTickStateAddr.writeS32(0);
            sharedTickStateAddr.add(4).writeS32(totalTicksExecuted);
            origFixedUpdate(thisPtr);
            return;
        }

        // When frozen: only execute if ticks are in budget
        if (ticksBudget > 0) {
            // Apply queued action if any
            if (pendingAction !== null) {
                try {
                    var playerPtr = battleCtrlPtr.add(0xB0).readPointer();
                    if (!playerPtr.isNull()) {
                        var pInput = playerPtr.add(0x150).readPointer();
                        if (!pInput.isNull()) {
                            if (pendingAction.quad > 0) actDown(pInput, pendingAction.quad);
                            if (pendingAction.button > 0) actDown(pInput, pendingAction.button);
                        }
                    }
                } catch(e) {}
                pendingAction = null;
            }

            ticksBudget--;
            totalTicksExecuted++;
            sharedTickStateAddr.writeS32(1);
            sharedTickStateAddr.add(4).writeS32(totalTicksExecuted);
            origFixedUpdate(thisPtr);

            if (ticksBudget === 0) {
                send({ event: "step_done", total_ticks: totalTicksExecuted });
            }
        } else {
            // Frozen: ensure flag is 1
            sharedTickStateAddr.writeS32(1);
        }
        // If ticksBudget == 0: skip call, game stays frozen!
    }, 'void', ['pointer']);

    try {
        Interceptor.revert(fixedUpdateAddr);
    } catch(e) {}
    try {
        Interceptor.replace(fixedUpdateAddr, customFixedUpdate);
    } catch(e) {}

    // Auto-freeze on Round Start (ViewerFight.Play - RVA 0x35BE050)
    var roundStartAddr = il2cppBase.add(0x35BE050);
    Interceptor.attach(roundStartAddr, {
        onEnter: function(args) {
            if (autoFreezeOnRoundStart) {
                isFrozen = true;
                ticksBudget = 0;
                sharedTickStateAddr.writeS32(1);
                send({ event: "auto_frozen_on_round_start", total_ticks: totalTicksExecuted });
            }
        }
    });

    rpc.exports = {
        freeze: function() {
            isFrozen = true;
            ticksBudget = 0;
            sharedTickStateAddr.writeS32(1);
            return { success: true, frozen: isFrozen };
        },
        unfreeze: function() {
            isFrozen = false;
            ticksBudget = 0;
            sharedTickStateAddr.writeS32(0);
            return { success: true, frozen: isFrozen };
        },
        step: function(numTicks, quad, button) {
            if (!isFrozen) {
                isFrozen = true;
                sharedTickStateAddr.writeS32(1);
            }
            if (quad > 0 || button > 0) {
                pendingAction = { quad: quad, button: button };
            } else {
                pendingAction = null;
            }
            ticksBudget += numTicks;
            return { success: true, budget: ticksBudget };
        },
        setSpeed: function(scale) {
            currentSpeed = scale;
            setTimeScale(scale);
            sharedTickStateAddr.add(8).writeFloat(scale);
            return { success: true, speed: currentSpeed };
        },
        setAutoFreeze: function(enabled) {
            autoFreezeOnRoundStart = enabled;
            return { success: true, auto_freeze: autoFreezeOnRoundStart };
        },
        getStatus: function() {
            return {
                frozen: isFrozen,
                total_ticks: totalTicksExecuted,
                budget: ticksBudget,
                speed: currentSpeed
            };
        }
    };
}
