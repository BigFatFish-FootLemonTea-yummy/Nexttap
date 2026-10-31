#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""goal 泄漏审计：检查 goal 里是否直接出现了目标框上的文字。

为什么必须查：
    hit@1 的评测里，模型拿到的是 goal 文本 + OCR 候选列表。如果 goal 里
    原样写着目标按钮的文字（"点就诊人管理" 而框里就是"就诊人管理"），
    那模型只要做字符串匹配就能命中，hit@1 会虚高。这是交接文档 §六
    明确点名的坑，但一直没有自动检查。

判定口径（对应交接文档 §六）：
    - step=1  的 goal 是「用户口语」，**不允许**出现目标框文字
    - step>=2 的 goal 是「动作指令」，出现目标文字是设计如此，不算泄漏

做法：
    用 RapidOCR 跑一遍图，取落在真值框中心区域的文字，与 goal 做子串比对。
    纯本地、不调 LLM、不花钱。依赖 rapidocr-onnxruntime（没装就跳过并提示）。

用法：
    python tools/audit_goal_leak.py                # 全量审计
    python tools/audit_goal_leak.py --only-leak    # 只打印泄漏的
    python tools/audit_goal_leak.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
GT = EVAL_DIR / "gt.jsonl"

sys.path.insert(0, str(ROOT / "server"))


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def texts_in_box(cands: List[Any], bbox: List[int]) -> List[str]:
    """取中心点落在 bbox 内的 OCR 文字。"""
    x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
    out: List[str] = []
    for c in cands:
        cx = (c.box.x0 + c.box.x1) / 2
        cy = (c.box.y0 + c.box.y1) / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            t = str(c.text).strip()
            if t:
                out.append(t)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="审计 goal 是否泄漏目标框文字")
    ap.add_argument("--gt", type=Path, default=GT)
    ap.add_argument("--only-leak", action="store_true", help="只打印泄漏的用例")
    ap.add_argument("--json", type=Path, default=None, help="把明细写成 JSON")
    args = ap.parse_args(argv)

    try:
        from app.vision.ocr import run_ocr
    except Exception as exc:  # noqa: BLE001
        print(f"[SKIP] 没装 OCR 依赖，跳过审计：{exc}")
        print("       pip install rapidocr-onnxruntime")
        return 0

    rows = load_jsonl(args.gt)
    if not rows:
        print(f"[FATAL] 读不到 {args.gt}")
        return 2

    cache: Dict[str, List[Any]] = {}
    detail: List[Dict[str, Any]] = []
    for r in rows:
        f = str(r.get("file"))
        if f not in cache:
            p = EVAL_DIR / f
            if not p.exists():
                print(f"[WARN] 图片不存在，跳过：{p}")
                cache[f] = []
                continue
            cache[f] = run_ocr(str(p))
        inside = texts_in_box(cache[f], r.get("bbox") or [])
        goal = str(r.get("goal") or "").strip()
        leaks = [t for t in inside if len(t) >= 2 and t in goal]
        detail.append({
            "file": f,
            "task_id": str(r.get("task_id") or ""),
            "step": int(r.get("step") or 1),
            "goal": goal,
            "inside": inside,
            "leak": leaks,
        })

    step1 = [d for d in detail if d["step"] == 1]
    leak1 = [d for d in step1 if d["leak"]]
    leak2 = [d for d in detail if d["step"] != 1 and d["leak"]]
    empty = [d for d in detail if not d["inside"]]

    print("=" * 72)
    print("goal 泄漏审计（step=1 的 goal 不得含目标框文字）")
    print("=" * 72)
    print(f"  用例总数            : {len(detail)}")
    print(f"  step=1              : {len(step1)}")
    print(f"  step=1 且泄漏答案   : {len(leak1)}   ← 必须为 0")
    print(f"  step>=2 含目标文字  : {len(leak2)}   ← 动作指令，设计如此，不算问题")
    print(f"  框内无 OCR 文字     : {len(empty)}   ← 图标类目标，无法用文字核验")

    if leak1:
        print()
        print("-- step=1 泄漏明细（按 task_id 汇总，逐条改 goal 后重跑）--")
        by_tid: Dict[str, List[Dict[str, Any]]] = {}
        for d in leak1:
            by_tid.setdefault(d["task_id"] or "(无 task_id)", []).append(d)
        for tid in sorted(by_tid):
            print(f"  {tid}")
            for d in by_tid[tid]:
                print(f"    {d['file']:16} goal={d['goal']!r:24} 框内={d['leak']}")
        print()
        print("  改法：在 data/eval/goal_map.tsv 里加一行")
        print("        <task_id>\\t<旧goal>\\t<新的口语说法>\\tdraft")
        print("        然后 python tools/apply_goal_map.py --map data/eval/goal_map.tsv --dry-run")
    else:
        print()
        print("  PASS  step=1 没有泄漏答案的 goal")

    if args.json:
        args.json.write_text(json.dumps(detail, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n  明细已写入 {args.json}")

    if not args.only_leak and empty:
        print()
        print("-- 框内无 OCR 文字的用例（图标类，建议降级为区域描述）--")
        for d in empty:
            print(f"  {d['file']:16} {d['goal']!r}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
