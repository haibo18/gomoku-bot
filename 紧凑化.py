# -*- coding: utf-8 -*-
"""把 五子棋棋盘坐标.json 重排为紧凑格式：数组内联、每个点位一行，数值与结构不变。"""
import json

src = r"C:\五子棋\五子棋棋盘坐标.json"
dst = r"C:\五子棋\五子棋棋盘坐标.紧凑版.json"

with open(src, "r", encoding="utf-8") as f:
    d = json.load(f)

def inline(v):
    return json.dumps(v, ensure_ascii=False)

lines = []
lines.append("{")
lines.append('  "name": %s,' % inline(d["name"]))
lines.append('  "device": {')
for k in ("package", "activity", "reference_screenshot", "measure"):
    lines.append('    "%s": %s,' % (k, inline(d["device"][k])))
lines.append('    "screen": { "width": %d, "height": %d, "density": %d }'
             % (d["device"]["screen"]["width"], d["device"]["screen"]["height"],
                d["device"]["screen"]["density"]))
lines.append('  },')
lines.append('  "geometry": {')
lines.append('    "origin": %s,' % inline(d["geometry"]["origin"]))
lines.append('    "step": %s,' % inline(d["geometry"]["step"]))
lines.append('    "lines": %d, "gaps": %d,' % (d["geometry"]["lines"], d["geometry"]["gaps"]))
lines.append('    "span": %s,' % inline(d["geometry"]["span"]))
lines.append('    "note": %s' % inline(d["geometry"]["note"]))
lines.append('  },')
lines.append('  "axes": {')
lines.append('    "x": %s,' % inline(d["axes"]["x"]))
lines.append('    "y": %s,' % inline(d["axes"]["y"]))
lines.append('    "x_int": %s,' % inline(d["axes"]["x_int"]))
lines.append('    "y_int": %s' % inline(d["axes"]["y_int"]))
lines.append('  },')
lines.append('  "special": {')
lines.append('    "center": %s,' % inline(d["special"]["center"]))
lines.append('    "stars": %s' % inline(d["special"]["stars"]))
lines.append('  },')

lines.append('  "points": [')
pts = d["points"]
for i, p in enumerate(pts):
    sep = "," if i < len(pts) - 1 else ""
    lines.append("    " + inline(p) + sep)
lines.append('  ]')
lines.append("}")

text = "\n".join(lines) + "\n"

# 自校验：重新解析后除空白外必须与原文件完全相等
with open(dst, "w", encoding="utf-8") as f:
    f.write(text)
back = json.loads(text)
assert back == d, "内容不一致"
print("written:", dst)
print("chars:", len(text), "lines:", len(lines), "points:", len(pts))
