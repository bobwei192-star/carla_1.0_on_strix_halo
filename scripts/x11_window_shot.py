#!/usr/bin/env python
"""针对具体 X11 窗口 ID 截图（绕过 XWayland 对 root window 的 XGetImage 限制）。

ffmpeg x11grab / PIL.ImageGrab 都抓 root window，在 XWayland 下被拒：
  X get_image failed: error 8 (73,0,858)  /  MIT-SHM major_code 130
本脚本用 libX11 的 XGetImage 直接抓目标窗口 drawable，不经 MIT-SHM。

用法:
  python x11_window_shot.py <0xWINDOWID> <输出.png>
  python x11_window_shot.py auto <输出.png>     # 自动找 Carla/Unreal 窗口
"""
import ctypes
import ctypes.util
import os
import re
import subprocess
import sys

from PIL import Image

XA = sorted(
    __import__("glob").glob("/run/user/1000/.mutter-Xwaylandauth*"),
    key=os.path.getmtime,
    reverse=True,
)
if XA:
    os.environ["XAUTHORITY"] = XA[0]
os.environ.setdefault("DISPLAY", ":0")

libX11 = ctypes.CDLL(ctypes.util.find_library("X11"))


class XImage(ctypes.Structure):
    pass


XImage._fields_ = []  # 只通过指针访问，字段由下面偏移读取

libX11.XOpenDisplay.restype = ctypes.c_void_p
libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
libX11.XDefaultRootWindow.restype = ctypes.c_ulong
libX11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
libX11.XGetWindowAttributes.restype = ctypes.c_int
libX11.XGetImage.restype = ctypes.POINTER(XImage)
libX11.XGetImage.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int,
]
libX11.XDestroyImage.restype = ctypes.c_int
libX11.XDestroyImage.argtypes = [ctypes.POINTER(XImage)]
libX11.XCloseDisplay.restype = ctypes.c_int
libX11.XCloseDisplay.argtypes = [ctypes.c_void_p]
libX11.XGetPixel.restype = ctypes.c_ulong
libX11.XGetPixel.argtypes = [ctypes.POINTER(XImage), ctypes.c_int, ctypes.c_int]

# XWindowAttributes: x,y,width,height,border_width,depth,... 头部布局
class XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int),
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong),
        ("class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
        ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
        ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
        ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong),
        ("map_installed", ctypes.c_int), ("map_state", ctypes.c_int),
        ("all_event_masks", ctypes.c_long), ("your_event_mask", ctypes.c_long),
        ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
    ]


libX11.XGetWindowAttributes.argtypes = [
    ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWindowAttributes)
]


def find_window(pattern=r"(?i)carla|unreal"):
    out = subprocess.run(
        ["xwininfo", "-root", "-tree"], capture_output=True, text=True
    ).stdout
    for line in out.splitlines():
        if re.search(pattern, line):
            m = re.search(r"0x[0-9a-fA-F]+", line)
            if m:
                return int(m.group(0), 16), line.strip()
    return None, None


def grab(wid, path):
    dpy = libX11.XOpenDisplay(None)
    if not dpy:
        raise RuntimeError("XOpenDisplay 失败")
    try:
        attr = XWindowAttributes()
        if not libX11.XGetWindowAttributes(dpy, wid, ctypes.byref(attr)):
            raise RuntimeError("XGetWindowAttributes 失败")
        w, h = attr.width, attr.height
        if w <= 0 or h <= 0:
            raise RuntimeError(f"窗口尺寸非法 {w}x{h}")
        print(f"window 0x{wid:x} -> {w}x{h} depth={attr.depth}")
        img = libX11.XGetImage(dpy, wid, 0, 0, w, h, 0xFFFFFFFF, 2)  # ZPixmap=2
        if not img:
            raise RuntimeError("XGetImage 返回 NULL")
        try:
            # XImage 结构：前 4 个 int 是 width,height,xoffset,byte_order 等，
            # data 指针位于固定偏移；用 XGetPixel 逐像素最稳妥（避免布局差异）。
            out = Image.new("RGB", (w, h))
            px = out.load()
            for y in range(h):
                for x in range(w):
                    v = libX11.XGetPixel(img, x, y)
                    px[x, y] = ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)
            out.save(path)
            print(f"SHOT OK {w}x{h} -> {path}")
            return True
        finally:
            libX11.XDestroyImage(img)
    finally:
        libX11.XCloseDisplay(dpy)


if __name__ == "__main__":
    arg = sys.argv[1]
    out_path = sys.argv[2]
    if arg == "auto":
        wid, name = find_window()
        if not wid:
            print("SHOT FAIL 未找到 Carla/Unreal 窗口")
            sys.exit(1)
        print(f"找到窗口: {name}")
    else:
        wid = int(arg, 16)
    try:
        ok = grab(wid, out_path)
        sys.exit(0 if ok else 1)
    except Exception as e:
        print(f"SHOT FAIL {type(e).__name__}: {e}")
        sys.exit(1)
