# -*- coding: utf-8 -*-
"""街霸6 随机按键玩具 —— 虚拟手柄版 v1.1（需要 ViGEmBus + vgamepad）

原理：借助 ViGEmBus 创建一台"虚拟 Xbox 手柄"（内核级虚拟设备，系统与
游戏都把它当作真实手柄），脚本直接随机按手柄按钮来玩街霸6。
——街霸6 会过滤"软件注入的键盘按键"，但虚拟手柄属于设备级输入，
不受该过滤影响（格斗游戏社区验证过的路线）。

两种用法：
  1) 双击「手柄乱按-按F9开始.cmd」——双击后先待命（不自动开始），
     进游戏按 F9 开始，F10 停止；迷你窗口只看状态。
  2) 双击「启动-手柄版.cmd」——带参数面板的完整版（同样支持热键）。
参数（面板里改、改完立即生效，并自动记住到 params.json）：
  每组持续帧数 / 空拍概率
热键（游戏里直接按，不用切出来）：
  F9 = 开始乱按（F7 同功能，F9 被占时用）
  F10 = 停止
  F8  = 嘲讽（六个拳脚键同时按一下，不进随机池）
  —— 三条监听通道并行（键盘钩子 / 系统热键 / 轮询），任何一条活着就能用。

命令行参数：
  --auto      启动即开始乱按（配合「一键乱按.cmd」）
  --topmost   面板置顶（游戏用"窗口/无边框"模式时能看到状态）
  --selftest  只做自检，不建窗口

前置条件：
  1) 安装 ViGEmBus 驱动：双击 tools\\ViGEmBus_1.22.0_x64_x86_arm64.exe
     （微软签名驱动，装完建议重启一次）
  2) pip install vgamepad

仅限单机 / 训练模式娱乐使用，请勿在在线模式使用！
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_TITLE = "街霸6 随机按键玩具（手柄版）v1.1"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(APP_DIR, "log.txt")
PARAM_PATH = os.path.join(APP_DIR, "params.json")   # 面板参数存档（调一次就记住）

TARGET_EXE_SUFFIX = "streetfighter6.exe"      # 前台进程名匹配（小写）
TARGET_TITLE_SUBSTR = "street fighter"        # 窗口标题包含（小写，兜底）
SELFTEST = "--selftest" in sys.argv
AUTO_START = "--auto" in sys.argv             # 启动即开始乱按（默认关闭：双击后先待命，按 F9 才开始）
TOPMOST = "--topmost" in sys.argv             # 面板置顶（窗口/无边框模式能看状态）
MINI = "--mini" in sys.argv                   # 迷你窗口：不显示参数框，只看状态 + 热键提示
DEBUG = SELFTEST or bool(os.environ.get("SF6RK_DEBUG"))

FONT_UI = ("Microsoft YaHei UI", 10)
FONT_BIG = ("Microsoft YaHei UI", 12, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)

# vgamepad 延迟导入：缺失/驱动异常时给出友好提示而不是崩溃。
# ⚠️ 关键：vgamepad 在 **import 阶段**就会去连 ViGEmBus 总线，所以 import 失败可能是
#    "没装库"（ModuleNotFoundError），也可能是"库装了但驱动没起来"（VIGEM_ERROR_BUS_NOT_FOUND），
#    两者要给完全不同的提示 —— 这里先把原因记下来，下面再分流。
VG_IMPORT_ERR = ""
try:
    import vgamepad as vg
except Exception as _vg_err:
    vg = None
    VG_IMPORT_ERR = str(_vg_err)


def is_driver_bus_error(err):
    """True = 库在，但 ViGEmBus 的总线设备不可用。"""
    return "BUS_NOT_FOUND" in str(err).upper()


def driver_error_detail(err):
    """把驱动错误翻译成用户能照着做的说明。"""
    if is_driver_bus_error(err):
        return ("ViGEmBus 驱动装了，但「虚拟手柄总线设备」没有起来。\n\n"
                "常见原因：驱动被卸载到一半（服务处于「待删除/已禁用」状态）、"
                "或者装完还没重启。\n\n"
                "修法（需要管理员）：\n"
                "  1) 先重启一次电脑（把「待删除」的服务清掉）\n"
                "  2) 双击 tools\\ViGEmBus_1.22.0_x64_x86_arm64.exe 重新安装\n"
                "  3) 再重启一次，然后重新打开本程序\n\n"
                "装完可自检：py sf6_gamepad.pyw --selftest（应显示 ViGEmBus driver: OK）\n")
    return ("创建虚拟手柄失败，通常是因为未安装 ViGEmBus 驱动。\n\n"
            "请先双击安装：\n    tools\\ViGEmBus_1.22.0_x64_x86_arm64.exe\n"
            "安装完成后（必要时重启电脑）再运行本程序。\n")

# 随机按钮池（名称仅用于界面显示；属性名对应 vgamepad 的 XUSB_BUTTON）
DIR_LIST = [
    ("上", "XUSB_GAMEPAD_DPAD_UP"),
    ("下", "XUSB_GAMEPAD_DPAD_DOWN"),
    ("左", "XUSB_GAMEPAD_DPAD_LEFT"),
    ("右", "XUSB_GAMEPAD_DPAD_RIGHT"),
]

# 六个拳脚键（按用户要求**不含** LB / LT —— 那两个他记得是"蓝防/迸放"类特殊动作）
ATK_LIST = [
    ("A", "XUSB_GAMEPAD_A"),
    ("B", "XUSB_GAMEPAD_B"),
    ("X", "XUSB_GAMEPAD_X"),
    ("Y", "XUSB_GAMEPAD_Y"),
    ("RB", "XUSB_GAMEPAD_RIGHT_SHOULDER"),
    ("RT", "TRIG:right_trigger"),      # 扳机不是按钮，是 0~255 的模拟轴，单独分支处理
]

BUTTON_LIST = DIR_LIST + ATK_LIST      # 全部可选键（界面/统计用）

# 每次"按一下"实际上是按一组：方向 1~2 个 + 拳脚 1~6 个，同时按下、同时松开
DIR_MIN, DIR_MAX = 1, 2
ATK_MIN, ATK_MAX = 1, len(ATK_LIST)

# 拳脚键个数：**单键最常见，越多越少见**，权重之和 = 100（所以数字就是百分比）。
# ⚠️ 六个一起按 = 街霸6 的嘲讽，**不在随机池里**；嘲讽改成 F8 手动触发（见 taunt_once）。
ATK_WEIGHTS = ((1, 40), (2, 25), (3, 18), (4, 11), (5, 6))

TAUNT_HOLD_SEC = 0.15      # 嘲讽按住多久（≈9 帧）：街霸6 靠"六键同按"判嘲讽，按一下够

# ------------------------------------------------------------------ Win32 前台窗口检测
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                ctypes.POINTER(wt.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wt.BOOL
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def query_foreground():
    """返回 (前台窗口标题, 前台进程完整路径)；失败给空串。"""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    n = user32.GetWindowTextLengthW(hwnd)
    tbuf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, tbuf, n + 2)
    title = tbuf.value

    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if h:
        pbuf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(len(pbuf))
        if kernel32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
            exe = pbuf.value
        kernel32.CloseHandle(h)
    return title, exe


def is_game_foreground():
    title, exe = query_foreground()
    if exe.lower().endswith(TARGET_EXE_SUFFIX):
        return True
    return TARGET_TITLE_SUBSTR in title.lower()


# ------------------------------------------------------------------ 全局热键
# v1.0：**三条通道并行**，任一条活着热键就能用（以前只有第 ③ 条，在游戏里失灵的
# 概率就全压在它身上）：
#   ① 低级键盘钩子 WH_KEYBOARD_LL —— 系统在把按键发给任何窗口之前先调我们，
#      全屏/游戏独占键盘时也照收；不需要管理员（除非前台是提权进程）
#   ② RegisterHotKey 系统热键 —— 进程级注册，和焦点无关
#   ③ GetAsyncKeyState 轮询 —— 兜底（前两条被安全软件拦掉时还能用）
# 三条会同时收到同一次按下 → 按"动作"做 200ms 去重，保证一次按键只切一次。
VK_F7 = 0x76       # 开始乱按（备用键：F9 被别的软件占住时用）
VK_F8 = 0x77       # 嘲讽（六个拳脚键同时按一下）
VK_F9 = 0x78       # 开始乱按（主用键）
VK_F10 = 0x79      # 强制停止

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
LLKHF_INJECTED = 0x10
HOTKEY_ID_BASE = 0x5F60
# 跨通道去重窗口：钩子几乎立刻收到，轮询最晚 120ms 后才看到同一次按下 -> 180ms 够用。
# 只按"键"去重（F7 和 F9 互不影响），真实人手两次按键远慢于 180ms。
HOTKEY_DEDUPE_SEC = 0.18

_HIT_QUEUE = queue.Queue()      # (vk, 来源) —— 钩子回调里只做入队，绝不干重活
_LAST_HIT = {}                  # vk -> 上次触发时间（跨通道去重）
_HELD = set()                   # 钩子通道：当前按着没松开的键（挡掉键盘自动重复）
_LISTENER_INFO = []             # 启动结果（写进日志）
_HOOK_PROC = None               # 必须留引用，否则回调被 GC 后钩子直接失效
_TIDS = {}                      # 监听线程 id（退出时 PostThreadMessage 用）

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, ctypes.c_void_p,
                                     wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                  wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
user32.RegisterHotKey.restype = wt.BOOL
user32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wt.BOOL
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
kernel32.GetCurrentThreadId.restype = wt.DWORD

_PREV_KEY = {}


def hotkey_hit(vk):
    """该键"刚被按下"时返回 True；同一次按下不会连续触发两次。

    长按不连发：键盘自动重复会反复把低 1 位置上，如果只看低位，按住 F8/F10
    就会每 120ms 触发一次（日志里出现过同一秒 4 条"热键 F10"）。
    但"按下又松开都发生在两次轮询之间"的快速点按，仍要靠低 1 位抓到。
    """
    try:
        state = user32.GetAsyncKeyState(vk)
    except Exception:
        return False
    down = bool(state & 0x8000)
    prev = _PREV_KEY.get(vk, 0)
    _PREV_KEY[vk] = 1 if down else 0
    return (bool(state & 0x0001) or down) and not prev


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


# ---- 动作（任何通道触发都走这里；都只碰共享状态，不碰 tk）
def _act_start(source):
    """F9/F7：开始乱按。**只开始，不切换** —— 战斗中不用记"现在是开还是关"。"""
    with S.lock:
        was = S.running
        S.running = True
    if was:
        _log_line("热键 开始（%s）—— 本来就在跑，忽略" % source)
        return
    _log_line("热键 开始（%s）" % source)
    _set_status("乱按中…", "run")


def _act_stop(source):
    """F10：停止（再按 F9 才继续）。"""
    with S.lock:
        was = S.running
        S.running = False
    if was:
        _log_line("热键 停止（%s）" % source)
    _set_status("已停止 —— 按 F9 开始", "idle")


def _act_taunt(source):
    with S.lock:
        S.taunt_req = True
    _log_line("热键 嘲讽（%s）" % source)


# 键 -> 动作（F7 是 F9 的备用键：F9 被别的软件占住时用）
_ACTIONS = {VK_F9: _act_start,
            VK_F7: _act_start,
            VK_F10: _act_stop,
            VK_F8: _act_taunt}


def handle_vk(vk, source):
    """所有通道的按键都汇总到这里：先按"键"做跨通道去重，再执行动作。"""
    fn = _ACTIONS.get(vk)
    if fn is None:
        return
    now = time.monotonic()
    if now - _LAST_HIT.get(vk, 0.0) < HOTKEY_DEDUPE_SEC:
        return                      # 同一次按下被另一条通道重复上报
    _LAST_HIT[vk] = now
    fn(source)


def poll_hotkeys():
    """兜底通道：轮询 F9/F7/F10/F8（由界面定时器调用）。"""
    for vk in (VK_F9, VK_F7, VK_F10, VK_F8):
        if hotkey_hit(vk):
            _HIT_QUEUE.put((vk, "轮询"))


def _hit_dispatcher():
    """把各通道上报的按键串行化，保证动作不在钩子回调里执行（钩子要立刻返回）。"""
    while not QUIT.is_set():
        try:
            vk, source = _HIT_QUEUE.get(timeout=0.1)
        except queue.Empty:
            continue
        try:
            handle_vk(vk, source)
        except Exception:
            _log_exc()


def _kb_hook_proc(nCode, wParam, lParam):
    try:
        if nCode == 0:
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = int(kb.vkCode)
            if vk in _ACTIONS and not (kb.flags & LLKHF_INJECTED):
                if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if vk not in _HELD:      # 按住不放的自动重复在这里挡掉
                        _HELD.add(vk)
                        _HIT_QUEUE.put((vk, "键盘钩子"))
                elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                    _HELD.discard(vk)
    except Exception:
        pass
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def _kb_hook_thread():
    global _HOOK_PROC
    _TIDS["hook"] = kernel32.GetCurrentThreadId()
    try:
        _HOOK_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                        wt.WPARAM, wt.LPARAM)(_kb_hook_proc)
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL,
                                        ctypes.cast(_HOOK_PROC, ctypes.c_void_p),
                                        None, 0)
    except Exception as e:
        _LISTENER_INFO.append("键盘钩子 FAIL(%s)" % e)
        return
    if not hook:
        _LISTENER_INFO.append("键盘钩子 FAIL(err=%d)" % ctypes.get_last_error())
        return
    _LISTENER_INFO.append("键盘钩子 OK")
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        pass                        # 只为让钩子回调有消息泵，不处理内容
    user32.UnhookWindowsHookEx(hook)


def _hotkey_msg_thread():
    """RegisterHotKey 通道：线程内注册 + 自己泵消息（WM_HOTKEY 投递到本线程队列）。"""
    _TIDS["hotkey"] = kernel32.GetCurrentThreadId()
    ids = {}
    for i, vk in enumerate((VK_F9, VK_F7, VK_F10, VK_F8)):
        hid = HOTKEY_ID_BASE + i
        if user32.RegisterHotKey(None, hid, MOD_NOREPEAT, vk):
            ids[hid] = vk
    _LISTENER_INFO.append("系统热键 %d/4 注册成功" % len(ids))
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            vk = ids.get(int(msg.wParam))
            if vk is not None:
                _HIT_QUEUE.put((vk, "系统热键"))
    for hid in ids:
        user32.UnregisterHotKey(None, hid)


def start_hotkey_listeners():
    """启动三条通道（都在守护线程里，不阻塞界面）。"""
    for target, name in ((_hit_dispatcher, "hk-dispatch"),
                         (_kb_hook_thread, "hk-hook"),
                         (_hotkey_msg_thread, "hk-register")):
        threading.Thread(target=target, daemon=True, name=name).start()


def stop_hotkey_listeners():
    for name, tid in list(_TIDS.items()):
        try:
            user32.PostThreadMessageW(tid, 0x0012, 0, 0)      # WM_QUIT
        except Exception:
            pass


# ------------------------------------------------------------------ 日志
def _log_line(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def _log_exc():
    import traceback
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n[%s] 异常:\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write(traceback.format_exc())
    except Exception:
        pass


# ------------------------------------------------------------------ 共享状态
class _Shared:
    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.status = "已停止"
        self.status_level = "idle"
        self.latest_key = "-"
        # 帧驱动：每一组输入保持 frames_min~frames_max 帧（街霸固定 60fps，1 帧 ≈ 16.7ms），
        # 到点就整组换成下一组（后一份报告自然覆盖前一份，等于"松开旧的、按下新的"）。
        self.frames_min, self.frames_max = 1, 3
        self.neutral_pct = 15                     # "空拍"（什么都不按）的概率 %
        self.taunt_req = False                    # 收到"嘲讽"请求（F8 / 面板按钮）


S = _Shared()
QUIT = threading.Event()
PAD = None          # VX360Gamepad 实例（主线程创建）


def _set_status(text, level):
    with S.lock:
        S.status = text
        S.status_level = level


def _set_latest(name):
    with S.lock:
        S.latest_key = name


# ------------------------------------------------------------------ 按一下 / 按一组（按钮 / 扳机）
def _down(attr):
    if attr.startswith("TRIG:"):
        getattr(PAD, attr[5:])(value=255)
    else:
        PAD.press_button(button=getattr(vg.XUSB_BUTTON, attr))


def _up(attr):
    if attr.startswith("TRIG:"):
        getattr(PAD, attr[5:])(value=0)
    else:
        PAD.release_button(button=getattr(vg.XUSB_BUTTON, attr))


def press_combo(items, hold_ms):
    """同时按下一组键（方向 + 拳脚），保持 hold_ms 后全部松开。

    items: [(显示名, attr), ...]，attr 为 XUSB_BUTTON 名或 "TRIG:left/right_trigger"。
    用 try/finally 保证任何异常下都会松开，避免"卡键"。
    """
    name = "+".join(n for n, _ in items)
    try:
        for _, attr in items:
            _down(attr)
        PAD.update()
        _set_latest(name)
        time.sleep(hold_ms / 1000.0)
    finally:
        for _, attr in items:
            _up(attr)
        PAD.update()


def press_once(name, attr, hold_ms):
    """按一个键（老接口，保留给测试脚本用）。"""
    press_combo([(name, attr)], hold_ms)


FRAME_SEC = 1.0 / 60.0     # 街霸固定 60fps：1 帧 ≈ 16.7ms
_KEYS_DOWN = False         # 当前是否有一组键按下（用于停止时补一份"全松开"报告）


def release_all_once():
    """发一份全零报告（松开所有键）。只在确实有键按着时发一次，防止卡键。"""
    global _KEYS_DOWN
    if PAD is None or not _KEYS_DOWN:
        return
    try:
        PAD.report = PAD.get_default_report()
        PAD.update()
    except Exception:
        _log_exc()
    _KEYS_DOWN = False


def pick_attack_count():
    """这一组要按几个拳脚键：1~5 个，按 ATK_WEIGHTS 加权（单键最多 → 五键最少）。

    ⚠️ **永远不会返回 6**：六个一起按 = 街霸6 的嘲讽，不进随机池，
    由 F8 /「嘲讽」按钮手动触发（见 taunt_once）。
    """
    r = random.randint(1, 100)
    acc = 0
    for n, w in ATK_WEIGHTS:
        acc += w
        if r <= acc:
            return n
    return ATK_WEIGHTS[0][0]


def taunt_once():
    """手动嘲讽：六个拳脚键同时按一下（街霸6 里六键同按 = 嘲讽）。

    只按住很短一下（TAUNT_HOLD_SEC），紧接着由 worker 的下一轮循环发新报告清掉，
    所以不会卡键。
    """
    global _KEYS_DOWN
    PAD.report = PAD.get_default_report()
    for _, attr in ATK_LIST:
        _down(attr)
    PAD.update()
    _set_latest("嘲讽（六键同按）")
    _KEYS_DOWN = True
    time.sleep(TAUNT_HOLD_SEC)


# ------------------------------------------------------------------ 工作线程
def worker():
    global _KEYS_DOWN
    while not QUIT.is_set():
        try:
            with S.lock:
                running = S.running
                frames_min, frames_max = S.frames_min, S.frames_max
                neutral_pct = S.neutral_pct
                taunt_req = S.taunt_req
                S.taunt_req = False

            if not is_game_foreground():
                release_all_once()
                _set_status("等街霸6…（已切出游戏，暂停中）", "pause")
                time.sleep(0.12)
                continue

            if PAD is None:
                _set_status("虚拟手柄未连接", "error")
                time.sleep(0.3)
                continue

            # 「嘲讽」优先：F8 或面板按钮 → 六个拳脚键同时按一下（跟乱按在不在跑无关）
            if taunt_req:
                _set_status("嘲讽！", "run")
                taunt_once()
                continue

            if not running:
                release_all_once()
                _set_status("已停止 —— 按 F9 开始", "idle")
                time.sleep(0.06)
                continue

            _set_status("乱按中…", "run")

            # 一组输入 = 方向 1~2 个 + 拳脚 1~5 个（加权：单键最多 → 五键最少；六个不进随机），
            # 整组同时按下，保持 1~3 帧后换成下一组。
            # XInput 每份报告都是"完整状态"，后一份自然覆盖前一份，所以不用先松开再按下。
            frames = random.randint(frames_min, frames_max)

            if random.randint(1, 100) <= neutral_pct:
                picks, name = [], "（空拍）"
            else:
                picks = (random.sample(DIR_LIST, random.randint(DIR_MIN, DIR_MAX)) +
                         random.sample(ATK_LIST, pick_attack_count()))
                name = "+".join(n for n, _ in picks)

            PAD.report = PAD.get_default_report()     # 干净的一份状态（不发送）
            for _, attr in picks:
                _down(attr)
            PAD.update()                              # 这一帧的输入就是这一组
            _set_latest(name)
            _KEYS_DOWN = bool(picks)

            time.sleep(frames * FRAME_SEC)

        except Exception:
            _log_exc()
            _set_status("出错了（见 log.txt）", "error")
            with S.lock:
                S.running = False
            time.sleep(0.5)


# ------------------------------------------------------------------ 小工具
def _to_int(text, default):
    try:
        return int(str(text).strip())
    except Exception:
        return default


# 面板参数默认值 + 存档：在完整面板里调一次，下次打开（含迷你版）还是这套
DEFAULT_PARAMS = {"frames_min": 1, "frames_max": 3, "neutral_pct": 15}


def load_params():
    """读上次调好的参数；文件不存在/损坏时用默认值（绝不因此崩溃）。"""
    p = dict(DEFAULT_PARAMS)
    try:
        with open(PARAM_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        for k in p:
            if k in saved:
                p[k] = _to_int(saved[k], p[k])
    except Exception:
        pass
    return p


def save_params(p):
    try:
        with open(PARAM_PATH, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def connect_pad():
    """创建虚拟手柄；返回 (成功?, 错误信息)。"""
    global PAD
    try:
        PAD = vg.VX360Gamepad()
        return True, ""
    except Exception as e:
        PAD = None
        return False, str(e)


# ------------------------------------------------------------------ 界面
class App:
    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        root.resizable(False, False)
        if TOPMOST:
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
        root.report_callback_exception = self._on_tk_error

        wrap = ttk.Frame(root, padding=(14, 10))
        wrap.pack(fill="both", expand=True)

        self.status_lbl = tk.Label(wrap, text="● 已停止", font=FONT_BIG, anchor="w")
        self.status_lbl.pack(fill="x")
        self.pad_lbl = tk.Label(wrap, text="虚拟手柄：已连接", font=FONT_SMALL,
                                fg="#2e7d32", anchor="w")
        self.pad_lbl.pack(fill="x")
        self.key_lbl = tk.Label(wrap, text="最近按钮：—", font=FONT_SMALL,
                                fg="#888888", anchor="w")
        self.key_lbl.pack(fill="x", pady=(2, 10))

        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(0, 10))
        self.start_btn = tk.Button(
            btns, text="▶ 开始乱按 (F9)", font=FONT_UI, width=16,
            bg="#2e7d32", fg="#ffffff", activebackground="#1b5e20",
            activeforeground="#ffffff", relief="flat", cursor="hand2",
            command=self.on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = tk.Button(
            btns, text="■ 停止 (F10)", font=FONT_UI, width=11,
            bg="#ececec", relief="flat", cursor="hand2", command=self.on_stop)
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.taunt_btn = tk.Button(
            btns, text="嘲讽 (F8)", font=FONT_UI, width=10,
            bg="#ede7f6", activebackground="#d1c4e9", relief="flat",
            cursor="hand2", command=self.on_taunt)
        self.taunt_btn.pack(side="left", padx=(8, 0))

        param = ttk.LabelFrame(wrap, text=" 参数（改完立即生效） ", padding=(10, 6))
        if not MINI:
            param.pack(fill="x")

        saved = load_params()
        self.frames_min_var = tk.StringVar(value=str(saved["frames_min"]))
        self.frames_max_var = tk.StringVar(value=str(saved["frames_max"]))
        self.neutral_var = tk.StringVar(value=str(saved["neutral_pct"]))

        ttk.Label(param, text="每组持续（帧，60fps）").grid(row=0, column=0, sticky="w")
        ttk.Entry(param, textvariable=self.frames_min_var, width=6).grid(
            row=0, column=1, sticky="w", padx=(4, 0))
        ttk.Label(param, text="~").grid(row=0, column=2)
        ttk.Entry(param, textvariable=self.frames_max_var, width=6).grid(
            row=0, column=3, sticky="w")

        ttk.Label(param, text="空拍概率（%）").grid(row=1, column=0, sticky="w",
                                              pady=(4, 0))
        ttk.Entry(param, textvariable=self.neutral_var, width=6).grid(
            row=1, column=1, sticky="w", padx=(4, 0), pady=(4, 0))
        ttk.Label(param, text="（什么都不按的几率）", font=FONT_SMALL,
                  foreground="#888888").grid(row=1, column=2, columnspan=2,
                                             sticky="w", pady=(4, 0))

        ttk.Label(param, text="拳脚个数：单键最多 → 五键最少（六个不进随机，嘲讽按 F8）",
                  font=FONT_SMALL, foreground="#888888").grid(
                      row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

        if MINI:
            tips_text = ("F9 = 开始乱按（F7 也行）      F10 = 停止      F8 = 嘲讽\n"
                         "不想乱按时按 F10，想继续再按 F9\n"
                         "只对街霸6窗口生效；切出游戏自动暂停\n"
                         "仅限单机 / 训练模式娱乐，请勿在在线模式使用")
        else:
            tips_text = ("通过「虚拟 Xbox 手柄」发送按键，游戏无法过滤\n"
                         "F9 = 开始乱按（F7 也行） / F10 = 停止 / F8 = 嘲讽\n"
                         "热键三条通道并行（键盘钩子 / 系统热键 / 轮询），游戏里直接按即可\n"
                         "只对街霸6窗口生效；切出游戏自动暂停（切回来继续）\n"
                         "参数改完立即生效，并自动记住（下次打开还是这套）\n"
                         "仅限单机 / 训练模式娱乐，请勿在在线模式使用")
        tips = tk.Label(wrap, text=tips_text,
                        font=FONT_SMALL, fg="#888888", justify="left", anchor="w")
        tips.pack(fill="x", pady=(10, 0))

        for var in (self.frames_min_var, self.frames_max_var, self.neutral_var):
            var.trace_add("write", lambda *_: self.apply_params())

        self.apply_params()
        self.root.after(120, self.poll)

    def apply_params(self):
        lo = _to_int(self.frames_min_var.get(), 1)
        hi = _to_int(self.frames_max_var.get(), 3)
        lo, hi = min(lo, hi), max(lo, hi)
        lo, hi = max(1, lo), min(30, hi)          # 每组 1~30 帧

        npct = _to_int(self.neutral_var.get(), 15)
        npct = max(0, min(90, npct))              # 空拍概率 0~90%

        with S.lock:
            changed = (S.frames_min, S.frames_max, S.neutral_pct) != (lo, hi, npct)
            S.frames_min, S.frames_max = lo, hi
            S.neutral_pct = npct
        if changed:
            save_params({"frames_min": lo, "frames_max": hi, "neutral_pct": npct})

    def on_start(self):
        self.apply_params()
        with S.lock:
            S.running = True

    def on_stop(self):
        with S.lock:
            S.running = False

    def on_taunt(self):
        """请求一次嘲讽（六个拳脚键同时按）。乱按没在跑也能用。"""
        with S.lock:
            S.taunt_req = True

    def poll(self):
        # 兜底通道：轮询 F9/F7/F10/F8（键盘钩子/系统热键正常时它们已先处理，
        # 这里重复上报的会被 200ms 去重吃掉）——面板按钮/热键都会在下一拍反映出来。
        poll_hotkeys()
        with S.lock:
            status, level, latest, running = (S.status, S.status_level,
                                              S.latest_key, S.running)
        colors = {"idle": "#9e9e9e", "run": "#2e7d32",
                  "pause": "#ef6c00", "error": "#c62828"}
        self.status_lbl.config(text="● " + status, fg=colors.get(level, "#9e9e9e"))
        self.key_lbl.config(text="最近按钮：" +
                            ("—" if latest == "-" else latest))
        self.start_btn.config(text="▶ 开始乱按 (F9)",
                              state=("disabled" if running else "normal"),
                              bg=("#9e9e9e" if running else "#2e7d32"))
        self.stop_btn.config(state=("normal" if running else "disabled"))
        self.root.after(120, self.poll)

    def on_close(self):
        QUIT.set()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _on_tk_error(self, exc, val, tb):
        _log_exc()


# ------------------------------------------------------------------ 自检
def selftest():
    ok = True
    # 控制台可能是 GBK：窗口标题里出现 GBK 编不出的字符（如零宽空格）时 print 会崩，
    # 这里把编码错误降级为替换字符，保证自检始终能跑完。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass
    print("[selftest] python :", sys.version.replace("\n", " "))
    print("[selftest] exe    :", sys.executable)
    if vg is None:
        print("[selftest] vgamepad: 导入失败 ->", VG_IMPORT_ERR or "(未知)")
        if is_driver_bus_error(VG_IMPORT_ERR):
            print("[selftest] 判定：vgamepad 库其实已安装，是 ViGEmBus 总线设备没起来")
            print("[selftest] 修法：重启 -> 重装 tools\\ViGEmBus_1.22.0_x64_x86_arm64.exe -> 再重启")
        else:
            print("[selftest] 判定：vgamepad 库没装（pip install vgamepad）")
        ok = False
    else:
        print("[selftest] vgamepad: OK")
        try:
            vg.VX360Gamepad()
            print("[selftest] ViGEmBus driver: OK (virtual pad created)")
        except Exception as e:
            print("[selftest] ViGEmBus driver: FAIL:", e)
            ok = False

    try:
        r = tk.Tk()
        r.withdraw()
        r.update()
        r.destroy()
        print("[selftest] tkinter : OK")
    except Exception as e:
        print("[selftest] tkinter : FAIL:", e)
        ok = False

    title, exe = query_foreground()
    print("[selftest] foreground title:", title)
    print("[selftest] foreground exe  :", exe)
    print("[selftest] RESULT :", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ------------------------------------------------------------------ 入口
def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # 把系统计时器精度提到 1ms：默认 15.6ms 的粒度会让"1~3 帧"的节奏严重失准
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass

    if SELFTEST:
        return selftest()

    # 前置检查：vgamepad（也可能是「库装了但 ViGEmBus 总线没起来」，要分流）
    if vg is None:
        _log_line("vgamepad 不可用：%s" % (VG_IMPORT_ERR or "(未知)"))
        root = tk.Tk()
        root.withdraw()
        if is_driver_bus_error(VG_IMPORT_ERR):
            messagebox.showerror("虚拟手柄驱动未就绪",
                                driver_error_detail(VG_IMPORT_ERR) +
                                "\n错误信息：" + VG_IMPORT_ERR)
        else:
            messagebox.showerror(
                "缺少 vgamepad 库",
                "未安装 Python 库 vgamepad。\n\n请在命令行运行：\n"
                "    py -m pip install vgamepad\n\n然后重新启动本程序。\n\n"
                "（如果装过还是这样，可能是启动时用了别的 Python：\n"
                "  本程序需要那个能 import vgamepad 的解释器）")
        root.destroy()
        return 1

    # 前置检查：虚拟手柄驱动
    ok, err = connect_pad()
    if not ok:
        _log_line("创建虚拟手柄失败：%s" % err)
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("虚拟手柄驱动未就绪",
                            driver_error_detail(err) + "\n错误信息：" + err)
        root.destroy()
        return 1

    _log_line("启动（手柄版，pid=%d）" % os.getpid())
    threading.Thread(target=worker, daemon=True, name="pad-worker").start()
    start_hotkey_listeners()                    # 键盘钩子 + 系统热键 + 轮询兜底

    root = tk.Tk()
    app = App(root)
    root.after(350, lambda: _log_line(
        "热键监听：" + (" / ".join(_LISTENER_INFO) or "(无报告)")))
    if AUTO_START:
        app.on_start()
        _log_line("自动开始乱按（--auto）")
    root.mainloop()
    stop_hotkey_listeners()
    QUIT.set()
    time.sleep(0.15)
    release_all_once()          # 退出前松开所有键（防卡键）
    try:
        ctypes.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass
    _log_line("退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
