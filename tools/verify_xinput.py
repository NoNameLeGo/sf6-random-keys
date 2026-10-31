# -*- coding: utf-8 -*-
"""用 XInput API 验证：虚拟手柄在 Windows 的"游戏视角"里是否真的存在、按键是否真的上报。

游戏（含街霸6）读手柄就是走 XInputGetState。这里直接调同一个 API，
如果 XInput 能读到我们的虚拟手柄、且按下 A 后 wButtons 位变化，就说明
"游戏能看见这个手柄、并且能收到我们发的按键" —— 不需要开游戏即可验证。
"""
import ctypes
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

XINPUT_GAMEPAD_A = 0x1000
XINPUT_GAMEPAD_DPAD_UP = 0x0001
ERROR_SUCCESS = 0


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", ctypes.c_ushort),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", ctypes.c_short),
        ("sThumbLY", ctypes.c_short),
        ("sThumbRX", ctypes.c_short),
        ("sThumbRY", ctypes.c_short),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_ulong), ("Gamepad", XINPUT_GAMEPAD)]


def load_xinput():
    for dll in ("XInput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            return ctypes.WinDLL(dll)
        except OSError:
            continue
    return None


xi = load_xinput()
if xi is None:
    print("找不到任何 XInput DLL")
    sys.exit(2)
print("已加载:", xi._name)

xi.XInputGetState.argtypes = [ctypes.c_uint, ctypes.POINTER(XINPUT_STATE)]
xi.XInputGetState.restype = ctypes.c_uint


def scan(tag):
    rows = []
    for i in range(4):
        st = XINPUT_STATE()
        rc = xi.XInputGetState(i, ctypes.byref(st))
        if rc == ERROR_SUCCESS:
            rows.append((i, st.Gamepad.wButtons, st.dwPacketNumber))
    print("[%s] 已连接的手柄槽位: %s" % (
        tag, ", ".join("slot%d(buttons=0x%04X)" % (i, b) for i, b, _ in rows) or "无"))
    return rows


before = scan("创建虚拟手柄前")

import vgamepad as vg
pad = vg.VX360Gamepad()
time.sleep(1.2)
after = scan("创建后")

if not after:
    print("❌ XInput 看不到虚拟手柄 —— 游戏也不会看到")
    sys.exit(1)

slot = after[0][0]
print("✅ 虚拟手柄占用 XInput 槽位 %d（游戏里的 P%d）" % (slot, slot + 1))

# 按下 A 并回读，确认按键真的上报
pad.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
pad.update()
time.sleep(0.3)
st = XINPUT_STATE()
xi.XInputGetState(slot, ctypes.byref(st))
pressed = bool(st.Gamepad.wButtons & XINPUT_GAMEPAD_A)
print("按下 A 后：wButtons=0x%04X  A 位=%s" % (st.Gamepad.wButtons, "按下✅" if pressed else "没上报❌"))

pad.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_A)
pad.update()
time.sleep(0.3)
xi.XInputGetState(slot, ctypes.byref(st))
released = not bool(st.Gamepad.wButtons & XINPUT_GAMEPAD_A)
print("松开 A 后：wButtons=0x%04X  已松开=%s" % (st.Gamepad.wButtons, "✅" if released else "❌"))

# 再验一个方向键（街霸6 的移动就是靠它）
pad.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
pad.update()
time.sleep(0.3)
xi.XInputGetState(slot, ctypes.byref(st))
up = bool(st.Gamepad.wButtons & XINPUT_GAMEPAD_DPAD_UP)
print("按下「上」后：wButtons=0x%04X  上方向=%s" % (st.Gamepad.wButtons, "✅" if up else "❌"))
pad.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
pad.update()

ok = pressed and released and up
print("RESULT:", "PASS —— 虚拟手柄对 XInput（也就是游戏）完全可见、按键可上报" if ok else "FAIL")
sys.exit(0 if ok else 1)
