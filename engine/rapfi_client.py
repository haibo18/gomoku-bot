# -*- coding: utf-8 -*-
"""Rapfi 五子棋引擎命令行客户端（Piskvork / Gomocup 协议封装）

用途：给一个 15×15 盘面，取回引擎的最优落点（同时换算成手机截图上的像素坐标，
可直接喂给 adb tap）。

用法示例：
    python rapfi_client.py --demo
    python rapfi_client.py --moves "H8,I9,G7,H7" --engine-color W --time 3000
    python rapfi_client.py --moves "H8" --threads 16 --time 5000 --json

盘面简写：H8 表示第 8 列（H）、第 8 行（从上往下数）。
落子颜色默认黑白交替，首子为黑；用 --engine-color 指定引擎执哪一方。
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 实测像素坐标（截图 1084×2412，与 五子棋棋盘坐标.紧凑版.json 完全一致）----
PX_X = [36, 108, 180, 252, 324, 398, 470, 542, 614, 686, 758, 831, 903, 976, 1048]
PX_Y = [636, 708, 780, 852, 924, 996, 1069, 1142, 1214, 1286, 1358, 1430, 1502, 1575, 1647]

MOVE_RE = re.compile(r"^\d+,\d+$")
PV_TOKEN = re.compile(r"\b([A-O](?:1[0-5]|[1-9]))\b")


# ---------------------------------------------------------------- 坐标换算
def label_to_xy(label):
    """'H8' -> (7, 7)  0 基坐标，与引擎一致"""
    label = label.strip().upper()
    if not re.fullmatch(r"[A-O](?:1[0-5]|[1-9])", label):
        raise ValueError("非法点位: %r（应为 A1..O15）" % label)
    x = ord(label[0]) - ord("A")
    y = int(label[1:]) - 1
    return x, y


def xy_to_label(x, y):
    """(7, 7) -> 'H8'"""
    return "%s%d" % (chr(ord("A") + int(x)), int(y) + 1)


def xy_to_pixel(x, y):
    """0 基棋盘坐标 -> 手机截图上的 (像素x, 像素y)"""
    return PX_X[int(x)], PX_Y[int(y)]


# ---------------------------------------------------------------- 引擎封装
class RapfiEngine:
    def __init__(self, exe=None, board_size=15, rule=1, threads=8,
                 time_ms=3000, tt_kb=None):
        """
        exe      : 引擎路径，默认本目录下的 SSE 版（本机 CPU 无 AVX2，只能用 SSE）
        rule     : 0=无禁手任意盘 1=15路无禁手(Standard, 对应手机这款 App) 4=有禁手(Renju)
        threads  : 搜索线程数（本机 10 核 20 线程，实测 8 线程约 583K nps）
        time_ms  : 每步思考上限（毫秒）
        tt_kb    : 置换表大小（KB），None 则用 config.toml 的默认 32768
        """
        self.exe = exe or os.path.join(HERE, "pbrain-rapfi-windows-sse.exe")
        if not os.path.exists(self.exe):
            raise FileNotFoundError("找不到引擎: %s" % self.exe)
        self.board_size = board_size
        self.rule = rule
        self.threads = threads
        self.time_ms = time_ms
        self.tt_kb = tt_kb

    # -- 单次分析：起一个进程、灌盘面、取一步、退出 --
    def analyze(self, stones, engine_color="B", time_ms=None, timeout_s=None):
        """
        stones      : [(label, 'B'/'W'), ...] 或 [(x, y, 'B'/'W'), ...]
        engine_color: 引擎执黑 'B' 还是执白 'W'
        返回 dict：move / pixel / eval / depth / pv / nps / ms / raw
        """
        time_ms = time_ms or self.time_ms
        stone_map = {}
        for item in stones:
            if len(item) == 2:
                x, y = label_to_xy(item[0])
                color = item[1]
            else:
                x, y, color = item[0], item[1], item[2]
            color = str(color).upper()[0]
            if color not in ("B", "W"):
                raise ValueError("颜色只能是 B 或 W: %r" % (color,))
            stone_map[(int(x), int(y))] = color

        cmd = ["START %d" % self.board_size,
               "INFO RULE %d" % self.rule,
               "INFO THREAD_NUM %d" % self.threads,
               "INFO TIMEOUT_TURN %d" % int(time_ms)]
        if self.tt_kb:
            cmd.append("INFO MAX_MEMORY %d" % int(self.tt_kb))
        # 协议要点：BOARD 里 1 = 引擎自己一方，2 = 对手
        cmd.append("BOARD")
        for (x, y), color in sorted(stone_map.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            cmd.append("%d,%d,%d" % (x, y, 1 if color == engine_color else 2))
        cmd.append("DONE")

        proc = subprocess.Popen([self.exe], cwd=HERE,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        proc.stdin.write("\n".join(cmd) + "\n")
        proc.stdin.flush()

        started = time.time()
        limit = timeout_s or (time_ms / 1000.0 * 3 + 10)
        lines, move = [], None
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line:
                    lines.append(line)
                if MOVE_RE.match(line):
                    move = line
                    break
                if time.time() - started > limit:
                    break
        finally:
            try:
                proc.stdin.write("END\n")
                proc.stdin.flush()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        elapsed = time.time() - started
        if move is None:
            return {"ok": False, "error": "引擎没有返回落点", "ms": round(elapsed * 1000),
                    "raw": lines}
        x, y = [int(v) for v in move.split(",")]
        info = self._parse_info(lines)
        return {
            "ok": True,
            "move": xy_to_label(x, y),
            "xy": [x, y],
            "pixel": list(xy_to_pixel(x, y)),
            "eval": info.get("eval"),
            "depth": info.get("depth"),
            "nps": info.get("nps"),
            "pv": info.get("pv", []),
            "ms": info.get("time_ms", round(elapsed * 1000)),
            "raw": lines,
        }

    @staticmethod
    def _parse_info(lines):
        out = {}
        for line in lines:
            if not line.startswith("MESSAGE"):
                continue
            m = re.search(r"Depth (\d+)-(\d+)", line)
            if m:
                out["depth"] = "%s-%s" % (m.group(1), m.group(2))
            m = re.search(r"Eval ([+-]?[A-Z0-9]+)", line)
            if m:
                out["eval"] = m.group(1)
            m = re.search(r"Speed (\d+)(K?)", line)
            if m:
                out["nps"] = int(m.group(1)) * (1000 if m.group(2) == "K" else 1)
            m = re.search(r"Node (\d+)(K?)", line)
            if m:
                out["nodes"] = int(m.group(1)) * (1000 if m.group(2) == "K" else 1)
            m = re.search(r"Time (\d+)ms", line)
            if m:
                out["time_ms"] = int(m.group(1))
            if "|" in line:
                pv = PV_TOKEN.findall(line.split("|")[-1])
                # 末行主变化常被截断成 1 手，取最长的一条更可靠
                if len(pv) > len(out.get("pv", [])):
                    out["pv"] = pv
        return out


# ---------------------------------------------------------------- CLI
def parse_moves(text):
    """'H8,I9,G7' -> [(('H',8) 即 (7,7), 'B'), ...] 黑白交替，首子黑"""
    labels = [s for s in re.split(r"[,\s]+", text.strip()) if s]
    stones, n = [], 0
    for lb in labels:
        lb = lb.strip()
        if ":" in lb:                      # 支持 H8:B 这样显式指定颜色
            lbl, col = lb.split(":")
            stones.append((lbl, col.strip().upper()))
        else:
            stones.append((lb, "B" if n % 2 == 0 else "W"))
        n += 1
    return stones


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rapfi 五子棋引擎客户端（Piskvork 协议）")
    ap.add_argument("--moves", default="", help="盘面棋子，如 \"H8,I9,G7\"（黑白交替，首子黑）")
    ap.add_argument("--engine-color", default=None, choices=["B", "W"],
                    help="引擎执黑还是执白，默认按手数推断")
    ap.add_argument("--time", type=int, default=3000, help="每步思考毫秒，默认 3000")
    ap.add_argument("--threads", type=int, default=8, help="搜索线程，默认 8")
    ap.add_argument("--rule", type=int, default=1, help="0=无禁手 1=15路无禁手 4=有禁手")
    ap.add_argument("--exe", default=None, help="引擎路径")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--demo", action="store_true", help="跑内置示例（天元一子，引擎执黑接走）")
    a = ap.parse_args(argv)

    if a.demo:
        a.moves = "H8:B"
        a.engine_color = "W"
        a.time = 2000

    stones = parse_moves(a.moves) if a.moves else []
    engine_color = a.engine_color
    if engine_color is None:
        engine_color = "B" if not stones else ("W" if stones[0][1] == "B" else "B")

    eng = RapfiEngine(exe=a.exe, rule=a.rule, threads=a.threads, time_ms=a.time)
    res = eng.analyze(stones, engine_color=engine_color, time_ms=a.time)

    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("ok") else 1

    if not res.get("ok"):
        print("引擎未返回落点：%s" % res.get("error"))
        for line in res.get("raw", [])[-6:]:
            print("  " + line)
        return 1

    print("盘面     : %s（引擎执%s）" % (a.moves or "空盘", "黑" if engine_color == "B" else "白"))
    print("推荐落点 : %s  → 棋盘坐标 %s  → 手机像素 (%d, %d)"
          % (res["move"], tuple(res["xy"]), res["pixel"][0], res["pixel"][1]))
    print("评估/深度: eval=%s depth=%s %s nps" %
          (res["eval"], res["depth"], res["nps"]))
    print("耗时     : %s ms" % res["ms"])
    if res["pv"]:
        print("主变化   : %s" % " ".join(res["pv"][:12]))
    print("adb 命令 : adb shell input tap %d %d" % tuple(res["pixel"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
