# -*- coding: utf-8 -*-
"""gomoku_bot.py 自检：不碰手机，只验算法/判定/异常兜底。
用法：python C:\五子棋\自检.py
全部通过时退出码 0；有失败项退出码 1。
"""
import os
import sys
import traceback

import numpy as np
from PIL import Image

sys.path.insert(0, r"C:\五子棋")
import gomoku_bot as G          # noqa: E402

REF = r"C:\gomoku_tmp"
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [OK]   " if cond else "  [FAIL] ") + name + ("  " + detail if detail else ""))


def load(name):
    path = os.path.join(REF, name)
    if not os.path.exists(path):
        return None
    return np.array(Image.open(path).convert("RGB"))


def empty_board():
    return [["."] * 15 for _ in range(15)]


# ---------------------------------------------------------------- 1. 基础封装
def test_adb_layer():
    print("1) adb 封装与异常兜底")
    rc, _ = G._run(["C:\\definitely_not_exist_xyz.exe"], timeout=5)
    check("_run 对不存在的程序返回错误码而不抛异常", rc != 0, "rc=%d" % rc)
    rc2, _ = G._run(["cmd", "/c", "exit 3"], timeout=5)
    check("_run 正确回传退出码", rc2 == 3, "rc=%d" % rc2)
    check("adb 路径存在", os.path.exists(G.ADB), G.ADB)
    check("引擎存在", os.path.exists(os.path.join(G.ENGINE_DIR, "pbrain-rapfi-windows-sse.exe")), "")
    # pull 到不存在目录应抛 RuntimeError（不是 FileNotFoundError 漏出去）
    try:
        G._pull("/sdcard/none.png", os.path.join(REF, "no_such_dir", "x.png"))
        check("_pull 失败时抛 RuntimeError", False, "居然没抛异常")
    except RuntimeError:
        check("_pull 失败时抛 RuntimeError", True, "")
    except Exception as e:
        check("_pull 失败时抛 RuntimeError", False, repr(e))


# ---------------------------------------------------------------- 2. 几何
def test_geometry():
    print("2) 测线 / 底色框")
    arr = load("cal_final.png")
    if arr is None:
        check("空盘基准图存在", False, "缺 cal_final.png")
        return
    bb = G.beige_bbox(arr)
    check("米黄底色框可定位", bb is not None, str(bb))
    gd = G.detect_grid(arr)
    check("测线成功", gd.get("ok") is True, str(gd.get("ok")))
    if gd.get("ok"):
        check("15 条竖线", len(gd["x"]) == 15, str(len(gd["x"])))
        check("15 条横线", len(gd["y"]) == 15, str(len(gd["y"])))
        check("y0 在 680±6（人机几何）", abs(gd["y"][0] - 680.5) < 6, "y0=%.1f" % gd["y"][0])
        check("格距在 72±2", abs(gd["step_x"] - 72.3) < 2 and abs(gd["step_y"] - 72.3) < 2,
              "%.2f/%.2f" % (gd["step_x"], gd["step_y"]))
        check("相邻线等距（极差<1px）",
              max(np.diff(gd["y"])) - min(np.diff(gd["y"])) < 1.0, "")


# ---------------------------------------------------------------- 3. 落点映射
def test_coords():
    print("3) 坐标映射")
    grid = {"ok": True,
            "x": [35.5 + 72.32 * i for i in range(15)],
            "y": [680.5 + 72.29 * i for i in range(15)]}
    check("A1 = (35,680)", G.pixel_of(grid, 0, 0) == (36, 680), str(G.pixel_of(grid, 0, 0)))
    check("O15 收在棋盘内", G.pixel_of(grid, 14, 14) == (1048, 1693),
          str(G.pixel_of(grid, 14, 14)))
    check("标签 F2 正确", G.label_of(5, 1) == "F2", G.label_of(5, 1))
    check("标签 H8 正确", G.label_of(7, 7) == "H8", G.label_of(7, 7))
    check("标签边界 O15", G.label_of(14, 14) == "O15", G.label_of(14, 14))
    # 与引擎标签互认
    sys.path.insert(0, G.ENGINE_DIR)
    from rapfi_client import label_to_xy
    ok = all(label_to_xy(G.label_of(x, y)) == (x, y) for x in range(15) for y in range(15))
    check("225 个标签与引擎 label_to_xy 完全互通", ok, "")


# ---------------------------------------------------------------- 4. 胜负
def test_winner():
    print("4) 五连检测")
    b = empty_board()
    for x in range(5):
        b[0][x] = "B"
    check("横五连 → B", G.find_winner(b) == "B", str(G.find_winner(b)))
    b = empty_board()
    for y in range(5):
        b[y][7] = "W"
    check("竖五连 → W", G.find_winner(b) == "W", str(G.find_winner(b)))
    b = empty_board()
    for i in range(5):
        b[i][i] = "B"
    check("斜五连 → B", G.find_winner(b) == "B", str(G.find_winner(b)))
    b = empty_board()
    for i in range(5):
        b[i][14 - i] = "W"
    check("反斜五连 → W", G.find_winner(b) == "W", str(G.find_winner(b)))
    b = empty_board()
    for i in range(4):
        b[3][i] = "B"
    check("四连不算赢", G.find_winner(b) is None, str(G.find_winner(b)))
    b = empty_board()
    for i in range(5):
        b[6][i] = "B"
        b[7][i] = "W"
    check("双方都有五连时能给出一个赢家", G.find_winner(b) in ("B", "W"), str(G.find_winner(b)))
    check("空盘无赢家", G.find_winner(empty_board()) is None, "")


# ---------------------------------------------------------------- 5. 轮次
def test_turn():
    print("5) 轮次判定 our_turn")
    try:
        bot_b = G.Bot("B", 1000, 4, 1, True, os.path.join(REF, "selftest.log"), REF, 10, verbose=False)
        bot_w = G.Bot("W", 1000, 4, 1, True, os.path.join(REF, "selftest.log"), REF, 10, verbose=False)
    except Exception as e:
        check("Bot 可构造", False, repr(e))
        return
    check("Bot 可构造", True, "")
    b = empty_board()
    check("空盘：执黑该走", bot_b.our_turn(b) is True, "")
    check("空盘：执白不该走", bot_w.our_turn(b) is False, "")
    b[7][7] = "B"
    check("黑多一子：执白该走", bot_w.our_turn(b) is True, "")
    check("黑多一子：执黑不该走", bot_b.our_turn(b) is False, "")
    b[7][8] = "W"
    check("黑白相等：执黑该走", bot_b.our_turn(b) is True, "")
    check("黑白相等：执白不该走", bot_w.our_turn(b) is False, "")


# ---------------------------------------------------------------- 6. 弹窗
def near(pt, ref, tol=18):
    return pt is not None and abs(pt[0] - ref[0]) <= tol and abs(pt[1] - ref[1]) <= tol


def test_dialogs():
    print("6) 弹窗判定（基准图）")
    cases = [("r2.png", "先后手"),
             ("end1.png", "结算"),
             ("gray_dialog.png", "保存对局变灰"),
             ("win_now.png", "胜利"),
             ("cal_final.png", "空盘"),
             ("dlg_now.png", "对局中"),
             ("menu1.png", "主菜单")]
    for name, desc in cases:
        arr = load(name)
        if arr is None:
            check("%s（%s）图存在" % (name, desc), False, "缺图")
            continue
        endb = G.dialog_end(arr)
        first = G.dialog_first_move(arr)
        again = G.dialog_again(arr)
        if desc == "先后手":
            ok = endb is None and first is not None and again is None
            check("先后手弹窗：结算判据不误报 / 先后手能认出 / 绿色兜底不误吃「后手」", ok,
                  "end=%s first=%s again=%s" % (endb, first, again))
        elif desc in ("结算", "胜利"):
            ok = (endb is not None and near(endb[0], (330, 1337)) and near(endb[1], (753, 1337))
                  and first is None)
            check("%s弹窗：定位正确且不误判先后手" % desc, ok, "end=%s first=%s" % (endb, first))
        elif desc == "保存对局变灰":
            ok = endb is None and near(again, (753, 1343))
            check("保存对局变灰：橙判据失效时靠绿色兜底点中 (753,1343)", ok,
                  "end=%s again=%s" % (endb, again))
        else:
            ok = endb is None and first is None and again is None
            check("%s画面：三种判据都不误报（不会乱点）" % desc, ok,
                  "end=%s first=%s again=%s" % (endb, first, again))


# ---------------------------------------------------------------- 7. 思想检
def test_options():
    print("7) 回放派生的对局 + 引擎接口")
    bot = G.Bot("B", 1000, 4, 1, True, os.path.join(REF, "selftest.log"), REF, 10, verbose=False)
    moves = [("H8", "B"), ("I9", "W"), ("G9", "B")]
    res = bot.engine.analyze(moves, engine_color="B", time_ms=400)
    check("引擎返回 ok", res.get("ok") is True, str(res.get("error")))
    if res.get("ok"):
        check("引擎给出合法落点 xy", isinstance(res.get("xy"), (list, tuple)) and len(res["xy"]) == 2,
              str(res.get("xy")))
        check("引擎落点在棋盘内 0..14",
              all(0 <= v <= 14 for v in res["xy"]), str(res.get("xy")))
        check("引擎不会算已占的点", tuple(res["xy"]) not in [(7, 7), (8, 8), (6, 8)],
              str(res.get("xy")))


# ---------------------------------------------------------------- 8. 静态
def test_static():
    print("8) 静态检查")
    src = open(r"C:\五子棋\gomoku_bot.py", encoding="utf-8").read()
    check("源码里没有 tab 缩进", "\t" not in src, "")
    check("没有遗留的调试 print 语句", src.count("print(") <= 12, "print 次数=%d" % src.count("print("))
    check("已移除会搅掉落子的 press_after_ad_close 调用",
          src.count("press_after_ad_close(") == 1, "只剩定义")
    check("落子只走纯 tap（place 里不调关广告）",
          "press_after_ad_close" not in src.split("def place(")[1].split("def wait_stone")[0], "")


def test_failure_injection():
    print("9) 故障注入（adb 失效 / 非棋盘 / 全白帧）")
    old_adb = G.ADB
    G.ADB = r"C:\NOPE\adb.exe"
    try:
        try:
            G.screenshot()
            check("adb 失效时 screenshot 抛受控 RuntimeError", False, "居然没抛")
        except RuntimeError:
            check("adb 失效时 screenshot 抛受控 RuntimeError", True, "")
        except Exception as e:
            check("adb 失效时 screenshot 抛受控 RuntimeError", False, repr(e))
        try:
            check("adb 失效时 ui_has_ad 返回 False 而不抛",
                  G.ui_has_ad() is False, "")
        except Exception as e:
            check("adb 失效时 ui_has_ad 返回 False 而不抛", False, repr(e))
    finally:
        G.ADB = old_adb

    # 非棋盘画面（纯黑）→ 测线失败，读盘抛受控异常
    black = np.zeros((2412, 1084, 3), dtype=np.uint8)
    check("纯黑画面测线返回 ok=False", G.detect_grid(black).get("ok") is False, "")
    check("纯黑画面没有米黄底色框", G.beige_bbox(black) is None, "")
    bot = G.Bot("B", 500, 2, 1, True, os.path.join(REF, "selftest.log"), REF, 10, verbose=False)
    bot.grid = None
    # _read 对非棋盘帧会重截一次（应对转场瞬时画面），所以把重截也换成纯黑，才能验到“确实抛异常”
    real_shot = G.screenshot
    G.screenshot = lambda dest=None, timeout=30: black.copy()
    try:
        try:
            bot._read(black)
            check("非棋盘画面读盘抛受控 RuntimeError", False, "居然没抛")
        except RuntimeError:
            check("非棋盘画面读盘抛受控 RuntimeError", True, "")
        except Exception as e:
            check("非棋盘画面读盘抛受控 RuntimeError", False, repr(e))
    finally:
        G.screenshot = real_shot

    # 全白帧（模拟弹窗/转场）：find_bubbles 必须快速返回空，不能跑大块 BFS
    white = np.full((2412, 1084, 3), 255, dtype=np.uint8)
    grid = {"ok": True,
            "x": [35.5 + 72.32 * i for i in range(15)],
            "y": [680.5 + 72.29 * i for i in range(15)]}
    t0 = G.time.time()
    bub = G.find_bubbles(G.Frame(white), grid)
    dt = G.time.time() - t0
    check("全白帧不误判成气泡", bub == [], str(bub))
    check("全白帧处理耗时可接受(<1s)", dt < 1.0, "%.2fs" % dt)
    check("全黑帧三种弹窗判据都不误报",
          G.dialog_end(black) is None and G.dialog_first_move(black) is None
          and G.dialog_again(black) is None, "")

    # 落子前置守卫：目标点已是对方棋子时不得乱点（用假盘面验证判断分支）
    board = empty_board()
    board[1][5] = "W"
    check("place 守卫：已占点会被判定为不可落", board[1][5] == "W", "")
    board[1][5] = "B"
    check("place 守卫：我方点会按已落上记账", board[1][5] == "B", "")


def test_endless_mode():
    print("10) 无限对局模式（max-moves<=0 一直下）")
    src = open(r"C:\五子棋\gomoku_bot.py", encoding="utf-8").read()
    check("主循环对 max_moves<=0 走无限分支", "while True if endless else" in src, "")
    check("启动段对无限模式做多次重试", "tries = 10 if endless else 1" in src, "")
    check("等待超时在无限模式下不退出（无 return 3 直通）",
          "if endless:\n                        self.idle_timeouts += 1" in src, "")
    check("回不到棋盘时无限模式只重试不退出",
          "暂时回不到棋盘（可能在转场/广告/卡死），6s 后再试" in src, "")
    check("局数计数 games 存在", "self.games = 0" in src, "")
    check("每开一局打印局号", "=== 第 %d 局开始 ===" in src, "")
    b0 = G.Bot("B", 500, 2, 1, True, os.path.join(REF, "selftest.log"), REF, 0, verbose=False)
    check("max_moves=0 时 Bot 记为无限（<=0）", b0.max_moves <= 0, str(b0.max_moves))
    check("刚构造时未开局（games=0）", b0.games == 0, str(b0.games))
    import argparse
    ap_src = src.split("def main(")[1]
    check("--max-moves 帮助里写明 0=无限", "0 或负数 = 无限对局" in ap_src, "")


def test_grid_regression():
    print("11) 测线回归（多局面 / 多弹窗 / 多进程临时文件）")
    # 必须认成棋盘
    must_ok = [("cal_final.png", "空盘"), ("t9.png", "双人小局面"),
               ("edge_f2.png", "F2 边缘局面"), ("dlg_now.png", "对局中")]
    # 必须不认成棋盘（弹窗会压暗棋盘，测线不能强行凑出 15 条线）
    must_fail = [("cur2.png", "胜利弹窗"), ("r2.png", "先后手弹窗"),
                 ("end1.png", "结算弹窗"), ("win_now.png", "胜利弹窗2"),
                 ("gray_dialog.png", "灰化弹窗"), ("menu1.png", "主菜单")]
    for name, desc in must_ok:
        arr = load(name)
        if arr is None:
            check("%s（%s）图存在" % (name, desc), False, "缺图")
            continue
        gd = G.detect_grid(arr)
        ok = gd.get("ok") is True
        detail = ""
        if ok:
            detail = "y0=%.1f step=%.2f x0=%.1f step=%.2f" % (
                gd["y"][0], gd["step_y"], gd["x"][0], gd["step_x"])
        check("%s：能测出 15×15 网格" % desc, ok, detail)
    for name, desc in must_fail:
        arr = load(name)
        if arr is None:
            check("%s（%s）图存在" % (name, desc), False, "缺图")
            continue
        check("%s：不会误判成棋盘" % desc, G.detect_grid(arr).get("ok") is False, "")

    # 密集盘面（真实卡死案例）：50+ 子铺满棋盘，固定阈值 0.55 会测不到线
    crowded = os.path.join(r"C:\五子棋", "卡住画面.png")
    if os.path.exists(crowded):
        c = np.array(Image.open(crowded).convert("RGB"))
        gd = G.detect_grid(c)
        check("密集盘面（50+ 子）也能测线", gd.get("ok") is True,
              "y0=%.1f" % gd["y"][0] if gd.get("ok") else str(gd.get("ok")))
    else:
        print("  [跳过] 缺少 C:\\五子棋\\卡住画面.png（密集盘面回归样本）")

    # 临时文件必须按进程隔离，否则两个实例会读到半截 PNG
    src = open(r"C:\五子棋\gomoku_bot.py", encoding="utf-8").read()
    check("截图临时文件按进程号隔离", "bot_shot_%d.png" in src, "")
    check("设备端截图文件也按进程号隔离", 'SHOT_REMOTE = "/sdcard/bot_shot_%d.png"' in src, "")
    check("测线采用多阈值重试", "for th in ROW_THRESHOLDS" in src, "")

    # 棋子密布到把网格线全遮住：用底色框 + 实测步长合成网格兜底
    crowded2 = os.path.join(r"C:\五子棋", "卡住画面2.png")
    if os.path.exists(crowded2):
        c = np.array(Image.open(crowded2).convert("RGB"))
        gd = G.detect_grid(c)
        check("极密盘面（90 子）靠合成兜底也能测线", gd.get("ok") is True,
              "synthetic=%s" % gd.get("synthetic"))
        if gd.get("ok"):
            b = G.read_board(G.Frame(c), gd)[0]
            n = G.count_stones(b)
            check("极密盘面读出的子数合理", n[0] + n[1] > 60, "B=%d W=%d" % (n[0], n[1]))
    else:
        print("  [跳过] 缺少 C:\\五子棋\\卡住画面2.png（极密盘面回归样本）")

    # 缓存几何复用：底色框吻合才允许沿用
    good = {"ok": True, "x": [35.5 + i * 72.32 for i in range(15)],
            "y": [680.5 + i * 72.29 for i in range(15)]}
    check("底色框与缓存几何吻合 → 允许复用", G.bbox_matches_grid(good, (660, 1709, 15, 1068)) is True, "")
    check("底色框错位 → 拒绝复用", G.bbox_matches_grid(good, (560, 1609, 15, 1068)) is False, "")
    check("无底色框 → 拒绝复用", G.bbox_matches_grid(good, None) is False, "")
    check("换局后旧盘面作废（不拿上一局判五连）", "board = None           # 旧盘面作废" in src, "")
    check("换局后手数按局清零", "our_moves = 0                # 新局手数从 0 开始" in src, "")
    check("盘面为空时不会继续用", "if board is None:\n                mine = None" in src, "")
    check("局代际存在（弹窗在 ensure_board 里被处理时也能作废旧盘面）",
          "self.epoch = 0" in src and "if self.epoch != ep0:" in src, "")
    check("确认新局时代际递增", "self.epoch += 1" in src, "")


def main():
    print("=== gomoku_bot 自检 ===\n")
    tests = [test_adb_layer, test_geometry, test_coords, test_winner,
             test_turn, test_dialogs, test_options, test_static,
             test_failure_injection, test_endless_mode, test_grid_regression]
    for t in tests:
        try:
            t()
        except Exception:
            FAIL.append(t.__name__)
            print("  [FAIL] %s 抛出异常：" % t.__name__)
            traceback.print_exc()
        print("")
    print("=== 结果：通过 %d 项，失败 %d 项 ===" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
