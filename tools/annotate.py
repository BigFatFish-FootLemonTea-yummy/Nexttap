"""bbox 标注小工具（零依赖，只用 Python 自带的 tkinter）。

一张图可以有多个用例：同一张截图里框不同的入口，各自命名一个行为。
例如「医院首页」这一张图，可以同时标出：
    1. 怎么预约挂号        -> 框住「预约挂号」图标
    2. 怎么查缴费记录      -> 框住「门诊缴费」图标
    3. 怎么添加家人信息    -> 框住「添加就诊人」图标

同一个目标也可以有多个框：图上如果有两个入口都能到同一个功能
（例如顶部搜索框和右上角的放大镜），就把它们标成同一个 goal，
评测时命中任意一个都算对。列表里会显示「备选2」以示区分。

启动：
    python tools/annotate.py

操作：
    - 在图上按住左键拖一个矩形 => 待保存的框（青色）
    - 顶部输入框填「这个框对应的任务目标」
    - 按 S 或点「保存这个用例」=> 追加一条，图不动，可以接着框下一个
    - 框错了按 R 清空当前待保存的框
    - 左侧列表点选已标注的用例，可按 U 改文案、按 Delete 删除
    - 按 N 下一张图，按 P 上一张图

注意：
    - 认 .png / .jpg / .jpeg / .webp / .bmp / .gif（JPG 会自动转 PNG 缓存）
    - 同一张图可以保存任意多个用例
    - 图片按窗口大小等比缩放显示，保存的是【原图像素坐标】
"""

from __future__ import annotations

import json
import os
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
IMAGES_DIR = ROOT / "data" / "eval" / "images"
GT_PATH = ROOT / "data" / "eval" / "gt.jsonl"
PATHS_PATH = ROOT / "data" / "rag" / "paths.jsonl"
CACHE_DIR = ROOT / "data" / "eval" / ".cache_png"
# 只认后端 /api/screenshot 能接受的格式，避免标完了跑评测才发现上传失败
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

COLOR_CASE = "#22C55E"      # 已保存的框
COLOR_SELECTED = "#FACC15"  # 当前选中的框
COLOR_PENDING = "#00E5FF"   # 正在拖的新框


# --------------------------------------------------------------------------
# 读写 gt.jsonl：每行一个「步骤用例」，同一张图可以出现多行
#
# 两个层级：
#   任务层  task_id  —— 一条完整流程，例如 wechat.video_call_son
#   步骤层  goal     —— 这一步在这块屏幕上点哪，例如「点视频通话」
# 同一 task_id 下可以有 step=1/2/3 多行，分属不同图片。
# task_id 留空表示「孤立的单步用例」，只参与单步统计。
# --------------------------------------------------------------------------

def load_task_ids() -> List[str]:
    """从 paths.jsonl 读出所有 task_id，供下拉框选择（去重保序）。"""
    if not PATHS_PATH.exists():
        return []
    ids: List[str] = []
    seen = set()
    for line in PATHS_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            tid = str(json.loads(line).get("task_id", "")).strip()
        except json.JSONDecodeError:
            continue
        if tid and tid not in seen:
            seen.add(tid)
            ids.append(tid)
    return ids


def load_gt() -> Dict[str, List[dict]]:
    """返回 {相对路径: [步骤用例, ...]}，保持文件里的顺序。

    重要：解析失败的行绝不能被静默丢弃。
    因为 save_gt 会把内存里的内容整个覆写回文件，
    一旦某行被跳过，下次保存就会把它永久删掉 —— 等于白干。
    所以这里遇到坏行立刻抛错，并先备份原文件。
    """
    grouped: Dict[str, List[dict]] = {}
    if not GT_PATH.exists():
        return grouped

    bad_lines: List[str] = []
    for lineno, line in enumerate(GT_PATH.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            bad_lines.append(f"第 {lineno} 行：{exc}｜内容：{line[:100]}")
            continue
        rel = str(row.get("file", "")).replace("\\", "/")
        bbox = row.get("bbox")
        if not rel or not bbox or len(bbox) < 4:
            bad_lines.append(f"第 {lineno} 行：缺 file 或 bbox｜内容：{line[:100]}")
            continue
        case = {
            "file": rel,
            "goal": str(row.get("goal", "")),
            "bbox": list(bbox[:4]),
        }
        tid = str(row.get("task_id", "")).strip()
        if tid:
            case["task_id"] = tid
            try:
                case["step"] = max(1, int(row.get("step", 1) or 1))
            except (TypeError, ValueError):
                case["step"] = 1
        grouped.setdefault(rel, []).append(case)

    if bad_lines:
        # 先备份，避免用户手一抖把好数据也冲掉
        backup = GT_PATH.with_suffix(f".broken-{int(__import__('time').time())}.jsonl")
        try:
            backup.write_text(GT_PATH.read_text(encoding="utf-8-sig"), encoding="utf-8")
        except OSError:
            backup = None
        msg = (
            f"gt.jsonl 有 {len(bad_lines)} 行无法解析：\n\n"
            + "\n".join(bad_lines[:8])
            + (f"\n...另有 {len(bad_lines) - 8} 行" if len(bad_lines) > 8 else "")
            + (f"\n\n原文件已备份到：\n{backup}" if backup else "")
            + "\n\n为避免数据丢失，程序现在退出。请修好这几行再启动。"
        )
        raise SystemExit(msg)

    return grouped


def save_gt(grouped: Dict[str, List[dict]]) -> int:
    """原子写回 gt.jsonl，按文件名排序，同一图内保持标注顺序。返回总用例数。

    先写临时文件再 os.replace，防止写到一半断电导致文件截断。
    """
    GT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for rel in sorted(grouped):
        for case in grouped[rel]:
            lines.append(json.dumps(case, ensure_ascii=False))
    payload = "\n".join(lines) + ("\n" if lines else "")

    tmp = GT_PATH.with_suffix(".jsonl.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, GT_PATH)
    return len(lines)


# --------------------------------------------------------------------------
# 图片读取：PNG 原生 / Pillow / Windows 自带 System.Drawing 三级兜底
# --------------------------------------------------------------------------

def _convert_with_system_drawing(src: Path, dst: Path) -> bool:
    ps = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$i=[System.Drawing.Image]::FromFile('{src}'); "
        f"$i.Save('{dst}', [System.Drawing.Imaging.ImageFormat]::Png); "
        "$i.Dispose(); "
        'Write-Output "OK"'
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def load_photo(path: Path) -> "tk.PhotoImage":
    """三条路按顺序试，全都不需要联网。"""
    try:
        return tk.PhotoImage(file=str(path))
    except tk.TclError:
        pass

    try:
        from PIL import Image, ImageTk  # type: ignore

        return ImageTk.PhotoImage(Image.open(path).convert("RGB"))
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Pillow 读不了这张图：{path.name}（{exc}）") from exc

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{path.stem}_{int(path.stat().st_mtime)}.png"
    if not cached.exists():
        if not _convert_with_system_drawing(path.resolve(), cached.resolve()):
            raise RuntimeError(
                f"这张图打不开：{path.name}\n\n"
                f"当前格式是 {path.suffix or '未知'}。可用的办法：\n"
                f"  1) 用「画图」打开它，另存为 PNG，再放回 images/ 目录\n"
                f"  2) 如果是 HEIC（iPhone 直出），先导出成 JPG 再放进来\n"
                f"  3) 命令行执行：pip install pillow "
                f"-i https://pypi.tuna.tsinghua.edu.cn/simple"
            )
    try:
        return tk.PhotoImage(file=str(cached))
    except tk.TclError as exc:
        raise RuntimeError(f"转换后仍然打不开：{path.name}（{exc}）") from exc


def load_pil_image(path: Path) -> "Image.Image":
    """读成 PIL Image，方便按显示尺寸缩放。

    优先 Pillow；没装就借 Windows 自带的 System.Drawing 转成 PNG 再读。
    """
    try:
        from PIL import Image  # type: ignore

        return Image.open(path).convert("RGB")
    except ImportError:
        pass
    except Exception as exc:
        raise RuntimeError(f"Pillow 读不了这张图：{path.name}（{exc}）") from exc

    from PIL import Image  # 走到这里说明 Pillow 其实没装，下面会再失败一次

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = CACHE_DIR / f"{path.stem}_{int(path.stat().st_mtime)}.png"
    if not cached.exists():
        if not _convert_with_system_drawing(path.resolve(), cached.resolve()):
            raise RuntimeError(f"打不开这张图：{path.name}")
    return Image.open(cached).convert("RGB")


# --------------------------------------------------------------------------
# 主界面
# --------------------------------------------------------------------------

class Annotator:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("指一下 · bbox 标注")
        self.root.geometry("1180x860")

        self.files: List[Path] = sorted(
            p for p in IMAGES_DIR.iterdir() if p.suffix.lower() in SUFFIXES
        ) if IMAGES_DIR.exists() else []
        self.idx = 0
        self.grouped = load_gt()
        self.task_ids = load_task_ids()

        self.start: Optional[Tuple[int, int]] = None
        self.rect_id: Optional[int] = None
        self.pending: Optional[Tuple[int, int, int, int]] = None  # 屏幕坐标
        self.selected: Optional[int] = None  # 当前图里选中的用例下标
        self.scale = 1.0
        self.offset = (0, 0)
        self.img_w = 0
        self.img_h = 0

        self._build_ui()
        if self.files:
            self.load_current()
        else:
            self.show_empty()

    # ---------------- UI ----------------

    def _build_ui(self) -> None:
        # ---- 第一行：任务链（task_id + step） ----
        chain = tk.Frame(self.root)
        chain.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(6, 2))

        tk.Label(chain, text="所属任务链：").pack(side=tk.LEFT)
        self.task_var = tk.StringVar()
        self.task_combo = ttk.Combobox(
            chain, textvariable=self.task_var, width=30,
            values=self.task_ids, font=("Microsoft YaHei", 10),
        )
        self.task_combo.pack(side=tk.LEFT, padx=4)
        self.task_combo.bind("<<ComboboxSelected>>", lambda _e: self.on_task_change())
        self.task_combo.bind("<KeyRelease>", lambda _e: self.on_task_change())

        tk.Label(chain, text="第几步：").pack(side=tk.LEFT, padx=(12, 0))
        self.step_var = tk.StringVar(value="1")
        tk.Spinbox(chain, from_=1, to=10, width=4, textvariable=self.step_var,
                   font=("Microsoft YaHei", 10)).pack(side=tk.LEFT, padx=4)

        self.chain_hint = tk.Label(chain, text="（留空 = 孤立单步用例）",
                                   fg="#666", font=("Microsoft YaHei", 9))
        self.chain_hint.pack(side=tk.LEFT, padx=8)

        # ---- 第二行：这一步做什么 ----
        top = tk.Frame(self.root)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=2)

        tk.Label(top, text="这一步点哪：").pack(side=tk.LEFT)
        self.goal_var = tk.StringVar()
        self.goal_entry = tk.Entry(top, textvariable=self.goal_var, width=30,
                                   font=("Microsoft YaHei", 11))
        self.goal_entry.pack(side=tk.LEFT, padx=4)
        self.goal_entry.bind("<Return>", lambda _e: self.save_case())

        tk.Button(top, text="保存这个用例 (S)", command=self.save_case,
                  bg="#16A34A", fg="white").pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="改选中文案 (U)", command=self.update_selected).pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="删除选中 (Del)", command=self.delete_selected).pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="清空当前框 (R)", command=self.reset_pending).pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="适应窗口 (F)", command=self.on_fit).pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="上一张 (P)", command=self.prev_image).pack(side=tk.LEFT, padx=3)
        tk.Button(top, text="下一张 (N)", command=self.next_image).pack(side=tk.LEFT, padx=3)

        body = tk.Frame(self.root)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8)

        left = tk.Frame(body, width=280)
        left.pack(side=tk.LEFT, fill=tk.Y)
        left.pack_propagate(False)
        tk.Label(left, text="这张图的用例", anchor="w",
                 font=("Microsoft YaHei", 10, "bold")).pack(fill=tk.X, pady=(0, 4))
        self.listbox = tk.Listbox(left, exportselection=False,
                                  font=("Microsoft YaHei", 10))
        self.listbox.pack(fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_list_select)

        self.canvas = tk.Canvas(body, bg="#1e1e1e", cursor="crosshair")
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)          # 滚轮缩放
        self.canvas.bind("<ButtonPress-2>", self.on_middle_press)  # 中键平移
        self.canvas.bind("<B2-Motion>", self.on_middle_drag)
        self.canvas.bind("<Configure>", self.on_configure)       # 窗口变化重排

        self.status = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.status, anchor="w", fg="#333",
                 font=("Microsoft YaHei", 10)).pack(side=tk.BOTTOM, fill=tk.X,
                                                    padx=8, pady=4)

        for key, fn in (
            ("s", self.save_case), ("r", self.reset_pending), ("n", self.next_image),
            ("p", self.prev_image), ("u", self.update_selected), ("f", self.on_fit),
        ):
            self.root.bind(f"<KeyPress-{key}>", self._make_handler(fn))
        self.root.bind("<Delete>", self._make_handler(self.delete_selected))

    def _make_handler(self, fn):
        def handler(_event):
            if isinstance(self.root.focus_get(), (tk.Entry, tk.Text)):
                return
            fn()
        return handler

    # ---------------- 数据访问 ----------------

    def current_rel(self) -> str:
        return f"images/{self.files[self.idx].name}"

    def cases(self) -> List[dict]:
        return self.grouped.get(self.current_rel(), [])

    def total_cases(self) -> int:
        return sum(len(v) for v in self.grouped.values())

    # ---------------- 渲染 ----------------

    def show_empty(self) -> None:
        self.canvas.create_text(
            560, 380,
            text=(
                "没有找到图片。\n\n"
                f"请把截图放进：\n{IMAGES_DIR}\n\n"
                "然后重新运行 python tools/annotate.py"
            ),
            fill="#ddd", font=("Microsoft YaHei", 13), justify="center",
        )
        self.status.set("0 张图片")

    def load_current(self) -> None:
        if self.idx >= len(self.files):
            self.canvas.delete("all")
            self.canvas.create_text(
                560, 380, text="全部标完了 🎉", fill="#7CFC00",
                font=("Microsoft YaHei", 20),
            )
            self.status.set(
                f"共 {self.total_cases()} 个用例 / {len(self.grouped)} 张图，可关闭窗口"
            )
            self.listbox.delete(0, tk.END)
            return

        path = self.files[self.idx]
        self.root.title(
            f"指一下 · bbox 标注 — {path.name}  [{self.idx + 1}/{len(self.files)}]"
        )

        self.reset_pending()
        self.selected = None
        try:
            self._pil = load_pil_image(path)
        except RuntimeError as exc:
            messagebox.showerror("打不开这张图", str(exc))
            self.idx += 1
            self.root.after(50, self.load_current)
            return

        self.img_w, self.img_h = self._pil.width, self._pil.height
        self.zoom = 1.0          # 1.0 = 适应窗口；滚轮可放大
        self.pan_x = 0.0         # 平移偏移（屏幕像素）
        self.pan_y = 0.0
        self.redraw_canvas()
        self.redraw_boxes()
        self.refresh_list()
        self.goal_var.set("")
        self.task_var.set("")
        self.step_var.set("1")
        self.chain_hint.config(text="（留空 = 孤立单步用例）", fg="#666")
        self.update_status()

    def canvas_size(self) -> Tuple[int, int]:
        """取画布真实尺寸。窗口刚建好时 winfo_* 还是 1，必须 update_idletasks。"""
        self.root.update_idletasks()
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw <= 1 or ch <= 1:
            # 退路：按窗口尺寸估一个
            cw = max(self.root.winfo_width() - 300, 500)
            ch = max(self.root.winfo_height() - 120, 500)
        return cw, ch

    def fit_scale(self) -> float:
        """让整张图刚好放进画布的比例（不放大超过 1.0）。"""
        if not self.img_w or not self.img_h:
            return 1.0
        cw, ch = self.canvas_size()
        return min(cw / self.img_w, ch / self.img_h, 1.0)

    def redraw_canvas(self) -> None:
        """按当前 zoom / pan 重新缩放并摆放图片。

        关键：PhotoImage 不会被画布自动缩小，必须自己 resize 出对应尺寸的图，
        否则长截图只会在画布左上角露出一角。
        """
        if not getattr(self, "_pil", None) or not self.img_w:
            return
        from PIL import Image, ImageTk  # type: ignore

        cw, ch = self.canvas_size()
        base = self.fit_scale()
        self.scale = base * self.zoom
        disp_w = max(1, int(self.img_w * self.scale))
        disp_h = max(1, int(self.img_h * self.scale))

        # 默认居中；放大后允许平移，并把图夹在画布范围内，防止拖丢
        ox = (cw - disp_w) // 2 + int(self.pan_x)
        oy = (ch - disp_h) // 2 + int(self.pan_y)
        if disp_w <= cw:
            ox = (cw - disp_w) // 2
            self.pan_x = 0
        else:
            ox = min(0, max(cw - disp_w, ox))
        if disp_h <= ch:
            oy = (ch - disp_h) // 2
            self.pan_y = 0
        else:
            oy = min(0, max(ch - disp_h, oy))
        self.offset = (ox, oy)

        # 真正把图缩到显示尺寸（LANCZOS 缩字更清楚）
        if (disp_w, disp_h) != (self.img_w, self.img_h):
            shown = self._pil.resize((disp_w, disp_h), Image.LANCZOS)
        else:
            shown = self._pil
        self.photo = ImageTk.PhotoImage(shown)

        self.canvas.delete("photo")
        self.canvas.create_image(ox, oy, anchor=tk.NW, image=self.photo, tags="photo")

    def on_wheel(self, event: tk.Event) -> None:
        """滚轮缩放，以鼠标位置为中心。"""
        if not self.img_w:
            return
        cw, ch = self.canvas_size()
        old = self.scale
        step = 1.15 if event.delta > 0 else 1 / 1.15
        new_zoom = max(0.4, min(6.0, self.zoom * step))
        if abs(new_zoom - self.zoom) < 1e-6:
            return

        base = self.fit_scale()
        new_scale = base * new_zoom
        # 让鼠标下的那个点保持不动
        img_x = (event.x - self.offset[0]) / old
        img_y = (event.y - self.offset[1]) / old
        self.zoom = new_zoom
        self.scale = new_scale
        disp_w = int(self.img_w * new_scale)
        disp_h = int(self.img_h * new_scale)
        self.pan_x = event.x - img_x * new_scale - (cw - disp_w) // 2
        self.pan_y = event.y - img_y * new_scale - (ch - disp_h) // 2
        self.redraw_canvas()
        self.redraw_boxes()

    def on_fit(self, _event=None) -> None:
        """回到「适应窗口」。"""
        self.zoom = 1.0
        self.pan_x = self.pan_y = 0.0
        self.redraw_canvas()
        self.redraw_boxes()

    def on_configure(self, _event=None) -> None:
        """窗口大小变了，重新排版（防抖：200ms 内只做一次）。"""
        if getattr(self, "_resize_job", None):
            try:
                self.root.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self.root.after(120, self._do_refit)

    def _do_refit(self) -> None:
        self._resize_job = None
        if not self.img_w:
            return
        self.redraw_canvas()
        self.redraw_boxes()

    def redraw_boxes(self) -> None:
        self.canvas.delete("case")
        for i, case in enumerate(self.cases()):
            sx0, sy0, sx1, sy1 = self.image_to_screen(case["bbox"])
            color = COLOR_SELECTED if i == self.selected else COLOR_CASE
            width = 4 if i == self.selected else 2
            self.canvas.create_rectangle(sx0, sy0, sx1, sy1, outline=color,
                                         width=width, tags="case")
            self.canvas.create_text(sx0 + 5, sy0 + 4, anchor=tk.NW, text=str(i + 1),
                                    fill=color, font=("Microsoft YaHei", 12, "bold"),
                                    tags="case")

    def refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        seen: Dict[str, int] = {}
        for i, case in enumerate(self.cases()):
            goal = case["goal"]
            seen[goal] = seen.get(goal, 0) + 1
            mark = f" (备选{seen[goal]})" if seen[goal] > 1 else ""
            tid = case.get("task_id", "")
            tag = f"[{tid} 第{case.get('step', 1)}步] " if tid else "[单步] "
            self.listbox.insert(tk.END, f"{i + 1}. {tag}{goal}{mark}")
        if self.selected is not None and 0 <= self.selected < self.listbox.size():
            self.listbox.selection_set(self.selected)

    def update_status(self) -> None:
        if self.idx >= len(self.files):
            return
        path = self.files[self.idx]
        n = len(self.cases())
        hint = "拖框 → 填目标 → S 保存；同一张图可标多个" if n == 0 else \
               f"已标 {n} 个，可继续框新的入口"
        chains = len({c["task_id"] for c in self.cases() if c.get("task_id")})
        extra = f" | 本图涉及 {chains} 条链" if chains else ""
        self.status.set(
            f"{path.name} | {self.img_w}x{self.img_h} | {hint} | "
            f"合计 {self.total_cases()} 个用例 / {len(self.grouped)} 张图{extra}"
        )

    def on_task_change(self) -> None:
        """选了任务链时，自动提示这条链下一步该是第几步。"""
        tid = self.task_var.get().strip()
        if not tid:
            self.chain_hint.config(text="（留空 = 孤立单步用例）", fg="#666")
            return
        steps: Dict[int, str] = {}
        for bucket in self.grouped.values():
            for case in bucket:
                if case.get("task_id") == tid:
                    steps[case.get("step", 1)] = case["goal"]
        if steps:
            nxt = max(steps) + 1
            done = " / ".join(f"第{k}步:{v}" for k, v in sorted(steps.items()))
            self.chain_hint.config(text=f"已有 {done}，下一个填 {nxt}", fg="#0369A1")
            try:
                self.step_var.set(str(nxt))
            except Exception:
                pass
        else:
            self.chain_hint.config(text="这条链还没有任何步骤，从第 1 步开始", fg="#0369A1")
            try:
                self.step_var.set("1")
            except Exception:
                pass

    # ---------------- 坐标换算 ----------------

    def image_to_screen(self, box: List[float]) -> Tuple[int, int, int, int]:
        ox, oy = self.offset
        return (
            int(ox + float(box[0]) * self.scale),
            int(oy + float(box[1]) * self.scale),
            int(ox + float(box[2]) * self.scale),
            int(oy + float(box[3]) * self.scale),
        )

    def screen_to_image(self) -> Optional[List[int]]:
        """屏幕坐标 -> 原图像素坐标。

        这里再做一次 min/max 归一化：on_release 已经归一化过，
        但万一有别的入口直接塞 pending，也不能产出反向的框（x1<x0）。
        """
        if not self.pending:
            return None
        ox, oy = self.offset
        ax, ay, bx, by = self.pending
        sx0, sy0 = min(ax, bx), min(ay, by)
        sx1, sy1 = max(ax, bx), max(ay, by)
        x0 = max(0, min(self.img_w, int(round((sx0 - ox) / self.scale))))
        y0 = max(0, min(self.img_h, int(round((sy0 - oy) / self.scale))))
        x1 = max(0, min(self.img_w, int(round((sx1 - ox) / self.scale))))
        y1 = max(0, min(self.img_h, int(round((sy1 - oy) / self.scale))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return [x0, y0, x1, y1]

    # ---------------- 鼠标 ----------------

    def on_press(self, event: tk.Event) -> None:
        self.start = (event.x, event.y)
        self.canvas.delete("pending")
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline=COLOR_PENDING, width=3, tags="pending",
        )

    def on_drag(self, event: tk.Event) -> None:
        if self.start and self.rect_id:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1],
                               event.x, event.y)

    def on_middle_press(self, event: tk.Event) -> None:
        """中键拖动平移图片（放大后才有用）。"""
        self._pan_start = (event.x, event.y, self.pan_x, self.pan_y)
        self.canvas.config(cursor="fleur")

    def on_middle_drag(self, event: tk.Event) -> None:
        if not getattr(self, "_pan_start", None):
            return
        sx, sy, px, py = self._pan_start
        self.pan_x = px + (event.x - sx)
        self.pan_y = py + (event.y - sy)
        self.redraw_canvas()
        self.redraw_boxes()

    def on_release(self, event: tk.Event) -> None:
        if not self.start:
            return
        x0, y0 = self.start
        self.pending = (min(x0, event.x), min(y0, event.y),
                        max(x0, event.x), max(y0, event.y))
        self.start = None
        self.canvas.config(cursor="crosshair")
        self.goal_entry.focus_set()

    def reset_pending(self) -> None:
        self.canvas.delete("pending")
        self.rect_id = None
        self.pending = None

    # ---------------- 增删改 ----------------

    def on_list_select(self, _event: tk.Event) -> None:
        sel = self.listbox.curselection()
        if not sel:
            return
        self.selected = int(sel[0])
        case = self.cases()[self.selected]
        self.goal_var.set(case["goal"])
        self.task_var.set(case.get("task_id", ""))
        self.step_var.set(str(case.get("step", 1)))
        self.redraw_boxes()

    def save_case(self) -> None:
        if self.idx >= len(self.files):
            return
        box = self.screen_to_image()
        if not box:
            messagebox.showwarning("还没画框", "请先在图片上拖出一个矩形，再保存。")
            return
        goal = self.goal_var.get().strip()
        if not goal:
            messagebox.showwarning(
                "缺「这一步点哪」",
                "请填写这一步在这块屏幕上应该点哪个元素。\n"
                "注意是动作，不是任务：\n"
                "  ✓ 点视频通话\n"
                "  ✗ 给儿子打视频电话（那是任务名，不是这一步）",
            )
            return

        rel = self.current_rel()
        bucket = self.grouped.setdefault(rel, [])
        tid = self.task_var.get().strip()

        # 同名目标 = 同一功能的「另一个可接受入口」，允许追加。
        # 评测时命中任意一个都算对，避免「图上有两个入口都能到，却只认一个」的误判。
        same = [c for c in bucket if c["goal"] == goal and c.get("task_id", "") == tid]
        case: dict = {"file": rel, "goal": goal, "bbox": box}
        if tid:
            try:
                step = int(self.step_var.get())
            except (TypeError, ValueError):
                step = 1
            case["task_id"] = tid
            case["step"] = max(1, step)

        bucket.append(case)
        total = save_gt(self.grouped)
        self.reset_pending()
        self.selected = len(bucket) - 1
        self.refresh_list()
        self.redraw_boxes()
        self.goal_var.set("")
        self.update_status()

        where = f"[{tid} 第{case.get('step')}步] " if tid else "[单步] "
        if same:
            self.status.set(
                f"{where}「{goal}」现在有 {len(same) + 1} 个可接受框"
                f"（命中任意一个即算对）| 合计 {total} 个用例"
            )
        else:
            self.status.set(
                f"已保存 {where}「{goal}」-> bbox={box} | 这张图 {len(bucket)} 个用例，"
                f"合计 {total} 个"
            )
        # 同一条链的下一步：把 step 自增，方便连着标
        if tid:
            try:
                self.step_var.set(str(case.get("step", 1) + 1))
            except Exception:
                pass
            self.on_task_change()
        self.goal_entry.focus_set()

    def update_selected(self) -> None:
        if self.selected is None or self.idx >= len(self.files):
            messagebox.showinfo("没选中", "先在左侧列表里点选一个用例。")
            return
        goal = self.goal_var.get().strip()
        if not goal:
            messagebox.showwarning("缺「这一步点哪」", "输入框是空的。")
            return
        bucket = self.cases()
        case = bucket[self.selected]
        case["goal"] = goal
        tid = self.task_var.get().strip()
        if tid:
            case["task_id"] = tid
            try:
                case["step"] = max(1, int(self.step_var.get()))
            except (TypeError, ValueError):
                case["step"] = 1
        else:
            case.pop("task_id", None)
            case.pop("step", None)
        save_gt(self.grouped)
        self.refresh_list()
        self.redraw_boxes()
        self.update_status()
        self.status.set(f"已更新第 {self.selected + 1} 个用例：{goal}")

    def delete_selected(self) -> None:
        if self.selected is None or self.idx >= len(self.files):
            messagebox.showinfo("没选中", "先在左侧列表里点选一个用例。")
            return
        bucket = self.cases()
        removed = bucket.pop(self.selected)
        if not bucket:
            self.grouped.pop(self.current_rel(), None)
        save_gt(self.grouped)
        self.selected = None
        self.refresh_list()
        self.redraw_boxes()
        self.update_status()
        self.status.set(f"已删除「{removed['goal']}」")

    # ---------------- 翻页 ----------------

    def next_image(self) -> None:
        if self.idx < len(self.files):
            self.idx += 1
            self.root.after(30, self.load_current)

    def prev_image(self) -> None:
        if self.idx > 0:
            self.idx -= 1
            self.root.after(30, self.load_current)


def main() -> None:
    root = tk.Tk()
    Annotator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
