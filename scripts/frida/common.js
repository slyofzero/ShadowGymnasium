'use strict';

/**
 * Shadow Fight 2 — Shared Frida In-Engine Helper
 * Unified Il2Cpp resolution supporting both native ARM64 (BlueStacks)
 * and translated x86_64 container environments (ReDroid / ndk_translation).
 */

function findIl2CppBase() {
    var mod = Process.findModuleByName('libil2cpp.so');
    if (mod) return mod.base;

    // 1. Native ARM64 runtime (BlueStacks): code section mapped as r-x with +0x18b6000 load bias
    var rxRanges = Process.enumerateRanges('r-x');
    for (var i = 0; i < rxRanges.length; i++) {
        if (rxRanges[i].file && rxRanges[i].file.path.indexOf('libil2cpp.so') !== -1) {
            return rxRanges[i].base.sub(0x18b6000);
        }
    }

    // 2. Translated ARM64 runtime (ReDroid / libndk_translation): mapped as read-only r--
    var rRanges = Process.enumerateRanges('r--');
    for (var j = 0; j < rRanges.length; j++) {
        if (rRanges[j].file && rRanges[j].file.path.indexOf('libil2cpp.so') !== -1 && rRanges[j].size > 20000000) {
            return rRanges[j].base;
        }
    }

    return null;
}

var il2cppBase = findIl2CppBase();
