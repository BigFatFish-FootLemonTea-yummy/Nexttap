# -*- coding: utf-8 -*-
"""失败用例补标（傻瓜版）：画完框松手即保存，不用点任何按钮。

和 tools/annotate_failed.py 的区别：
    1. 预填的 task_id / step / goal 会**一直保留**，翻图自动更新，不会清空
    2. 拖框松手 -> 立即写盘，不需要再点「保存这个用例」
    3. 状态栏实时显示「已保存 N 个框」，Ctrl+Z 可撤销

用法：
    cd D:\\Nexttap
    python tools\\annotate_quick.py

操作：
    拖框        -> 松手自动保存
    N / P       -> 下一张 / 上一张
    Delete      -> 删除选中的框
    Ctrl+Z      -> 撤销上一次保存
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import annotate as A  # noqa: E402

FAILED_LIST = ROOT / "data" / "eval" / "_failed_cases.json"


def load_failed_cases() -> List[dict]:
    if not FAILED_LIST.exists():
        raise SystemExit(
            f"[FATAL] 找不到 {FAILED_LIST}\n请先运行 python tools\\collect_failed.py"
        )
    data = json.loads(FAILED_LIST.read_text(encoding="utf-8"))
    return data.get("icon", []) + data.get("text", [])


class QuickAnnotator(A.Annotator):
    """画完框自动保存。

    不重写 load_current / next_image / prev_image —— 父类用 root.after(30, ...)
    延迟调用，重写会被绕过。改用「定时同步」：每 200ms 检查一次当前图是否变了，
    变了就把预填信息写回输入框。
    """

    def __init__(self, root: tk.Tk, files: List[Path], meta: Dict[str, Tuple]) -> None:
        self._meta = meta
        self._last_rel = ""
        self._undo: List[Tuple[str, int]] = []
        super().__init__(root)

        # 父类扫描了全部 112 张图，换成失败清单
        self.files = files
        self.idx = 0
        self.root.title(f"失败用例补标 · 拖框即存 · 共 {len(files)} 张")
        self.load_current()

        # 覆盖父类的保存快捷键：S 也走自动保存
        self.root.bind("<Control-z>", self._do_undo)
        self.root.bind("<Control-Z>", self._do_undo)
        self.root.after(200, self._watch)

    # ---------- 定时同步当前图 ----------

    def _watch(self) -> None:
        try:
            rel = self.current_rel()
        except Exception:
            rel = ""
        if rel != self._last_rel:
            self._last_rel = rel
            self._apply_meta(rel)
            self._refresh_title(rel)
        self.root.after(200, self._watch)

    def _apply_meta(self, rel: str) -> None:
        # current_rel() 返回的是 "images/<相对路径>"，而 meta 的键是 "<相对路径>"
        key = rel[len("images/"):] if rel.startswith("images/") else rel
        info = self._meta.get(key)
        if not info:
            return
        tid, step, goal = info
        self.task_var.set(tid)
        self.step_var.set(str(step))
        self.goal_var.set(goal)

    def _refresh_title(self, rel: str) -> None:
        n = len(self.grouped.get(rel, []))
        self.root.title(
            f"失败用例补标 [{self.idx + 1}/{len(self.files)}] · "
            f"本图 {n} 个框 · 拖框即存"
        )
        self.status.set(
            f"拖框松手即保存 ｜ 本图已有 {n} 个框 ｜ N 下一张 / P 上一张 ｜ Ctrl+Z 撤销"
        )

    # ---------- 松手即保存 ----------

    def on_release(self, event: tk.Event) -> None:  # type: ignore[override]
        super().on_release(event)
        if self.pending:
            self._auto_save()

    def _auto_save(self) -> None:
        box = self.screen_to_image()
        if not box:
            return
        goal = self.goal_var.get().strip()
        if not goal:
            messagebox.showwarning("缺文案", "「这一步点哪」是空的，请先填。")
            return

        rel = self.current_rel()
        bucket = self.grouped.setdefault(rel, [])
        tid = self.task_var.get().strip()
        case: dict = {"file": rel, "goal": goal, "bbox": box}
        if tid:
            try:
                step = int(self.step_var.get())
            except (TypeError, ValueError):
                step = 1
            case["task_id"] = tid
            case["step"] = max(1, step)

        bucket.append(case)
        total = A.save_gt(self.grouped)
        self._undo.append((rel, len(bucket) - 1))
        self.reset_pending()
        self.selected = len(bucket) - 1
        self.refresh_list()
        self.redraw_boxes()

        self.status.set(
            f"✓ 已保存 {box} ｜ 本图 {len(bucket)} 个框 ｜ 合计 {total} 个用例"
        )
        self.root.title(
            f"失败用例补标 [{self.idx + 1}/{len(self.files)}] · "
            f"本图 {len(bucket)} 个框 ✓已保存"
        )

    # ---------- 撤销 ----------

    def _do_undo(self, _e=None) -> None:
        if not self._undo:
            self.status.set("没有可撤销的操作")
            return
        rel, idx = self._undo.pop()
        bucket = self.grouped.get(rel, [])
        if 0 <= idx < len(bucket):
            removed = bucket.pop(idx)
            A.save_gt(self.grouped)
            self.status.set(f"↶ 已撤销 {removed['bbox']}，本图剩 {len(bucket)} 个框")
            self.refresh_list()
            self.redraw_boxes()


def main() -> None:
    cases = load_failed_cases()
    if not cases:
        raise SystemExit("失败清单为空")

    meta: Dict[str, Tuple] = {}
    files: List[Path] = []
    for c in cases:
        rel = c["file"][len("images/"):] if c["file"].startswith("images/") else c["file"]
        if rel in meta:
            continue
        meta[rel] = (c["task_id"], c["step"], c["goal"])
        p = A.IMAGES_DIR / rel
        if p.exists():
            files.append(p)

    print(f"共 {len(files)} 张图，拖框后【松手即保存】。")
    for i, p in enumerate(files, 1):
        rel = p.relative_to(A.IMAGES_DIR).as_posix()
        tid, step, goal = meta[rel]
        print(f"  [{i:2}/{len(files)}] {tid:24} step{step}  {goal}")

    root = tk.Tk()
    QuickAnnotator(root, files, meta)
    root.mainloop()


if __name__ == "__main__":
    main()
