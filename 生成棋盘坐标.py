# -*- coding: utf-8 -*-
"""按实测像素生成五子棋 15x15 棋盘交叉点坐标（JSON）。

基准截图：1084x2412（density 480），对局界面 GameAct。
实测：从截图像素扫描得到 15 条竖线、15 条横线的位置（非等分理想模型），
末端累计偏差约 4px，因此按逐条线实测值输出，而不是"起点+固定步长"。
"""
import json

# 实测竖线（列 A..O，左->右）：由截图像素扫描得出，已剔除棋盘外框
X = [35.5, 108, 180, 252.5, 324.5, 397.5, 469.5, 542, 614,
     686.5, 758.5, 831, 903, 975.5, 1047.5]
# 实测横线（行 1..15，上->下）
Y = [635.5, 707.5, 779.5, 852, 924, 996.5, 1069, 1141.5,
     1213.5, 1286, 1358.5, 1430.5, 1502.5, 1575, 1647]

assert len(X) == 15 and len(Y) == 15

step_x = round((X[-1] - X[0]) / 14, 2)
step_y = round((Y[-1] - Y[0]) / 14, 2)


def r(v: float) -> int:
    """四舍五入到整数像素，tap 只接受整数"""
    return int(round(v))


def label(col: int, row: int) -> str:
    return f"{chr(ord('A') + col - 1)}{row}"


points = []
for row in range(1, 16):
    for col in range(1, 16):
        points.append({
            "col": col,
            "row": row,
            "label": label(col, row),
            "x": r(X[col - 1]),
            "y": r(Y[row - 1]),
            "xf": X[col - 1],
            "yf": Y[row - 1],
        })


def pt(col: int, row: int) -> dict:
    return {"col": col, "row": row, "label": label(col, row),
            "x": r(X[col - 1]), "y": r(Y[row - 1])}


data = {
    "name": "五子棋棋盘坐标（15×15 交叉点，像素实测）",
    "device": {
        "package": "com.wedobest.fivechess.huawei",
        "activity": "com.wedobest.fivechess.huawei.GameAct",
        "screen": {"width": 1084, "height": 2412, "density": 480},
        "reference_screenshot": "C:\\Users\\17601\\Desktop\\手机截图_20261116-203608.png",
        "measure": "截图像素扫描（亮度阈值145，逐列/逐行统计暗像素），已剔除棋盘外框",
    },
    "geometry": {
        "origin": {"x": 35.5, "y": 635.5},
        "step": {"x": step_x, "y": step_y},
        "lines": 15,
        "gaps": 14,
        "span": {"x": [X[0], X[-1]], "y": [Y[0], Y[-1]]},
        "note": "x/y 为取整后的 adb tap 像素坐标；xf/yf 为未取整实测值；列 A 在最左，行 1 在最上",
    },
    "axes": {
        "x": X,
        "y": Y,
        "x_int": [r(v) for v in X],
        "y_int": [r(v) for v in Y],
    },
    "special": {
        "center": pt(8, 8),
        "stars": [pt(4, 4), pt(12, 4), pt(4, 12), pt(12, 12)],
    },
    "points": points,
}

out = r"C:\五子棋\五子棋棋盘坐标.json"
with open(out, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("written:", out)
print("points:", len(points))
print("x span:", X[0], "->", X[-1], "step:", step_x)
print("y span:", Y[0], "->", Y[-1], "step:", step_y)
print("A1:", points[0]["x"], points[0]["y"], " H8:", pt(8, 8)["x"], pt(8, 8)["y"],
      " O15:", points[-1]["x"], points[-1]["y"])
