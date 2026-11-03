# -*- coding: utf-8 -*-
"""只标注「评测失败」的那几张图 —— 复用项目 tools/annotate.py 的全部交互。

为什么单独做一个：
    tools/annotate.py 会加载 data/eval/images 下全部 112 张图，
    翻到你关心的那 20 多张要按很多次 N。这个脚本把图片列表
    限定成「失败用例涉及的图」，打开就在第一张。

用法：
    cd D:\\Nexttap
    python tools\\annotate_failed.py

操作（与 annotate.py 完全一致）：
    拖框                -> 画出待保存的框（青色）
    填「这一步要干嘛」   -> goal 文案
    选 task_id / 第几步
    S 或点「保存这个用例」-> 追加一条
    N / P               -> 下一张 / 上一张
    Delete              -> 删除选中的用例
    U                   -> 改选中用例的文案

标注完直接写回 data/eval/gt.jsonl，跑 `python tools/eval.py` 就能看到新分数。
"""
from __future__ import annotations

import json
import sys
import tkinter as tk
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import annotate as A  # noqa: E402

FAILED_LIST = ROOT / "data" / "eval" / "_failed_cases.json"


def load_failed_files() -> List[Path]:
    """从失败用例清单里取出涉及的图片（去重保序）。"""
    if not FAILED_LIST.exists():
        raise SystemExit(
            f"[FATAL] 找不到 {FAILED_LIST}\n"
            "请先运行 tools/collect_failed.py 生成失败清单。"
        )
    data = json.loads(FAILED_LIST.read_text(encoding="utf-8"))
    seen = set()
    files: List[Path] = []
    for c in data.get("icon", []) + data.get("text", []):
        rel = c["file"]
        if rel.startswith("images/"):
            rel = rel[len("images/"):]
        if rel in seen:
            continue
        seen.add(rel)
        p = A.IMAGES_DIR / rel
        if p.exists():
            files.append(p)
        else:
            print(f"[WARN] 图不存在，跳过：{p}")
    return files


class FailedAnnotator(A.Annotator):
    """只把图片列表换成「失败用例涉及的图」，其余行为完全继承。"""

    def __init__(self, root: tk.Tk, files: List[Path]) -> None:
        self._override_files = files
        super().__init__(root)

    # 复刻父类 __init__ 里那段文件扫描，但用我们自己的清单
    def _scan_files(self) -> List[Path]:  # pragma: no cover - 仅作说明
        return self._override_files


def main() -> None:
    failed = load_failed_files()
    if not failed:
        raise SystemExit("没有需要标注的图片")

    print(f"共 {len(failed)} 张图需要核对：")
    for p in failed:
        print(f"  {p.relative_to(A.IMAGES_DIR).as_posix()}")
    print("\n打开窗口后，直接在图上拖框补标即可。")

    root = tk.Tk()
    root.title(f"NextTap · 失败用例补标（{len(failed)} 张）")

    # 父类在 __init__ 里直接给 self.files 赋值，这里在实例化后立即替换
    app = A.Annotator(root)
    app.files = failed
    app.idx = 0
    if app.files:
        app.load_current()
    else:
        app.show_empty()

    root.mainloop()


if __name__ == "__main__":
    main()
