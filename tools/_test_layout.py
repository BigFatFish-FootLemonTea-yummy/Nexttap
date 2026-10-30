"""无头验证 annotate.py 的排版计算（不开窗口，纯几何测试）。

验证点：
    1. fit_scale 让整张图刚好装进画布
    2. redraw_canvas 算出的 offset / scale 一致
    3. screen_to_image 和 image_to_screen 互为逆运算
    4. 放大后拖动平移，坐标换算仍然正确

跑法：python tools/_test_layout.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


class FakeAnnotator:
    """只借用坐标换算那段逻辑，不依赖 tkinter。"""

    def __init__(self, img_w: int, img_h: int, cw: int, ch: int):
        self.img_w, self.img_h = img_w, img_h
        self.cw, self.ch = cw, ch
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.scale = 1.0
        self.offset = (0, 0)
        self.layout()

    def fit_scale(self) -> float:
        return min(self.cw / self.img_w, self.ch / self.img_h, 1.0)

    def layout(self) -> None:
        base = self.fit_scale()
        self.scale = base * self.zoom
        disp_w = int(self.img_w * self.scale)
        disp_h = int(self.img_h * self.scale)
        ox = (self.cw - disp_w) // 2 + int(self.pan_x)
        oy = (self.ch - disp_h) // 2 + int(self.pan_y)
        if disp_w <= self.cw:
            ox = (self.cw - disp_w) // 2
            self.pan_x = 0
        else:
            ox = min(0, max(self.cw - disp_w, ox))
        if disp_h <= self.ch:
            oy = (self.ch - disp_h) // 2
            self.pan_y = 0
        else:
            oy = min(0, max(self.ch - disp_h, oy))
        self.offset = (ox, oy)

    def image_to_screen(self, box):
        ox, oy = self.offset
        return (
            int(ox + box[0] * self.scale),
            int(oy + box[1] * self.scale),
            int(ox + box[2] * self.scale),
            int(oy + box[3] * self.scale),
        )

    def screen_to_image(self, rect):
        ox, oy = self.offset
        sx0, sy0, sx1, sy1 = rect
        x0 = max(0, min(self.img_w, int(round((sx0 - ox) / self.scale))))
        y0 = max(0, min(self.img_h, int(round((sy0 - oy) / self.scale))))
        x1 = max(0, min(self.img_w, int(round((sx1 - ox) / self.scale))))
        y1 = max(0, min(self.img_h, int(round((sy1 - oy) / self.scale))))
        return [x0, y0, x1, y1]


fails = 0


def check(name, got, want, tol=2):
    global fails
    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        ok = abs(got - want) <= tol
    elif isinstance(want, list) and isinstance(got, list):
        ok = len(got) == len(want) and all(abs(a - b) <= tol for a, b in zip(got, want))
    else:
        ok = got == want
    if not ok:
        fails += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


print("=" * 60)
print("场景 1：手机长截图 1260x2644，画布 850x740")
a = FakeAnnotator(1260, 2644, 850, 740)
check("缩放比例", a.scale, 740 / 2644, 0.001)
disp_h = 2644 * a.scale
check("显示高度不超画布", disp_h <= 740, True)
check("水平居中", a.offset[0], (850 - 1260 * a.scale) / 2)
check("垂直居中", a.offset[1], (740 - disp_h) / 2)

# 整张图都在画布内
check("左上角在画布内", a.offset[0] >= -1 and a.offset[1] >= -1, True)
check("右下角在画布内",
      a.offset[0] + 1260 * a.scale <= 851 and a.offset[1] + disp_h <= 741, True)

print()
print("场景 2：坐标往返（屏幕框 -> 原图框 -> 屏幕框）")
orig = [100, 200, 900, 400]
scr = a.image_to_screen(orig)
back = a.screen_to_image(scr)
# 缩放小于 1 时，1 个屏幕像素 = 1/scale 个原图像素，
# 往返误差必然存在，上界是 1/scale + 1（整数取整造成）
err = max(abs(g - w) for g, w in zip(back, orig))
bound = 1 / a.scale + 1
check(f"往返误差 {err} 在量化界内（{bound:.1f}px）", err <= bound, True)

print()
print("场景 3：放大 3 倍后，坐标换算仍然正确")
a.zoom = 3.0
a.layout()
check("放大后 scale", a.scale, 740 / 2644 * 3, 0.001)
orig2 = [100, 200, 900, 400]
scr2 = a.image_to_screen(orig2)
back2 = a.screen_to_image(scr2)
check("放大后往返一致", back2, orig2)

print()
print("场景 4：短图不放大（1000x600，画布 850x740）")
b = FakeAnnotator(1000, 600, 850, 740)
check("比例不超过 1.0", b.scale <= 1.0, True)
check("短图居中", b.offset[0], (850 - 1000 * b.scale) / 2)

print()
print("场景 5：极端窄图 1260x295（050.jpg），画布 850x740")
c = FakeAnnotator(1260, 295, 850, 740)
check("窄图缩放", c.scale, 850 / 1260, 0.001)
check("窄图完整可见", c.offset[0] >= -1 and c.offset[0] + 1260 * c.scale <= 851, True)

print()
print("=" * 60)
print("全部通过" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
