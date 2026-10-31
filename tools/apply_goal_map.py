#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 gt 里 step=1 的 goal 换成用户口语。

背景：原 gt 的 goal 写成了"点预约挂号"这种动作指令，而真值框里就是"预约挂号"，
等于把答案直接告诉模型，hit@1 会虚高。本脚本按映射表把 step=1 的 goal 换成
不含答案文字的口语说法。

用法：
    python tools/apply_goal_map.py --map data/eval/goal_map.tsv --dry-run
    python tools/apply_goal_map.py --map data/eval/goal_map.tsv --write

安全措施：
    - 先写 .bak 备份（只备份一次，不覆盖旧备份）
    - 先写 .tmp 再 os.replace，避免写一半断电
    - --write 之前务必先跑 --dry-run 看报告
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Dict, List, Set, Tuple


def load_map(path: str) -> Dict[str, Tuple[str, str]]:
    """读到 {task_id: (旧goal, 新goal)}；跳过注释、空行、未填新值的行。"""
    mapping: Dict[str, Tuple[str, str]] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for lineno, line in enumerate(f, start=1):
            raw = line.rstrip("\n")
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            parts = raw.split("\t")
            if len(parts) < 3:
                print(f"[WARN] 映射表第 {lineno} 行字段不足，跳过：{raw!r}")
                continue
            tid, old, new = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if not tid or not new:
                continue
            if new == old:
                print(f"[WARN] {tid} 新旧 goal 相同，跳过")
                continue
            mapping[tid] = (old, new)
    return mapping


def rewrite(path: str, mapping: Dict[str, Tuple[str, str]], write: bool) -> Tuple[int, Set[str], List[str]]:
    """改写一个 gt.jsonl。返回 (改写条数, 未覆盖的 task_id, 变化明细)。"""
    if not os.path.exists(path):
        print(f"[SKIP] 文件不存在：{path}")
        return 0, set(), []

    rows: List[dict] = []
    changed = 0
    unmapped: Set[str] = set()
    details: List[str] = []

    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                sys.exit(f"[FATAL] {path} 第 {lineno} 行不是合法 JSON：{exc}")

            tid = str(row.get("task_id") or "").strip()
            step = row.get("step") or 1
            try:
                step_int = int(step)
            except (TypeError, ValueError):
                step_int = 1

            if tid and step_int == 1:
                if tid in mapping:
                    old, new = mapping[tid]
                    row["goal"] = new
                    changed += 1
                    details.append(f"{os.path.basename(path)}  {tid}  {old} -> {new}")
                else:
                    unmapped.add(tid)

            rows.append(row)

    if write:
        bak = path + ".bak"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
            print(f"[BAK] {bak}")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)

    return changed, unmapped, details


def main() -> None:
    ap = argparse.ArgumentParser(description="把 step=1 的 goal 换成口语")
    ap.add_argument("--map", default="data/eval/goal_map.tsv")
    ap.add_argument("--gt", default="data/eval/gt.jsonl")
    ap.add_argument("--gt50", default="data/eval/gt_first50.jsonl")
    ap.add_argument("--write", action="store_true", help="真正写回；不加则只报告")
    args = ap.parse_args()

    mapping = load_map(args.map)
    print(f"映射表：{len(mapping)} 条\n")

    total = 0
    all_unmapped: Set[str] = set()
    for path in (args.gt50, args.gt):
        changed, unmapped, details = rewrite(path, mapping, args.write)
        total += changed
        all_unmapped |= unmapped
        print(f"{os.path.basename(path)}: 改写 {changed} 条")
        for d in details[:3]:
            print(f"  e.g. {d}")

    print(f"\n合计改写 {total} 条")

    if all_unmapped:
        print(f"\n还有 {len(all_unmapped)} 个 step=1 的 task_id 没覆盖：")
        for tid in sorted(all_unmapped):
            print(f"  - {tid}")
        print("补进 goal_map.tsv 后重跑即可。")

    if not args.write:
        print("\n[dry-run] 没有写文件。确认无误后加 --write。")


if __name__ == "__main__":
    main()
