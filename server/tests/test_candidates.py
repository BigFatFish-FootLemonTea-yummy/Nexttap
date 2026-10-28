#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""纯逻辑单测：不依赖 OCR / LLM / FastAPI。

    python server/tests/test_candidates.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.vision.candidates import (  # noqa: E402
    Box,
    Candidate,
    from_norm,
    horizontal_band,
    merge_lines,
    pad_box,
    to_norm,
    vertical_band,
)

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


# 几何
b1 = Box(100, 100, 200, 200)
b2 = Box(150, 150, 250, 250)
check("iou", round(b1.iou(b2), 3), 0.143)
check("包含内部点", b1.contains_point(150, 150), True)
check("不包含外部点", b1.contains_point(50, 50), False)

# 同行合并
cands = [
    Candidate(0, "视频", Box(100, 900, 140, 930)),
    Candidate(1, "通话", Box(145, 900, 185, 930)),
    Candidate(2, "语音", Box(300, 900, 340, 930)),
]
merged = merge_lines(cands)
check("merge 数量", len(merged), 2)
check("merge 文本", [c.text for c in merged], ["视频通话", "语音"])
check("merge 重建索引", [c.idx for c in merged], [0, 1])

# 不同行不合并
rows = [
    Candidate(0, "确定", Box(100, 900, 200, 930)),
    Candidate(1, "确定", Box(100, 980, 200, 1010)),
]
check("跨行不合并", len(merge_lines(rows)), 2)

# 方位
check("顶部", vertical_band(50, 1000), "顶部")
check("底部", vertical_band(950, 1000), "底部")
check("居中", horizontal_band(500, 1000), "居中")

# padding：向外扩且不越界
padded = pad_box(Box(10, 10, 50, 50), 100, 100)
check("padding 外扩", padded.x0 < 10, True)
check(
    "padding 不越界",
    padded.x0 >= 0 and padded.y0 >= 0 and padded.x1 <= 100 and padded.y1 <= 100,
    True,
)

# 归一化往返
box = Box(100, 200, 300, 400)
norm = to_norm(box, 1000, 2000)
check("to_norm", norm, [100.0, 100.0, 300.0, 200.0])
check("from_norm 往返", from_norm(norm, 1000, 2000), [100, 200, 300, 400])

print()
print("全部通过" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
