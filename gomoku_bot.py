# -*- coding: utf-8 -*-
"""五子棋自动对弈机器人（adb 截图 -> 识别盘面 -> Rapfi 引擎算点 -> adb 落子）

适配 App: com.wedobest.fivechess.huawei
设备    : HUAWEI FOA-AL00 / 1084x2412 / density 480

核心闭环
  1. adb screencap 截图
  2. 自动测线（不写死坐标，兼容 联机/人机/双人 不同 y 起点）
  3. 逐点识别 黑子/白子/空（r=21 圆盘特征 + 绿勾气泡排除）
  4. Rapfi 引擎算最优落点
  5. adb 按压落子 + 落子校验（失败自动关广告后重试）

用法
  python gomoku_bot.py --color B --time 2000          # 执黑下第一步
  python gomoku_bot.py --color W --time 3000          # 执白
  python gomoku_bot.py --dry-run                      # 只识别盘面，不落子
  python gomoku_bot.py --probe-only                   # 只测线+识别一次就退出
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime

import numpy as np
from PIL import Image

# ------------------------------------------------------------------ 常量配置
ADB = r"C:\Users\17601\Desktop\scrcpy-win64-v5.0.1\adb.exe"
SERIAL = "9TM9K23524017179"
TMP_DIR = r"C:\gomoku_tmp"                      # adb pull 只能进 ASCII 路径
# 临时截图文件名带进程号：两个实例（或调试脚本）同时跑时不会互相覆写，避免读到半截 PNG
SHOT_TMP = os.path.join(TMP_DIR, "bot_shot_%d.png" % os.getpid())
SHOT_REMOTE = "/sdcard/bot_shot_%d.png" % os.getpid()
ENGINE_DIR = r"C:\五子棋\engine"

# 广告关闭按钮（华为广告 SDK 底部广告条右上角 X）
# 广告条关闭 X 的候选位置（整屏实测：旧版在 App 窗口内，新版在窗口下方的独立浮层）
AD_CLOSE_CAND = [(1059, 2277), (1054, 2130)]
AD_CLOSE = AD_CLOSE_CAND[0]

# 底部游戏按钮行实测 y（整屏 1084×2412 下的真实位置；早期 2136 是缩放换算错误）
BTN_ROW_Y = 2022
BTN_LEAVE = (161, 2022)
BTN_UNDO = (412, 2022)
BTN_REPLAY = (677, 2022)
BTN_LEVEL = (917, 2022)

# 主菜单四大模式（整屏实测中心）
MENU_ONLINE = (301, 1170)
MENU_HUMAN = (764, 1093)
MENU_ENDGAME = (301, 1671)
MENU_TWO = (816, 1595)

COLS = "ABCDEFGHIJKLMNO"

# 测线时"暗像素占比"的多个阈值，由严到松依次尝试（棋子密布时白子会打断网格线）
ROW_THRESHOLDS = (0.55, 0.48, 0.42, 0.36)
GRID_STEP = (72.32, 72.29)      # 本机实测：竖线步长 / 横线步长
GRID_PAD = 20.5                 # 首线到米黄底色框内边（上下左右一致）


# ------------------------------------------------------------------ adb 封装
def _run(args, timeout=20):
    """执行命令，绝不抛异常（超时/找不到 adb 都退化成错误码）"""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "ignore") + p.stderr.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return 124, "命令超时(%ss): %s" % (timeout, " ".join(str(a) for a in args))
    except (OSError, ValueError) as e:
        return 127, "命令无法执行: %r" % (e,)


def adb(*args, timeout=20):
    return _run([ADB, "-s", SERIAL] + list(args), timeout=timeout)


def _pull(src, dest, timeout=30):
    """拉文件，拉之前先删残留，避免拿到上一次的旧图"""
    if os.path.exists(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
    rc, out = adb("pull", src, dest, timeout=timeout)
    ok = os.path.exists(dest) and rc == 0
    if not ok:
        raise RuntimeError("adb pull 失败 rc=%d %s" % (rc, out.strip()[:200]))
    return dest


def screenshot(dest=None, timeout=30):
    """截图并返回 RGB ndarray（原图尺寸 1084x2412）"""
    os.makedirs(TMP_DIR, exist_ok=True)
    rc, out = adb("shell", "screencap", "-p", SHOT_REMOTE, timeout=timeout)
    if rc != 0:
        raise RuntimeError("screencap 失败 rc=%d %s" % (rc, out.strip()[:200]))
    _pull(SHOT_REMOTE, SHOT_TMP, timeout=timeout)
    adb("shell", "rm", SHOT_REMOTE, timeout=10)
    try:
        img = Image.open(SHOT_TMP).convert("RGB")
        arr = np.asarray(img)
    except (OSError, ValueError) as e:
        raise RuntimeError("截图解码失败: %r" % (e,))
    if dest:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        img.save(dest)
    return arr


def press(x, y, ms=0):
    """快速点击。实测瞬时 tap 对按钮有效；0 长度的 swipe(长按) 反而常被忽略"""
    adb("shell", "input", "tap", str(int(x)), str(int(y)))


def swipe(x1, y1, x2, y2, ms=420):
    """带时长的滑动（用于把气泡拖进红圈位置确认）"""
    adb("shell", "input swipe %d %d %d %d %d"
        % (int(x1), int(y1), int(x2), int(y2), int(ms)))


def _close_ad_inline():
    """已废弃：实测在 tap 前附加关广告点击会让这次落子点击失效，仅保留作诊断用"""
    return "; ".join(["input tap %d %d" % (cx, cy) for cx, cy in AD_CLOSE_CAND]) + "; sleep 0.08"


def press_after_ad_close(x, y, ms=0):
    """已废弃、不要用在落子上（会把落子点击搅掉）。仅保留给临时诊断。"""
    adb("shell", "%s; input tap %d %d" % (_close_ad_inline(), int(x), int(y)))


def drag_after_ad_close(x1, y1, x2, y2, ms=400):
    """已废弃、不要用在落子上。仅保留给临时诊断。"""
    adb("shell", "%s; input swipe %d %d %d %d %d"
        % (_close_ad_inline(), int(x1), int(y1), int(x2), int(y2), int(ms)))


def close_ad():
    """依次点所有已知的广告条 X 位置"""
    for cx, cy in AD_CLOSE_CAND:
        press(cx, cy)
        time.sleep(0.2)


def ui_has_ad():
    """dump 视图层级，看是否还有华为广告 SDK 的全屏拦截层"""
    remote = "/sdcard/ui_bot_%d.xml" % os.getpid()
    local = os.path.join(TMP_DIR, "ui_bot_%d.xml" % os.getpid())
    adb("shell", "uiautomator", "dump", remote)
    try:
        _pull(remote, local, timeout=30)
    except RuntimeError:
        return False
    finally:
        adb("shell", "rm", remote, timeout=10)
    with open(local, encoding="utf-8", errors="ignore") as fh:
        txt = fh.read()
    return bool(re.search(r"hiad|haid", txt))


# ------------------------------------------------------------------ 棋盘测线
def _gray(a):
    a = a.astype(float)
    return a[:, :, 0] * 0.299 + a[:, :, 1] * 0.587 + a[:, :, 2] * 0.114


def _runs(idx):
    out, s = [], None
    for i, v in enumerate(idx):
        if s is None:
            s = v
        elif v - idx[i - 1] > 2:
            out.append((s, idx[i - 1]))
            s = v
    if s is not None:
        out.append((s, idx[-1]))
    return [(a, b, (a + b) / 2.0) for a, b in out]


def beige_bbox(arr, min_frac=0.35):
    """棋盘米黄底色的外接框：这是最稳的锚点（气泡/棋子都不影响它的边界）"""
    R = arr[:, :, 0].astype(np.int16)
    G = arr[:, :, 1].astype(np.int16)
    B = arr[:, :, 2].astype(np.int16)
    beige = (R > 170) & (R < 245) & (G > 130) & (G < 215) & (B < 160) & (G < R)
    rows = beige.mean(axis=1)
    cols = beige.mean(axis=0)
    rr = np.where(rows > min_frac)[0]
    cc = np.where(cols > min_frac)[0]
    if len(rr) < 50 or len(cc) < 50:
        return None
    return int(rr[0]), int(rr[-1]), int(cc[0]), int(cc[-1])


def detect_grid(arr):
    """自动测出 15 条横线 + 15 条竖线的像素中心
    用米黄底色框约束，避免把棋盘外框/气泡边缘误当成网格线（那会导致整体偏移一格）
    """
    g = _gray(arr)
    H, W = g.shape
    bb = beige_bbox(arr)
    if bb:
        bt, bb_b, bl, br = bb
    else:
        bt, bb_b, bl, br = 0, H - 1, 0, W - 1

    x_lo, x_hi = max(20, int(W * 0.03)), min(W - 1, int(W * 0.97))
    row_ratio = (g[:, x_lo:x_hi] < 140).mean(axis=1)

    # 多阈值重试：棋子密布时白子会把整条网格线打断，固定 0.55 会直接测不到线。
    # 由严到松逐个试，第一个能拼出 15 条等距线的阈值胜出。
    seq_h = None
    for th in ROW_THRESHOLDS:
        rows = [y for y in range(max(0, bt - 8), min(H - 1, bb_b + 8) + 1) if row_ratio[y] > th]
        hcent = [c for (a, b, c) in _runs(rows) if 0 <= b - a <= 12]
        seq_h = _pick15_best(hcent, bt, bb_b, m_top=20, m_bot=18)
        if seq_h:
            break

    y_lo, y_hi = (int(seq_h[0]) + 5, int(seq_h[-1]) - 5) if seq_h else (int(H * 0.25), int(H * 0.7))
    y_lo, y_hi = max(0, y_lo), min(H - 1, y_hi)
    col_ratio = (g[y_lo:y_hi, :] < 140).mean(axis=0)
    seq_v = None
    for th in ROW_THRESHOLDS:
        cols = [x for x in range(max(0, bl - 8), min(W - 1, br + 8) + 1) if col_ratio[x] > th]
        vcent = [c for (a, b, c) in _runs(cols) if 0 <= b - a <= 12]
        seq_v = _pick15_best(vcent, bl, br, m_top=20, m_bot=18)
        if seq_v:
            break

    if not seq_h or not seq_v:
        syn = _grid_from_bbox_fallback(bb, row_ratio, g, W, H)
        return syn if syn else {"ok": False}

    # 几何自检：步长必须像 15 路棋盘，首线必须贴着底色框内边（防把外框/装饰线当网格）
    step_y = (seq_h[-1] - seq_h[0]) / 14.0
    step_x = (seq_v[-1] - seq_v[0]) / 14.0
    if not (60 <= step_y <= 85) or not (60 <= step_x <= 85):
        return {"ok": False}
    if bb:
        if abs(seq_h[0] - (bt + 20)) > 14 or abs(seq_v[0] - (bl + 20)) > 14:
            return {"ok": False}
    return {"ok": True,
            "x": [round(v, 1) for v in seq_v],
            "y": [round(v, 1) for v in seq_h],
            "step_x": round(float(np.mean(np.diff(seq_v))), 2),
            "step_y": round(float(np.mean(np.diff(seq_h))), 2),
            "beige": bb}


def _pick15_best(centers, lo, hi, m_top=20, m_bot=18, gap_lo=55, gap_hi=90, tol=6.0):
    """从候选线里拟合 15 条等距线。
    不能要求"连续 15 个候选"——密布棋子/装饰线会多出几条假候选，一旦夹在中间，
    连续窗口全部作废（实测 16 个候选就整盘测线失败）。改成按步长贪心拟合：
    先估出单格步长，再从每个候选当首线往后按 step*k 找最近候选（±tol）。
    评分仍用"首末线到底色框内边距 + 相邻线极差"。"""
    if len(centers) < 15:
        return None
    centers = sorted(centers)
    diffs = [b - a for a, b in zip(centers, centers[1:]) if gap_lo <= b - a <= gap_hi]
    if not diffs:
        return None
    step0 = float(np.median(diffs))
    best = None
    for i, c0 in enumerate(centers):
        seq = [c0]
        cur = i
        for k in range(1, 15):
            want = c0 + step0 * k
            hit = None
            for j in range(cur + 1, len(centers)):
                cj = centers[j]
                if cj > want + tol:
                    break
                if abs(cj - want) <= tol:
                    hit = (j, cj)
                    break
            if hit is None:
                break
            cur, c = hit
            seq.append(c)
        if len(seq) < 15:
            continue
        gaps = np.diff(seq)
        if gaps.min() < gap_lo - 12 or gaps.max() > gap_hi + 12:
            continue
        score = (abs(seq[0] - (lo + m_top)) + abs((hi - m_bot) - seq[-1])
                 + (gaps.max() - gaps.min()) * 6)
        if best is None or score < best[0]:
            best = (score, seq)
    return list(best[1]) if best else None


def bbox_matches_grid(grid, bb, tol=8):
    """底色框和缓存几何是否仍然吻合（盘中棋盘位置不会变，用它验证缓存可用）"""
    if not grid or not bb or not grid.get("ok"):
        return False
    return (abs(grid["y"][0] - (bb[0] + 20)) <= tol
            and abs(grid["x"][0] - (bb[2] + 20)) <= tol
            and grid["y"][-1] < bb[1] + 12
            and grid["x"][-1] < bb[3] + 12)


def _grid_from_bbox_fallback(bb, row_ratio, g, W, H):
    """棋子密布到把网格线完全打断时的最后兜底：
    用米黄底色框 + 实测步长合成 15×15 网格，再用"该位置附近确实有暗线候选"反向校验。
    校验不过就返回 None（宁可报测线失败，也不能拿错位的假网格去落子）。"""
    if not bb:
        return None
    bt, bb_b, bl, br = bb
    sx, sy = GRID_STEP
    x0, y0 = bl + GRID_PAD, bt + GRID_PAD
    xs = [round(x0 + i * sx, 1) for i in range(15)]
    ys = [round(y0 + i * sy, 1) for i in range(15)]
    if xs[-1] > br or ys[-1] > bb_b:
        return None

    # 反向校验：每个合成位置身边 6px 内要有暗线候选（阈值放到最松 0.30）
    loose_rows = [y for y in range(max(0, bt - 8), min(H - 1, bb_b + 8) + 1) if row_ratio[y] > 0.30]
    hcent = [c for (a, b, c) in _runs(loose_rows) if 0 <= b - a <= 14]
    hit_y = sum(1 for y in ys if any(abs(c - y) <= 6 for c in hcent))
    if hit_y < 9:
        return None

    y_lo, y_hi = max(0, int(ys[0]) + 5), min(H - 1, int(ys[-1]) - 5)
    col_ratio = (g[y_lo:y_hi, :] < 140).mean(axis=0)
    loose_cols = [x for x in range(max(0, bl - 8), min(W - 1, br + 8) + 1) if col_ratio[x] > 0.30]
    vcent = [c for (a, b, c) in _runs(loose_cols) if 0 <= b - a <= 14]
    hit_x = sum(1 for x in xs if any(abs(c - x) <= 6 for c in vcent))
    if hit_x < 9:
        return None

    return {"ok": True, "x": xs, "y": ys,
            "step_x": round(sx, 2), "step_y": round(sy, 2),
            "beige": bb, "synthetic": True}


# ------------------------------------------------------------------ 棋子识别
R_DISC = 21
_YY, _XX = np.ogrid[-R_DISC:R_DISC + 1, -R_DISC:R_DISC + 1]
_DISC = (_YY * _YY + _XX * _XX) <= R_DISC * R_DISC


class Frame:
    """一次截图预计算好灰度/三通道，避免逐点重复算整图（否则一次识别要 40s）"""

    def __init__(self, arr):
        self.arr = arr
        self.g = _gray(arr)
        self.R = arr[:, :, 0].astype(np.int16)
        self.G = arr[:, :, 1].astype(np.int16)
        self.B = arr[:, :, 2].astype(np.int16)

    def feats(self, x, y):
        r = R_DISC
        y0, x0 = int(y) - r, int(x) - r
        if y0 < 0 or x0 < 0:
            return None
        p = self.g[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
        if p.shape != (2 * r + 1, 2 * r + 1):
            return None
        pr = self.R[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
        pg = self.G[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
        pb = self.B[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]
        v = p[_DISC]
        green = ((pg > pr + 40) & (pg > pb + 40))[_DISC].mean()
        return {
            "mean": float(v.mean()),
            "std": float(v.std()),
            "dark": float((v < 100).mean()),          # 黑子特征
            "bright": float((v > 180).mean()),        # 白子特征
            "green": float(green),                    # 绿勾气泡特征
            "red": float(((pr > 150) & (pg < 90) & (pb < 90))[_DISC].mean()),
        }


def classify(f, in_bubble=False):
    """黑 / 白 / 空（绿勾气泡按空处理）"""
    if f is None:
        return "?"
    if f["dark"] > 0.75:             # 强黑子，气泡压着也能认出来
        return "B"
    if f["green"] > 0.15:            # 绿勾像素（实测气泡 0.51 / 白子 0.00）
        return "."
    if in_bubble:                    # 落在气泡白色圆盘范围内，纯白不是棋子
        return "."
    if f["dark"] > 0.60:             # 实测黑子 0.98，星位 0.23
        return "B"
    if f["bright"] > 0.38:           # 实测白子 0.50；气泡边缘伪白 0.29
        return "W"
    return "."


def find_bubbles(frame, grid, th=232, min_area=1500, radius_pad=78, pad_y=145):
    """找绿勾气泡的白色大圆盘（面积远大于棋子，亮度也高于白子）
    返回 [(cx, cy)]，落点判定时凡圆心 78px 内的点一律按空处理

    pad_y：气泡浮在目标点上方 104px，落在第 1/2 行时会跑到棋盘外（甚至压住外框），
    所以纵向搜索范围要往上多探 145px，否则“找不到气泡”→ 落子永远确认不了。
    """
    g = frame.g
    H = g.shape[0]
    x0, x1 = int(grid["x"][0]) - 12, int(grid["x"][-1]) + 12
    y0 = max(0, int(grid["y"][0]) - pad_y)
    y1 = min(H, int(grid["y"][-1]) + 12)
    sub = g[y0:y1, x0:x1]
    mask = sub > th
    if mask.sum() < min_area:
        return []
    if mask.sum() > 40000:
        # 纯白像素太多（弹窗/转场/广告页），不可能是气泡；也避免 BFS 在大块上跑几十秒
        return []
    # 简易连通域（BFS），只在 mask 像素上跑
    pts = np.argwhere(mask)
    seen = np.zeros(mask.shape, bool)
    out = []
    for (sy, sx) in pts:
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        acc_y = acc_x = n = 0
        while stack:
            cy_, cx_ = stack.pop()
            acc_y += cy_
            acc_x += cx_
            n += 1
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy_ + dy, cx_ + dx
                    if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] \
                            and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        if n >= min_area:
            cx, cy = acc_x / n + x0, acc_y / n + y0
            # 棋盘外（气泡浮到第 1/2 行上方）的亮块必须验「绿勾」，否则会把
            # 背景水面的白色亮斑当成气泡
            if cy < grid["y"][0] - 6 or cy > grid["y"][-1] + 6:
                if not (4000 <= n <= 22000):
                    continue
                f = frame.feats(cx, cy)
                if f is None or f["green"] < 0.12:
                    continue
            out.append((cx, cy, n))
    return [(cx, cy) for (cx, cy, _) in out]


def read_board(frame, grid):
    """返回 15x15 字符板 + 特征表"""
    bubbles = find_bubbles(frame, grid)
    board = [["."] * 15 for _ in range(15)]
    feats = {}
    for yi, y in enumerate(grid["y"]):
        for xi, x in enumerate(grid["x"]):
            f = frame.feats(x, y)
            feats[(xi, yi)] = f
            in_bub = any((x - bx) ** 2 + (y - by) ** 2 < 78 ** 2 for (bx, by) in bubbles)
            board[yi][xi] = classify(f, in_bub)
    return board, feats


def board_str(board):
    return "\n".join("".join(row) for row in board)


def color_blob(arr, kind, region):
    """在 region=(x0,y0,x1,y1) 内找橙色/绿色按钮块 → (cx, cy, n) 或 None"""
    x0, y0, x1, y1 = region
    sub = arr[y0:y1, x0:x1].astype(np.int16)
    R, G, B = sub[:, :, 0], sub[:, :, 1], sub[:, :, 2]
    if kind == "orange":
        m = (R > 200) & (G > 90) & (G < 180) & (B < 90)
    else:
        m = (R < 110) & (G > 150) & (B > 80) & (B < 190)
    ys, xs = np.nonzero(m)
    if len(xs) < 3000:
        return None
    return (int(xs.mean()) + x0, int(ys.mean()) + y0, int(len(xs)))


def dialog_first_move(arr):
    """「请选择先后手」弹窗：左边橙(先手)+右边绿(后手)，按钮中心 y≈1237
    竖向上界收到 1285，避免够到结算弹窗的按钮（y≈1337）造成互相误判"""
    l = color_blob(arr, "orange", (60, 1150, 540, 1285))
    r = color_blob(arr, "green", (560, 1150, 1020, 1285))
    if l and r:
        return (l[0], l[1]), (r[0], r[1])
    return None


def dialog_end(arr):
    """结算弹窗：左橙(保存对局)+右绿(再来一局)，按钮中心 y≈1333（比先后手弹窗低约 95px）"""
    l = color_blob(arr, "orange", (120, 1290, 500, 1400))
    r = color_blob(arr, "green", (520, 1290, 940, 1400))
    if l and r:
        return (l[0], l[1]), (r[0], r[1])
    return None


def dialog_again(arr):
    """通用「再来一局」定位：只在结算按钮那一行（y 1300~1800）找绿色按钮块。
    这样不会误吃「请选择先后手」的绿色「后手」(y≈1232)，也不会吃底部绿色「难度」(y≈2022)。
    版式变了（比如「保存对局」变灰不可点导致橙按钮判据失效）也能靠它点中。
    """
    return color_blob(arr, "green", (100, 1300, 1000, 1800))


def count_stones(board):
    n_b = sum(row.count("B") for row in board)
    n_w = sum(row.count("W") for row in board)
    return n_b, n_w


def find_winner(board, need=5):
    """五连检测，返回 'B'/'W'/None"""
    for y in range(15):
        for x in range(15):
            c = board[y][x]
            if c not in ("B", "W"):
                continue
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                cnt = 1
                for k in range(1, need):
                    nx, ny = x + dx * k, y + dy * k
                    if 0 <= nx < 15 and 0 <= ny < 15 and board[ny][nx] == c:
                        cnt += 1
                    else:
                        break
                if cnt >= need:
                    return c
    return None


def label_of(xi, yi):
    return "%s%d" % (COLS[xi], yi + 1)


def pixel_of(grid, xi, yi):
    return int(round(grid["x"][xi])), int(round(grid["y"][yi]))


# ------------------------------------------------------------------ 引擎
sys.path.insert(0, ENGINE_DIR)
from rapfi_client import RapfiEngine            # noqa: E402


# ------------------------------------------------------------------ 主流程
class Bot:
    def __init__(self, color, time_ms, threads, rule, dry_run, log_path, shot_dir,
                 max_moves=400, poll=0.6, verbose=True):
        self.color = color
        self.time_ms = time_ms
        self.threads = threads
        self.rule = rule
        self.dry_run = dry_run
        self.log_path = log_path
        self.shot_dir = shot_dir
        self.max_moves = max_moves
        self.poll = poll
        self.verbose = verbose
        self.engine = RapfiEngine(rule=rule, threads=threads, time_ms=time_ms)
        self.grid = None
        self.last_frame = None
        self.fail_streak = 0
        self.games = 0
        self.epoch = 0              # 局代际：每确认进入新局 +1，主循环据此作废旧盘面                  # 已开过的局数（无限模式用来报进度）
        self.idle_timeouts = 0          # 连续等待超时次数（无限模式用来自愈）

    # ---- 日志
    def log(self, msg):
        line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
        if self.verbose:
            print(line)
        d = os.path.dirname(self.log_path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def _next_game_on(self, arr):
        """判断这一帧是否已经进入「新的一局」：能测到棋盘，且棋子数 ≤2（新局刚开局）"""
        gd = detect_grid(arr)
        if not gd.get("ok"):
            return False, 0, None
        b = read_board(Frame(arr), gd)[0]
        n = sum(count_stones(b))
        return (n <= 2), n, gd

    def _wait_next_game(self, timeout=14.0):
        """点掉结算弹窗后再确认一次：真的开了下一局（空盘/先后手弹窗）。
        期间若「请选择先后手」弹出就自动选（按 --color）；若弹窗还在就补点绿色按钮。
        返回 True=已在新局；False=超时（交回主循环继续处理）"""
        t0 = time.time()
        repress = 0
        while time.time() - t0 < timeout:
            time.sleep(1.0)
            try:
                arr = screenshot()
            except Exception:
                continue

            first = dialog_first_move(arr)
            if first:
                btn = first[0] if self.color == "B" else first[1]
                self.log("  新局初始化：「请选择先后手」→ 点(%d,%d)" % btn)
                press(*btn)
                time.sleep(1.6)
                continue

            ok, n, gd = self._next_game_on(arr)
            if ok:
                self.grid = gd              # 顺带把几何缓存换成新局的
                self.epoch += 1
                self.log("  新局已开始：盘面识别成功，当前棋子数 %d" % n)
                return True

            end_btn = dialog_end(arr)
            again = dialog_again(arr)
            if end_btn or again:
                if repress < 3:             # 点击被吞（广告/动画没走完）时补点
                    repress += 1
                    tgt = end_btn[1] if end_btn else (again[0], again[1])
                    self.log("  结算弹窗仍在，补点「再来一局」(%d,%d) 第 %d 次" % (tgt[0], tgt[1], repress))
                    press(tgt[0], tgt[1])
                    time.sleep(1.6)
                    continue
                self.log("  已补点 3 次仍未进入新局，交给主循环处理")
                return False
        self.log("  等新局超时（%.0fs），交给主循环继续" % timeout)
        return False

    # ---- 结算/先后手弹窗
    def handle_dialogs(self, arr=None):
        """识别并处理弹窗：结算弹窗点「再来一局」并确认进入下一局；先后手弹窗点「先手」。
        返回 True 表示处理过（调用方应重新截图）。"""
        if arr is None:
            arr = screenshot()
        end_btn = dialog_end(arr)
        first = dialog_first_move(arr)
        if end_btn:
            save_btn, again_btn = end_btn
            self.log("检测到结算弹窗，点绿色「再来一局」(%d,%d)" % again_btn)
            press(*again_btn)
            time.sleep(2.0)
            self._wait_next_game()
            return True
        if first:
            first_btn, second_btn = first
            btn = first_btn if self.color == "B" else second_btn
            self.log("检测到「请选择先后手」弹窗，我执%s → 点(%d,%d)" %
                     ("黑/先手" if self.color == "B" else "白/后手", btn[0], btn[1]))
            self.epoch += 1
            press(*btn)
            time.sleep(2.0)
            return True
        # 兜底：版式变化（胜负文案/按钮位置不同、甚至「保存对局」变灰不可点）时，
        # 只要结算行（y 1300~1800）有绿色按钮就点它。该区间已排除「后手」与底部「难度」。
        again = dialog_again(arr)
        if again:
            self.log("检测到结算行绿色按钮，按「再来一局」处理 (%d,%d)" % (again[0], again[1]))
            press(again[0], again[1])
            time.sleep(2.0)
            self._wait_next_game()
            return True
        return False

    # ---- 让画面回到棋盘态（返回已经确认是棋盘的截图，失败返回 None）
    def ensure_board(self, tries=6):
        """弹窗 → 处理；菜单 → 进人机；棋盘 → 返回该帧"""
        for i in range(tries):
            try:
                arr = screenshot()
            except Exception as e:
                self.log("截屏失败，重试：%r" % (e,))
                time.sleep(1.5)
                continue
            if self.handle_dialogs(arr):
                self.grid = None
                time.sleep(0.8)
                continue
            try:
                grid = detect_grid(arr)
            except Exception:
                grid = None
            if grid and grid.get("ok"):
                if self.grid is None:
                    self.grid = grid
                return arr
            if bbox_matches_grid(self.grid, beige_bbox(arr)):
                self.log("测线失败，但几何缓存与底色框吻合，按棋盘态处理")
                return arr
            self.log("当前不在棋盘（第 %d 次）" % (i + 1))
            if beige_bbox(arr) is not None:
                # 画面已经有棋盘底色 → 只是测线/刷新异常，绝不能去点主菜单坐标
                # （人机格子 (764,1093) 正好落在棋盘里，点了等于往盘上乱落子）
                self.log("画面已有棋盘底色，判定为测线/刷新异常，重试截屏")
                time.sleep(1.0)
                continue
            self.log("确认停在菜单，点「人机」进入对局")
            press(*MENU_HUMAN)
            time.sleep(2.2)
        return None

    # ---- 截图 + 识别
    def capture(self, save_name=None):
        dest = os.path.join(self.shot_dir, save_name) if (save_name and self.shot_dir) else None
        if dest:
            os.makedirs(self.shot_dir, exist_ok=True)
        arr = screenshot(dest)
        return self._read(arr, dest)

    def _read(self, arr, dest=None):
        bb = beige_bbox(arr)
        need = self.grid is None
        if not need and bb:
            # 用米黄底色框校验缓存几何，漂移超过 8px 就重测（防止气泡/转场带偏）
            if abs(self.grid["y"][0] - (bb[0] + 20)) > 8 or abs(self.grid["x"][0] - (bb[2] + 20)) > 8:
                self.log("几何漂移，重新测线")
                need = True
        if need:
            cached = self.grid
            gd = detect_grid(arr)
            if not gd.get("ok"):
                arr = screenshot(dest)
                gd = detect_grid(arr)
            if not gd.get("ok"):
                # 最后一道：盘中棋盘位置不会变，缓存几何与底色框吻合就直接沿用
                # （棋子密到把网格线全遮住时，测线本来就测不出来）
                if bbox_matches_grid(cached, beige_bbox(arr)):
                    self.log("测线失败，但几何缓存与底色框吻合，沿用缓存几何")
                    self.grid = cached
                    frame = Frame(arr)
                    board, feats = read_board(frame, self.grid)
                    self.last_frame = frame
                    return board, feats
                raise RuntimeError("测线失败：当前画面不是棋盘（可能停在菜单/弹窗/广告页）")
            self.grid = gd
            self.log("测线完成 x0=%.1f x8=%.1f y0=%.1f y8=%.1f step=%.2f/%.2f 底色框=%s"
                     % (self.grid["x"][0], self.grid["x"][8], self.grid["y"][0],
                        self.grid["y"][8], self.grid["step_x"], self.grid["step_y"], bb))
        frame = Frame(arr)
        board, feats = read_board(frame, self.grid)
        self.last_frame = frame
        return board, feats

    def our_turn(self, board):
        n_b, n_w = count_stones(board)
        if self.color == "B":
            return n_b == n_w
        return n_b == n_w + 1

    # ---- 落子 + 校验：两步式 —— 点交叉点「选中」（浮起气泡+红圈），再点气泡「确认」
    # 注意气泡浮在目标点上方 104px；落在靠上边缘（第 1/2 行）时气泡会露到棋盘外，
    # 位置模型会失效，因此这里用「就近找气泡 + 多种确认手势兜底」
    MARK_DY = 104

    def _find_marker(self, x, y):
        """在 (x,y-104) 或 (x,y+104) 附近找确认气泡；返回 (cx,cy) 或 None"""
        if not self.grid:
            return None, 0
        try:
            frame = Frame(screenshot())
        except Exception:
            return None, 0
        bubbles = find_bubbles(frame, self.grid)
        if not bubbles:
            return None, 0
        best, bestd = None, 1e9
        for bx, by in bubbles:
            for ty in (y - self.MARK_DY, y + self.MARK_DY):
                d = ((bx - x) ** 2 + (by - ty) ** 2) ** 0.5
                if d < bestd:
                    best, bestd = (bx, by), d
        return best, bestd

    def place(self, xi, yi, label):
        x, y = pixel_of(self.grid, xi, yi)

        # 先查这个点现在的真实状态：上一轮的“落子失败”可能其实已经落上了
        try:
            board_now, _ = self.capture()
        except RuntimeError as e:
            self.log("  落子前读盘失败，本手跳过：%s" % e)
            self.grid = None
            return False
        cur = board_now[yi][xi]
        if cur == self.color:
            self.log("  目标点 %s 已有我方棋子（上轮其实已落上），直接记账" % label)
            return True
        if cur not in (".", "?"):
            self.log("  目标点 %s 已被对方占据，本手作废（盘面识别滞后，重新读盘再算）" % label)
            return False

        for attempt in range(1, 4):
            # 第一步：点目标交叉点选中该点
            # 必须用「纯 tap」；实测 tap 前附加的关广告点击会让这次落子点击失效
            press(x, y)
            time.sleep(0.5)

            # 第二步：确认。按优先级依次尝试，命中即返回
            conf, dist = self._find_marker(x, y)
            strategies = []
            if conf is not None and dist < 150:
                strategies.append(("点气泡", lambda c=conf: press(c[0], c[1])))
                strategies.append(("拖气泡入红圈", lambda c=conf: swipe(c[0], c[1], x, y)))
            # 兜底：直接按固定偏移拖（气泡没被识到也能落）
            strategies.append(("上偏移拖", lambda: swipe(x, y - self.MARK_DY, x, y)))
            strategies.append(("下偏移拖", lambda: swipe(x, y + self.MARK_DY, x, y)))
            strategies.append(("再点一次", lambda: press(x, y)))

            self.log("  第 %d 次尝试：气泡=%s 距离=%.0f" %
                     (attempt, "无" if conf is None else "(%d,%d)" % conf, dist))
            for sname, fn in strategies:
                fn()
                if self.wait_stone(xi, yi, timeout=2.2):
                    self.log("  ✓ 落子确认（方式：%s）" % sname)
                    return True
                self.log("    %s 未生效" % sname)
                time.sleep(0.3)
        # 3 次都没确认：最后用一整帧复查一次，仍不算成功就重新测线（防几何漂移）
        try:
            board_end, _ = self.capture()
        except RuntimeError:
            board_end = None
        if board_end is not None and board_end[yi][xi] == self.color:
            self.log("  复查：%s 其实已落上，按成功处理" % label)
            return True
        self.grid = None
        return False

    def wait_stone(self, xi, yi, timeout=3.0, want=None):
        want = want or self.color
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.5)
            try:
                board, _ = self.capture()
            except RuntimeError:
                continue
            if board[yi][xi] == want:
                return True
        return False

    def wait_opponent(self, board, timeout=15.0):
        """等对方落子：以“棋子总数变化”为信号（避免气泡出现/消失造成误判）
        返回新盘面；若中途画面变成弹窗则返回 'dialog'；真超时返回 None"""
        base_n = sum(count_stones(board))
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(self.poll)
            try:
                nb, _ = self.capture()
            except RuntimeError:
                continue
            if self.last_frame is not None and \
                    (dialog_end(self.last_frame.arr) or dialog_first_move(self.last_frame.arr)
                     or dialog_again(self.last_frame.arr)):
                return "dialog"
            if sum(count_stones(nb)) > base_n:
                return nb
        return None

    # ---- 一回合
    def my_move(self, board, feats):
        moves = []
        for yi in range(15):
            for xi in range(15):
                c = board[yi][xi]
                if c in ("B", "W"):
                    moves.append((label_of(xi, yi), c))
        res = self.engine.analyze(moves, engine_color=self.color, time_ms=self.time_ms)
        if not res.get("ok"):
            self.log("引擎异常: %s" % res.get("error"))
            return None
        xi, yi = res["xy"]
        label = res["move"]
        self.log("引擎落点 %s (x=%d,y=%d) eval=%s depth=%s %sms"
                 % (label, xi, yi, res.get("eval"), res.get("depth"), res.get("ms")))
        if self.dry_run:
            return label, (xi, yi)
        ok = self.place(xi, yi, label)
        if not ok:
            try:
                nb, _ = self.capture()
                n_b, n_w = count_stones(nb)
                self.log("落子失败：盘面 B=%d W=%d（可能被气泡/广告接管）" % (n_b, n_w))
            except RuntimeError:
                self.log("落子失败（且读盘也失败，画面可能不是棋盘）")
            return None
        self.log("已落子 %s" % label)
        return label, (xi, yi)

    def run(self):
        self.log("=== 启动 color=%s time=%dms threads=%d rule=%d dry_run=%s ==="
                 % (self.color, self.time_ms, self.threads, self.rule, self.dry_run))
        endless = self.max_moves <= 0
        self.log("模式：%s" % ("无限对局（一直下，Ctrl+C 停）" if endless
                           else "最多 %d 手后停止" % self.max_moves))
        # 启动前先把画面弄回棋盘态（弹窗/菜单都处理掉）；无限模式下多给几次机会
        tries = 10 if endless else 1
        arr_ok = None
        for k in range(tries):
            arr_ok = self.ensure_board()
            if arr_ok is not None:
                break
            if endless and k < tries - 1:
                self.log("还回不到棋盘，8s 后重试（%d/%d）" % (k + 1, tries))
                time.sleep(8)
        if arr_ok is None:
            self.log("无法进入棋盘，退出")
            return 2
        try:
            board, _ = self.capture()
        except RuntimeError as e:
            self.log("启动读盘失败：%s" % e)
            return 2
        n_b, n_w = count_stones(board)
        self.log("起始盘面 B=%d W=%d" % (n_b, n_w))
        self.log("=== 盘面 ===\n" + board_str(board))
        mine = self.our_turn(board)
        self.log("起始轮次判定：%s方该走" % ("我" if mine else "对"))

        our_moves = 0
        while True if endless else our_moves < self.max_moves:
            # 0) 保证画面处在棋盘态（弹窗自动处理、掉到菜单就重新开局）
            ep0 = self.epoch
            if self.ensure_board() is None:
                if endless:
                    self.log("暂时回不到棋盘（可能在转场/广告/卡死），6s 后再试")
                    time.sleep(6)
                    continue
                self.log("无法回到棋盘，退出")
                return 2

            # 0.5) 这一帧之间可能已经在 ensure_board 里换过局（点掉了「再来一局」），
            #      局代际一变就必须把旧盘面/轮次/手数全部作废，否则会拿上一局的子误判五连
            if self.epoch != ep0:
                self.log("检测到已换局（局代际 %d→%d），旧盘面作废，重新判轮次"
                         % (ep0, self.epoch))
                board = None
                mine = None
                our_moves = 0
                continue
            try:
                arr0 = screenshot()
            except Exception as e:
                self.log("截屏失败，重试：%r" % (e,))
                time.sleep(1.5)
                continue
            if self.handle_dialogs(arr0):
                self.grid = None
                mine = None            # 换局了，轮次必须重判
                board = None           # 旧盘面作废，别拿上一局的子去判五连
                our_moves = 0          # 手数按局清零
                continue

            # 1) 轮次未知（刚换局/刚重启）→ 重读一帧判断该谁走
            if mine is None:
                self.grid = None
                try:
                    board, _ = self.capture()
                except RuntimeError:
                    time.sleep(1.0)
                    continue
                if self.last_frame is not None and dialog_end(self.last_frame.arr):
                    self.log("画面是结算弹窗，交给下一轮弹窗处理")
                    time.sleep(0.8)
                    continue
                n_b, n_w = count_stones(board)
                if n_b + n_w == 0:
                    mine = (self.color == "B")   # 空盘时只有执黑先下
                    self.games += 1
                    self.idle_timeouts = 0
                    our_moves = 0                # 新局手数从 0 开始
                    self.log("=== 第 %d 局开始 === 空盘，我执%s，%s先下"
                             % (self.games, self.color, "我" if mine else "对"))
                else:
                    mine = self.our_turn(board)
                    self.log("轮次判定：B=%d W=%d，%s方该走" % (n_b, n_w, "我" if mine else "对"))
                continue

            # 1.5) 盘面已被作废（刚换过局）→ 回头重判轮次，别拿 None 去读
            if board is None:
                mine = None
                continue

            # 2) 等对方落子
            if not mine:
                self.log("等待对方落子…")
                nb = self.wait_opponent(board, timeout=15.0)
                if nb == "dialog":
                    self.log("等待中出现弹窗，交给弹窗处理")
                    mine = None
                    continue
                if nb is None:
                    # 别急着退出：AI 可能只是慢，或这一帧识别没刷出来，再核一次
                    nchk = None
                    try:
                        nchk, _ = self.capture()
                    except RuntimeError:
                        pass
                    if nchk is not None and sum(count_stones(nchk)) != sum(count_stones(board)):
                        board = nchk
                        mine = True
                        self.log("超时复查：盘面已变化 B=%d W=%d" % count_stones(board))
                        continue
                    self.log("等待超时：连续 15s 无变化（对局可能已结束或卡在弹窗）")
                    try:
                        self.capture("stuck.png")
                    except RuntimeError:
                        pass
                    if endless:
                        self.idle_timeouts += 1
                        if self.idle_timeouts >= 3:
                            self.log("连续 %d 次等待无进展，强制重测几何 + 重判轮次"
                                     % self.idle_timeouts)
                            self.grid = None
                            self.idle_timeouts = 0
                        mine = None
                        time.sleep(2.0)
                        continue
                    return 3
                board = nb
                winner = find_winner(board)
                if winner:
                    self.log("检测到五连：%s 获胜，等结算弹窗后自动续局"
                             % ("黑" if winner == "B" else "白"))
                    try:
                        self.capture("win_%03d_%s.png" % (our_moves, winner))
                    except RuntimeError:
                        pass
                    time.sleep(1.0)
                    mine = None
                    continue
                mine = True
                self.log("对方已落子，盘面 B=%d W=%d" % count_stones(board))
                continue

            # 3) 我方落子：先用最新的盘面复查有没有已经结束
            winner = find_winner(board)
            if winner:
                self.log("检测到五连：%s 获胜，等结算弹窗后自动续局"
                         % ("黑" if winner == "B" else "白"))
                try:
                    self.capture("win_%03d_%s.png" % (our_moves, winner))
                except RuntimeError:
                    pass
                time.sleep(1.0)
                mine = None
                continue
            our_moves += 1
            self.log("--- 第 %d 手（我执%s）B=%d W=%d ---"
                     % (our_moves, self.color, count_stones(board)[0], count_stones(board)[1]))
            if our_moves <= 2:
                try:
                    self.capture("turn%02d_before.png" % our_moves)
                except RuntimeError:
                    pass
            # 算点前必须重取一帧：用上一帧的陈旧盘面会把已经被占的点再算一遍（正是 F2 反复失败的原因）
            try:
                board, _ = self.capture()
            except RuntimeError:
                time.sleep(1.0)
                continue
            if self.last_frame is not None and dialog_end(self.last_frame.arr):
                self.log("画面变成结算弹窗，交给下一轮弹窗处理")
                continue
            got = None
            try:
                got = self.my_move(board, None)
            except Exception as e:
                self.log("落子过程异常（已捕获，继续）：%r" % (e,))
            if not got:
                self.fail_streak += 1
                our_moves -= 1          # 这一手没落下，不计数，拿新盘面重算
                time.sleep(0.8)
                try:
                    self.capture("turn%02d_fail.png" % our_moves)
                except RuntimeError:
                    pass
                if self.fail_streak >= 5:
                    self.log("连续 %d 手没落下，重测几何后继续" % self.fail_streak)
                    self.grid = None
                    self.fail_streak = 0
                    time.sleep(1.5)
                continue
            self.fail_streak = 0
            if self.dry_run:
                return 0
            # 我方落子后的快照：若 AI 已在截图窗口内回了一手，这里就能看到，直接判定我方可走
            expected = sum(count_stones(board)) + 1
            try:
                board, _ = self.capture()
            except RuntimeError:
                mine = None
                continue
            if sum(count_stones(board)) > expected:
                self.log("对方落子很快，已在同一帧内看到（不额外等待）")
                mine = True
            else:
                mine = False
        if endless:
            self.log("对局结束（无限模式被中止），共进行 %d 局" % self.games)
        else:
            self.log("达到最大手数 %d，停止（共 %d 局）" % (self.max_moves, self.games))
        return 0


def watch_dialogs(color="B", interval=1.2, log_path=None):
    """专职盯弹窗：只要出现结算弹窗（含「保存对局」变灰、只剩绿色按钮的版式）就点「再来一局」，
    出现「请选择先后手」就按 color 选。不下棋，适合人自己下、只让程序负责续局。"""
    print("开始监视弹窗（Ctrl+C 退出）…")
    n = 0
    last_key = None
    repeat = 0
    while True:
        try:
            arr = screenshot()
        except Exception as e:
            print("截图失败：%r" % (e,))
            time.sleep(2.0)
            continue

        end_btn = dialog_end(arr)
        first = dialog_first_move(arr)
        again = dialog_again(arr)

        if end_btn:
            btn, act = end_btn[1], "结算弹窗→再来一局"
        elif first:
            btn = first[0] if color == "B" else first[1]
            act = "请选择先后手→%s" % ("先手" if color == "B" else "后手")
        elif again:
            btn, act = (again[0], again[1]), "结算行绿色按钮→再来一局"
        else:
            last_key, repeat = None, 0
            time.sleep(interval)
            continue

        key = (btn[0] // 15, btn[1] // 15)
        if key == last_key:
            repeat += 1
        else:
            last_key, repeat = key, 1
        if repeat > 5:
            print("同一位置连续点击仍无变化，暂停 5 秒（画面可能卡死）")
            time.sleep(5.0)
            repeat = 0
            last_key = None
            continue

        press(btn[0], btn[1])
        n += 1
        msg = "[%s] 第 %d 次：%s (%d,%d)" % (time.strftime("%H:%M:%S"), n, act, btn[0], btn[1])
        print(msg)
        if log_path:
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(msg + "\n")
            except OSError:
                pass
        time.sleep(1.8)


def main(argv=None):
    ap = argparse.ArgumentParser(description="五子棋自动对弈（adb + Rapfi）")
    ap.add_argument("--color", default="B", choices=["B", "W"], help="我执黑/白")
    ap.add_argument("--time", type=int, default=2000, help="每步思考毫秒")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--rule", type=int, default=1, help="1=15路无禁手（对应本 App）")
    ap.add_argument("--dry-run", action="store_true", help="只算不落子")
    ap.add_argument("--probe-only", action="store_true", help="只测线+识别一次")
    ap.add_argument("--max-moves", type=int, default=400,
                    help="我方的最大手数；0 或负数 = 无限对局（一直下，一局结束自动点再来一局）")
    ap.add_argument("--log", default=r"C:\五子棋\对局日志.txt")
    ap.add_argument("--shot-dir", default=r"C:\五子棋\对局截图")
    ap.add_argument("--watch-again", action="store_true",
                    help="只盯弹窗：出现结算弹窗就点绿色「再来一局」，出现先后手弹窗按 --color 选")
    a = ap.parse_args(argv)

    if a.watch_again:
        return watch_dialogs(a.color, log_path=a.log)

    if a.probe_only:
        try:
            arr = screenshot()
        except Exception as e:
            print("截图失败：%r" % (e,))
            return 2
        gd = detect_grid(arr)
        print("grid:", {k: gd.get(k) for k in ("ok", "x", "y", "step_x", "step_y")})
        if gd["ok"]:
            b, _ = read_board(Frame(arr), gd)
            print(board_str(b))
            print("B=%d W=%d" % count_stones(b))
        return 0 if gd["ok"] else 2

    try:
        bot = Bot(a.color, a.time, a.threads, a.rule, a.dry_run, a.log, a.shot_dir, a.max_moves)
    except Exception as e:
        print("初始化失败（引擎或依赖异常）：%r" % (e,))
        return 1
    try:
        return bot.run()
    except KeyboardInterrupt:
        print("已手动中止")
        return 130
    except Exception as e:
        print("异常: %r" % (e,))
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
