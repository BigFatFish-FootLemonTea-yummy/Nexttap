#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测数据修复 + 一致性审计。

背景（2026-11-01 排查）：

    A. 根因：tools/apply_goal_map.py 只改写 `if tid and step_int == 1` 的行，
       即**必须有 task_id**。gt.jsonl 里有 5 行没有 task_id，于是被整体跳过：
       既没补身份，也没做口语化改写，goal 仍是「点关怀版」这类泄漏答案的写法。

    B. 这 5 行不是「无主用例」，而是从多步链里掉出来的步骤。后果：
       - 评测口径被污染：step 缺省成 1，被算进「step=1 用户口语」这个最关键
         的指标里，而它们实际是 step>=2 的路径内指令；
       - 整链统计被拆散：wechat.video_call_son 少一步，gov.elder_mode 整条链消失；
       - gt_first50 对不上：按 (file, task_id, step) 匹配报「16 条缺预测」。

本脚本做两件事：

    1. FIX   ：用 gt_first50 里 **bbox 完全相同** 的记录补回 task_id / step。
               bbox 逐像素相等才算，不做模糊匹配。不改 bbox、不改 goal、不改图片。
    2. WARN  ：输出一致性审计报告（同一 bbox 对应多个 task_id/step、链上缺步等），
               这些需要人工判断，脚本不擅自改。

用法：
    python tools/repair_eval_data.py --dry-run
    python tools/repair_eval_data.py --write
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
GT = EVAL_DIR / "gt.jsonl"
PRED = EVAL_DIR / "pred.jsonl"
FIRST50 = EVAL_DIR / "gt_first50.jsonl"
PATHS = ROOT / "data" / "rag" / "paths.jsonl"

Key = Tuple[str, str, int, str]


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dump_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    """先写 .tmp 再 os.replace，避免写一半断电。"""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def case_key(row: Dict[str, Any]) -> Key:
    """必须与 eval.py / run_eval.py 的 case_key 语义完全一致。"""
    return (str(row.get("file", "")).replace("\\", "/"),
            str(row.get("task_id") or "").strip(),
            int(row.get("step") or 1),
            str(row.get("goal", "")).strip())


def box_of(row: Dict[str, Any]) -> Tuple[int, ...]:
    return tuple(int(v) for v in (row.get("bbox") or [])[:4])


def load_chain_targets() -> Dict[str, List[str]]:
    """{task_id: [每步 target]}，paths.jsonl 是任务链的权威来源。"""
    out: Dict[str, List[str]] = {}
    for p in load_jsonl(PATHS):
        tid = str(p.get("task_id") or "").strip()
        if tid:
            out[tid] = [str(s.get("target") or "").strip() for s in (p.get("steps") or [])]
    return out


def has_identity(row: Dict[str, Any]) -> bool:
    return bool(str(row.get("task_id") or "").strip()) and row.get("step") is not None


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="修复 gt 的 task_id/step 并重建 gt_first50")
    ap.add_argument("--write", action="store_true", help="真正落盘（默认只干跑）")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告")
    args = ap.parse_args(argv)
    write = args.write and not args.dry_run

    gt = load_jsonl(GT)
    if not gt:
        print(f"[FATAL] 读不到 {GT}")
        return 2
    first50 = load_jsonl(FIRST50)
    chain_targets = load_chain_targets()

    # ------------------------------------------------------------------
    print("=" * 72)
    print("1. 找出缺 task_id / step 的行")
    print("=" * 72)
    broken = [(i, r) for i, r in enumerate(gt, 1) if not has_identity(r)]
    if not broken:
        print("  PASS  没有缺字段的行")
    for i, r in broken:
        print(f"  第{i:3d}行  {r.get('file')}  goal={r.get('goal')!r}  bbox={r.get('bbox')}")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("2. 按 bbox 逐像素相等，从 gt_first50 补回身份")
    print("=" * 72)
    f50_by_box: Dict[Tuple[str, Tuple[int, ...]], List[Dict[str, Any]]] = {}
    for f in first50:
        f50_by_box.setdefault((f.get("file"), box_of(f)), []).append(f)

    fixed: List[Tuple[int, str, int]] = []
    unresolved: List[int] = []
    for i, r in broken:
        cands = f50_by_box.get((r.get("file"), box_of(r)), [])
        ids = {(str(c.get("task_id") or "").strip(), int(c.get("step") or 1))
               for c in cands if str(c.get("task_id") or "").strip()}
        if len(ids) == 1:
            tid, step = next(iter(ids))
            fixed.append((i, tid, step))
            print(f"  FIX   第{i:3d}行  {r.get('file')}  {r.get('goal')!r}")
            print(f"        -> task_id={tid}  step={step}   （bbox 完全匹配 gt_first50）")
        else:
            unresolved.append(i)
            why = "gt_first50 里找不到同 bbox 的行" if not ids else f"匹配到多个身份 {ids}"
            print(f"  FAIL  第{i:3d}行  {r.get('file')}  {r.get('goal')!r}  —— {why}")

    if unresolved:
        print(f"\n[ABORT] {len(unresolved)} 行无法自动推断，不落盘。请人工确认后重跑。")
        return 1

    for i, tid, step in fixed:
        gt[i - 1]["task_id"] = tid
        gt[i - 1]["step"] = step

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("3. 一致性审计（只报告，不自动改）")
    print("=" * 72)
    warns = 0

    # 3.1 同一个 bbox 被标成不同的 task_id / step —— 多半是 apply_goal_map 漏改留下的
    print("  3.1 同一 bbox 对应多个身份：")
    by_box: Dict[Tuple[str, Tuple[int, ...]], List[Tuple[int, Dict[str, Any]]]] = {}
    for i, r in enumerate(gt, 1):
        by_box.setdefault((r.get("file"), box_of(r)), []).append((i, r))
    dup_found = False
    for (_f, _b), group in by_box.items():
        ids = {case_key(r)[1:3] for _, r in group}
        if len(ids) > 1:
            dup_found = True
            warns += 1
            print(f"    WARN {group[0][1].get('file')} bbox={list(_b)}")
            for i, r in group:
                print(f"         行{i:3d} task_id={str(r.get('task_id') or ''):26} "
                      f"step={r.get('step')} goal={r.get('goal')!r}")
    if not dup_found:
        print("    PASS 没有重复 bbox")

    # 3.2 链上 step 重复
    print("  3.2 链上 step 重复：")
    chain_steps: Dict[str, List[Tuple[int, int, str]]] = {}
    for i, r in enumerate(gt, 1):
        tid = str(r.get("task_id") or "").strip()
        if tid:
            chain_steps.setdefault(tid, []).append((int(r.get("step") or 1), i, str(r.get("goal"))))
    step_dup = False
    for tid, rows in sorted(chain_steps.items()):
        seen: Dict[int, List[int]] = {}
        for s, i, _g in rows:
            seen.setdefault(s, []).append(i)
        dups = {s: v for s, v in seen.items() if len(v) > 1}
        if dups:
            step_dup = True
            warns += 1
            print(f"    WARN {tid}: 重复 step {dups}")
    if not step_dup:
        print("    PASS 没有重复 step")

    # 3.3 链上缺步（gt 的步数少于 paths.jsonl 的步数）
    print("  3.3 链上缺步（gt 步数 < paths.jsonl 步数）：")
    missing_found = False
    for tid, rows in sorted(chain_steps.items()):
        want = len(chain_targets.get(tid) or [])
        got = len({s for s, _i, _g in rows})
        if want and got < want:
            missing_found = True
            warns += 1
            print(f"    WARN {tid}: gt 有 {got} 步，paths.jsonl 有 {want} 步")
    if not missing_found:
        print("    PASS 没有缺步的链")

    # 3.4 goal 里含目标框文字（泄漏答案），只查 step=1
    print("  3.4 step=1 的 goal 泄漏答案（需 OCR，跑 tools/audit_goal_leak.py）")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("4. 修复后的分布")
    print("=" * 72)
    n1 = sum(1 for r in gt if int(r.get("step") or 1) == 1)
    print(f"  step=1  : {n1}")
    print(f"  step>=2 : {len(gt) - n1}")
    print(f"  总计    : {len(gt)}   （分布在 {len({r['file'] for r in gt})} 张图上）")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("5. 重建 gt_first50")
    print("=" * 72)
    new50 = gt[:50]
    n50_1 = sum(1 for r in new50 if int(r.get("step") or 1) == 1)
    print(f"  取 gt.jsonl 前 {len(new50)} 条，覆盖 {len({r['file'] for r in new50})} 张图")
    print(f"  其中 step=1 {n50_1} 条，step>=2 {len(new50) - n50_1} 条")

    gt_keys = {case_key(r) for r in gt}
    not_subset = [r for r in new50 if case_key(r) not in gt_keys]
    if not_subset:
        print(f"  FAIL  first50 有 {len(not_subset)} 条不在 gt 里，不落盘")
        return 1
    print("  PASS  first50 是 gt.jsonl 的子集（按 file+task_id+step+goal 校验）")

    if not write:
        print()
        print(f"[DRY-RUN] 未写入。一致性告警 {warns} 项需人工确认，确认后加 --write")
        return 0

    # ------------------------------------------------------------------
    for p in (GT, FIRST50, PRED):
        bak = p.with_suffix(p.suffix + ".bak-repair")
        if p.exists() and not bak.exists():
            shutil.copy2(p, bak)
            print(f"  备份 -> {bak.name}")

    dump_jsonl(GT, gt)
    print(f"  写入 {GT.name}（{len(gt)} 条）")
    dump_jsonl(FIRST50, new50)
    print(f"  写入 {FIRST50.name}（{len(new50)} 条）")

    # pred 的匿名行也要对齐，否则 case_key 对不上，会被算成「缺预测」。
    # 注意：pred 的 bbox 是模型预测的，不等于真值框，所以只能按 (file, goal) 匹配。
    pred = load_jsonl(PRED)
    if pred:
        gt_by_goal: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for r in gt:
            gt_by_goal.setdefault((str(r.get("file")), str(r.get("goal")).strip()), []).append(r)
        changed = 0
        for r in pred:
            if not has_identity(r):
                cands = gt_by_goal.get((str(r.get("file")), str(r.get("goal")).strip()), [])
                ids = {(str(c.get("task_id") or "").strip(), int(c.get("step") or 1))
                       for c in cands if str(c.get("task_id") or "").strip()}
                if len(ids) == 1:
                    r["task_id"], r["step"] = next(iter(ids))
                    changed += 1
        dump_jsonl(PRED, pred)
        print(f"  写入 {PRED.name}（对齐 {changed} 条匿名预测）")
        unmatched = [r for r in pred if case_key(r) not in gt_keys]
        print(f"  pred 与 gt 对不上的条数：{len(unmatched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
