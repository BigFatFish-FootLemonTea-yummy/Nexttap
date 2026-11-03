#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""评测数据一致性审计（+ 可选的 pred 身份对齐）。

## 2026-11 变更：gt_first50.jsonl 已废弃

旧集（`gt.jsonl` 164 条 / `images/` 47 张扁平图）配套过一个 `gt_first50.jsonl`，
本脚本原来靠它「按 bbox 逐像素相等」把掉队的 5 行补回 task_id / step。

新集（`gt_nexttap_v2` → 现在的 `gt.jsonl` 118 条 / `images/` 113 张分层图）
**每一行都自带 task_id 和 step**，且没有 first50 这个中间产物，所以：

    - 「从 first50 补身份」的逻辑已整段删除；
    - 不再生成 / 校验 gt_first50.jsonl；
    - 如果以后又出现缺 task_id / step 的行，本脚本只报告，不猜测 ——
      因为新集没有第二个可信来源可以对 bbox 做交叉验证。

## 现在做什么

    1. AUDIT ：gt.jsonl 结构体检（必填字段、bbox 越界、task_id/step 完整）
    2. AUDIT ：同一 bbox 对应多个身份、链上 step 重复、链上缺步
    3. AUDIT ：新集 task_id 与 paths.jsonl 的命名体系差异提示
    4. FIX   ：pred.jsonl 里缺 task_id / step 的行，按 (file, goal) 回填
               （只有唯一匹配才回填，否则不动）

用法：
    python tools/repair_eval_data.py --dry-run    # 只打印报告（默认行为）
    python tools/repair_eval_data.py --write      # 回填 pred.jsonl 的缺失身份
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
    ap = argparse.ArgumentParser(
        description="评测数据一致性审计（gt_first50 逻辑已随新集废弃）")
    ap.add_argument("--write", action="store_true",
                    help="回填 pred.jsonl 里缺失的 task_id/step（默认只干跑）")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告（默认）")
    args = ap.parse_args(argv)
    write = args.write and not args.dry_run

    gt = load_jsonl(GT)
    if not gt:
        print(f"[FATAL] 读不到 {GT}")
        return 2
    chain_targets = load_chain_targets()

    # ------------------------------------------------------------------
    print("=" * 72)
    print("1. 缺 task_id / step 的行")
    print("=" * 72)
    broken = [(i, r) for i, r in enumerate(gt, 1) if not has_identity(r)]
    if not broken:
        print("  PASS  没有缺字段的行")
    else:
        for i, r in broken:
            print(f"  第{i:3d}行  {r.get('file')}  goal={r.get('goal')!r}  bbox={r.get('bbox')}")
        print()
        print("  [注意] 新集没有 gt_first50.jsonl 可以交叉验证，本脚本不猜身份。")
        print("         请手工确认这两行属于哪条链的哪一步后直接改 gt.jsonl。")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("2. 结构体检")
    print("=" * 72)
    bad_rows = 0
    for i, r in enumerate(gt, 1):
        for field in ("file", "goal"):
            if not str(r.get(field) or "").strip():
                bad_rows += 1
                print(f"  FAIL  第{i:3d}行缺 {field}")
        box = r.get("bbox") or []
        if len(box) < 4:
            bad_rows += 1
            print(f"  FAIL  第{i:3d}行 bbox 不完整：{box}")
            continue
        x0, y0, x1, y1 = (int(v) for v in box[:4])
        if not (x0 < x1 and y0 < y1):
            bad_rows += 1
            print(f"  FAIL  第{i:3d}行 bbox 不是左上/右下顺序：{list(box[:4])}")
    if not bad_rows:
        print(f"  PASS  {len(gt)} 行结构完整（file / goal / bbox 齐全且顺序正确）")

    # 图片是否存在
    missing_img = sorted({r["file"] for r in gt if not (EVAL_DIR / str(r["file"])).exists()})
    if missing_img:
        print(f"  FAIL  {len(missing_img)} 个 file 指向的图片不存在：")
        for f in missing_img[:10]:
            print(f"        {f}")
    else:
        print(f"  PASS  所有 file 都能在 data/eval/ 下找到（共 {len({r['file'] for r in gt})} 张图）")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("3. 一致性审计（只报告，不自动改）")
    print("=" * 72)
    warns = 0

    # 3.1 同一个 bbox 被标成不同的 task_id / step
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
    #     注意区分两种情况：
    #       - 同一 (file, goal) 多行 = 同一目标的多个可接受框，**正常**，不算重复；
    #       - 同一 step 落在不同图 / 不同 goal 上 = 真的重复，需要人工看。
    print("  3.2 链上 step 重复：")
    chain_steps: Dict[str, List[Tuple[int, int, str]]] = {}
    for i, r in enumerate(gt, 1):
        tid = str(r.get("task_id") or "").strip()
        if tid:
            chain_steps.setdefault(tid, []).append((int(r.get("step") or 1), i, str(r.get("goal"))))
    step_dup = False
    alt_boxes = 0
    for tid, rows in sorted(chain_steps.items()):
        seen: Dict[int, List[int]] = {}
        for s, i, _g in rows:
            seen.setdefault(s, []).append(i)
        dups = {s: v for s, v in seen.items() if len(v) > 1}
        for s, idxs in dups.items():
            # 这些行的 (file, goal) 全相同 => 只是多个可接受框
            sigs = {(str(gt[i - 1].get("file")), str(gt[i - 1].get("goal")).strip()) for i in idxs}
            if len(sigs) == 1:
                alt_boxes += 1
            else:
                step_dup = True
                warns += 1
                print(f"    WARN {tid}: step {s} 出现在 {len(sigs)} 组 (file, goal) 上 -> 行 {idxs}")
    if not step_dup:
        if alt_boxes:
            print(f"    PASS 没有真正重复的 step（{alt_boxes} 处是同一目标的多个可接受框，属正常）")
        else:
            print("    PASS 没有重复 step")

    # 3.3 链上缺步（gt 的步数少于 paths.jsonl 的步数）
    #     新集的 task_id 与 paths.jsonl 是两套命名体系，能对上的才比
    print("  3.3 链上缺步（gt 步数 < paths.jsonl 步数，仅比对能对上的 task_id）：")
    comparable = {t: rows for t, rows in chain_steps.items() if t in chain_targets}
    if not comparable:
        print("    SKIP  gt 的 task_id 与 paths.jsonl 无交集（两套独立命名），无法比对")
    else:
        missing_found = False
        for tid, rows in sorted(comparable.items()):
            want = len(chain_targets.get(tid) or [])
            got = len({s for s, _i, _g in rows})
            if want and got < want:
                missing_found = True
                warns += 1
                print(f"    WARN {tid}: gt 有 {got} 步，paths.jsonl 有 {want} 步")
        if not missing_found:
            print("    PASS 没有缺步的链")

    # 3.4 goal 泄漏
    print("  3.4 step=1 的 goal 泄漏答案：请跑 tools/audit_goal_leak.py（需要 OCR）")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("4. 数据分布")
    print("=" * 72)
    n1 = sum(1 for r in gt if int(r.get("step") or 1) == 1)
    print(f"  step=1  : {n1}")
    print(f"  step>=2 : {len(gt) - n1}")
    print(f"  总计    : {len(gt)}   （分布在 {len({r['file'] for r in gt})} 张图上）")
    print(f"  任务链  : {len(chain_steps)} 条")

    # ------------------------------------------------------------------
    print()
    print("=" * 72)
    print("5. pred.jsonl 身份对齐")
    print("=" * 72)
    pred = load_jsonl(PRED)
    if not pred:
        print("  SKIP  pred.jsonl 不存在（还没跑 run_eval.py）")
        return 0 if not bad_rows else 1

    gt_keys = {case_key(r) for r in gt}
    gt_by_goal: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in gt:
        gt_by_goal.setdefault((str(r.get("file")), str(r.get("goal")).strip()), []).append(r)

    changed = 0
    ambiguous = 0
    for r in pred:
        if has_identity(r):
            continue
        cands = gt_by_goal.get((str(r.get("file")), str(r.get("goal")).strip()), [])
        ids = {(str(c.get("task_id") or "").strip(), int(c.get("step") or 1))
               for c in cands if str(c.get("task_id") or "").strip()}
        if len(ids) == 1:
            tid, step = next(iter(ids))
            r["task_id"], r["step"] = tid, step
            changed += 1
        else:
            ambiguous += 1

    print(f"  pred 条数        : {len(pred)}")
    print(f"  可回填           : {changed}")
    print(f"  无法唯一确定     : {ambiguous}")
    unmatched = [r for r in pred if case_key(r) not in gt_keys]
    print(f"  pred 与 gt 对不上: {len(unmatched)}")

    if not write:
        print()
        print(f"[DRY-RUN] 未写入。一致性告警 {warns} 项需人工确认，确认后加 --write")
        return 0

    if changed:
        bak = PRED.with_suffix(PRED.suffix + ".bak-repair")
        if not bak.exists():
            shutil.copy2(PRED, bak)
            print(f"  备份 -> {bak.name}")
        dump_jsonl(PRED, pred)
        print(f"  写入 {PRED.name}（回填 {changed} 条）")
    else:
        print("  没有需要回填的行，未改动 pred.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
