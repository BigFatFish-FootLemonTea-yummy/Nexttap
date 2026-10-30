"""无头 GUI 测试：把 messagebox 全部替换掉，避免模态弹窗阻塞。

只测逻辑，不测像素。跑法：python tools/_test_gui.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import annotate

# ---- 关键：把所有弹窗换成记录器，否则 showwarning 会卡死测试 ----
POPUPS: list[tuple[str, str]] = []


class FakeBox:
    @staticmethod
    def showwarning(title, msg, **kw):
        POPUPS.append(("warning", f"{title}: {msg}"))

    @staticmethod
    def showerror(title, msg, **kw):
        POPUPS.append(("error", f"{title}: {msg}"))

    @staticmethod
    def showinfo(title, msg, **kw):
        POPUPS.append(("info", f"{title}: {msg}"))


annotate.messagebox = FakeBox

# 用临时目录，不碰真实 gt.jsonl
TMP = Path(tempfile.mkdtemp())
annotate.GT_PATH = TMP / "gt.jsonl"

fails = 0


def check(name, cond, detail=""):
    global fails
    if cond:
        print(f"  PASS  {name}")
    else:
        fails += 1
        print(f"  FAIL  {name}\n        {detail}")


root = tk.Tk()
app = annotate.Annotator(root)
root.update()

print("=" * 62)
print("1. 初始状态")
print("=" * 62)
check("加载到图片", len(app.files) > 0, f"{len(app.files)} 张")
check("图片尺寸有效", app.img_w > 0 and app.img_h > 0, f"{app.img_w}x{app.img_h}")
check("缩放比例 < 1（长图必须缩小）", app.scale < 1.0, f"scale={app.scale}")
disp_w = int(app.img_w * app.scale)
disp_h = int(app.img_h * app.scale)
cw, ch = app.canvas_size()
check("整张图在画布内",
      app.offset[0] >= -1 and app.offset[1] >= -1
      and app.offset[0] + disp_w <= cw + 1 and app.offset[1] + disp_h <= ch + 1,
      f"disp={disp_w}x{disp_h} offset={app.offset} canvas={cw}x{ch}")
check("PhotoImage 已是缩放后尺寸",
      app.photo.width() == disp_w and app.photo.height() == disp_h,
      f"photo={app.photo.width()}x{app.photo.height()} 期望={disp_w}x{disp_h}")

print()
print("=" * 62)
print("2. 拖框与坐标换算")
print("=" * 62)

# 屏幕框要足够大，否则除以 scale 后仍小于 4px 会被判为无效框。
# 长图 scale≈0.28，100 屏幕像素 ≈ 355 原图像素，够用。
BIG = (250, 80, 450, 300)          # 正向
BIG_REV = (450, 300, 250, 80)      # 反向（从右下拖到左上）

app.pending = BIG
box = app.screen_to_image()
check("正向拖框有效", box is not None, str(box))

app.pending = BIG_REV
box_rev = app.screen_to_image()
check("反向拖框归一化一致", box == box_rev, f"{box} vs {box_rev}")

app.pending = (300, 100, 301, 101)
check("过小的框被拒绝", app.screen_to_image() is None, "应返回 None")

app.pending = (-9999, -9999, 9999, 9999)
clamped = app.screen_to_image()
check("越界框被裁剪到图片范围",
      clamped is not None and clamped[0] >= 0 and clamped[1] >= 0
      and clamped[2] <= app.img_w and clamped[3] <= app.img_h,
      str(clamped))

print()
print("=" * 62)
print("3. 保存（带任务链）")
print("=" * 62)
app.reset_pending()
app.pending = BIG
app.goal_var.set("点预约挂号")
app.task_var.set("hospital.book_appointment")
app.step_var.set("1")
app.save_case()

rows = [json.loads(l) for l in annotate.GT_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
check("写入 1 条", len(rows) == 1, str(rows))
if rows:
    r = rows[0]
    check("file 正确", r["file"].startswith("images/"), r.get("file"))
    check("goal 正确", r["goal"] == "点预约挂号", r.get("goal"))
    check("task_id 正确", r.get("task_id") == "hospital.book_appointment", str(r.get("task_id")))
    check("step 正确", r.get("step") == 1, str(r.get("step")))
    check("bbox 是 4 个整数", len(r["bbox"]) == 4 and all(isinstance(v, int) for v in r["bbox"]), str(r.get("bbox")))

check("step 自动递增到 2", app.step_var.get() == "2", app.step_var.get())

print()
print("=" * 62)
print("4. 缺 goal 时不写入")
print("=" * 62)
before = len(rows)
app.reset_pending()
app.pending = BIG
app.goal_var.set("")
app.save_case()
rows2 = [json.loads(l) for l in annotate.GT_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
check("缺 goal 被拦截", len(rows2) == before, f"{before} -> {len(rows2)}")
check("弹出了警告", any(k == "warning" for k, _ in POPUPS), str(POPUPS))

print()
print("=" * 62)
print("5. 缩放后坐标仍正确")
print("=" * 62)
app.zoom = 3.0
app.redraw_canvas()
app.pending = BIG
box_zoom = app.screen_to_image()
check("放大后框变小（原图像素更少）",
      box_zoom is not None and (box_zoom[2] - box_zoom[0]) < (box[2] - box[0]),
      f"zoom={box_zoom} normal={box}")

app.on_fit()
check("适应窗口后 zoom 归 1", app.zoom == 1.0, str(app.zoom))

print()
print("=" * 62)
print("6. 修改与删除")
print("=" * 62)
app.selected = 0
app.goal_var.set("点预约挂号（改）")
app.update_selected()
rows3 = [json.loads(l) for l in annotate.GT_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
check("修改生效", rows3 and rows3[0]["goal"] == "点预约挂号（改）", str(rows3[0]["goal"]) if rows3 else "EMPTY")

app.selected = 0
app.delete_selected()
rows4 = [l for l in annotate.GT_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
check("删除后文件为空", len(rows4) == 0, str(rows4))

print()
print("=" * 62)
print("7. 坏行保护")
print("=" * 62)
annotate.GT_PATH.write_text('{"file": "x", "goal": "坏行"\n', encoding="utf-8")
try:
    annotate.load_gt()
    check("坏行应触发退出", False, "没有抛 SystemExit")
except SystemExit:
    check("坏行触发退出（不静默丢数据）", True)

root.destroy()

print()
print("=" * 62)
print("全部通过" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
