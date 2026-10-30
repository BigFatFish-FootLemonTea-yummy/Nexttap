"""修正多步链：只保留「同一 App 内、有真实跳转关系」的链。

问题背景：
    自动标注是按「文字匹配」串链的，会把不同小程序的同名按钮串到一起，
    例如「白云区人民医院首页 → 患者服务平台首页」，
    现实中这两个是不同小程序，跳不过去。

本脚本按人工确认的真实路径重写链，其余全部降级为单步用例
（task_id 保留，便于 RAG 检索；但不参与整链统计）。

真实链（人工核对过界面跳转）：
    wechat.video_call_son       041 聊天列表 → 042 聊天页 → 043 功能面板 → 044 弹窗
    hospital.book_appointment   001 首页 → 005 科室列表 → 011 医生列表 → 013 挂号详情
    gov.elder_mode              018 粤省事首页 → 048 关怀版首页

跑法：
    python tools/fix_chains.py --dry-run
    python tools/fix_chains.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"

# --------------------------------------------------------------------------
# 真实链路定义：(task_id, [(图片stem, 该步的 goal 关键词, 新 goal)])
#   goal 关键词用于在当前 gt 里定位那一行；新 goal 用于改名（让每步的指令更准确）
#
# 判定标准（三条都要满足）：
#   1. 同一个 App / 小程序内
#   2. 相邻两步是「上一屏点完跳到下一屏」的真实跳转
#   3. 每一步都真的有「该点的地方」
# --------------------------------------------------------------------------
REAL_CHAINS: List[Tuple[str, List[Tuple[str, str, str]]]] = [
    (
        "wechat.video_call_son",
        [
            ("041", "儿子", "点儿子的微信"),
            ("043", "视频通话", "点视频通话"),
            ("044", "视频通话", "选视频通话"),
        ],
    ),
    (
        "hospital.book_appointment",
        [
            ("001", "预约挂号", "点预约挂号"),
            ("005", "神经医学科", "选神经医学科"),
            ("011", "袁嘉蔚", "选医生袁嘉蔚"),
            ("013", "挂号详情", "确认挂号详情"),
        ],
    ),
    # gov.elder_mode 已删除：018 和 048 是同一个页面截了两次，不是两步操作
]

# 明确作废的链：成员全部降级为单步
DEAD_CHAINS: List[str] = [
    "gov.elder_mode",
]

# 这些链降级为单步（task_id 保留但不再算链）——由 CHAIN_EXCLUDE 处理即可
# 这里只需要把「串错的」成员从原链里摘出来


def load_gt() -> List[dict]:
    return [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]


def save_gt(rows: List[dict]) -> None:
    tmp = GT.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                   encoding="utf-8")
    os.replace(tmp, GT)


def find_row(rows: List[dict], stem: str, keyword: str, tid: str, step: int) -> dict | None:
    """在指定图片里找对应的那一行。

    三种匹配方式按优先级：
      1. 已经是 (task_id, step) 的那一行 —— 重跑时最可靠
      2. 图片 + goal 含 keyword
      3. 图片 + task_id 相同（该图只有一个候选时）
    """
    same_img = [r for r in rows if Path(r["file"]).stem == stem]
    if not same_img:
        return None

    exact = [r for r in same_img
             if r.get("task_id") == tid and int(r.get("step", 0) or 0) == step]
    if exact:
        return exact[0]

    by_kw = [r for r in same_img if keyword in str(r.get("goal", ""))]
    if by_kw:
        by_kw.sort(key=lambda r: (r["bbox"][2] - r["bbox"][0]) * (r["bbox"][3] - r["bbox"][1]),
                   reverse=True)
        return by_kw[0]

    by_tid = [r for r in same_img if r.get("task_id") == tid]
    if len(by_tid) == 1:
        return by_tid[0]

    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    rows = load_gt()
    before_chains: Dict[str, int] = {}
    for r in rows:
        t = r.get("task_id", "")
        if t:
            before_chains[t] = before_chains.get(t, 0) + 1

    # 1. 先把每条真实链的成员「认领」下来，再处理多余的
    claimed: Dict[int, Tuple[str, int, str]] = {}  # id(row) -> (task_id, step, goal)
    missing: List[str] = []

    for tid, members in REAL_CHAINS:
        for i, (stem, kw, new_goal) in enumerate(members, 1):
            row = find_row(rows, stem, kw, tid, i)
            if row is None:
                missing.append(f"{tid} step{i} {stem}.jpg 含「{kw}」")
                continue
            claimed[id(row)] = (tid, i, new_goal)

    # 2. 属于这些 task_id 但没被认领的 → 摘成 .extra（不参与整链）
    changed = 0
    target_tids = {tid for tid, _ in REAL_CHAINS}
    for r in rows:
        if r.get("task_id") in target_tids and id(r) not in claimed:
            r["task_id"] = f"{r['task_id']}.extra"
            r["step"] = 1
            changed += 1

    # 2.5 作废的链：成员全部降级为单步
    #     每个成员给一个独立的 task_id，否则同一个 .single 下有多条还是会被当成链
    dead_changed = 0
    for r in rows:
        if r.get("task_id") in DEAD_CHAINS:
            stem = Path(r["file"]).stem
            r["task_id"] = f"{r['task_id']}.single.{stem}"
            r["step"] = 1
            dead_changed += 1

    # 3. 写回认领结果
    for r in rows:
        if id(r) in claimed:
            tid, step, goal = claimed[id(r)]
            r["task_id"] = tid
            r["step"] = step
            r["goal"] = goal

    # 4. 报告
    report: List[str] = []
    for tid, members in REAL_CHAINS:
        report.append(f"\n[{tid}]  {len(members)} 步")
        for r in sorted([x for x in rows if x.get("task_id") == tid],
                        key=lambda x: int(x.get("step", 1) or 1)):
            report.append(f"   step{r['step']}  {Path(r['file']).name:<10} "
                          f"{r['goal']:<16} bbox={r['bbox']}")
        for m in missing:
            if m.startswith(tid):
                report.append(f"   ! {m}")

    # 3. 统计
    after_chains: Dict[str, int] = {}
    for r in rows:
        t = r.get("task_id", "")
        if t:
            after_chains[t] = after_chains.get(t, 0) + 1
    multi = {t: n for t, n in after_chains.items() if n > 1}

    print("=" * 70)
    print("重写真实链")
    print("=" * 70)
    for line in report:
        print(line)

    print()
    print(f"总用例: {len(rows)}")
    print(f"多步链: {len(multi)} 条  {dict(sorted(multi.items()))}")
    print(f"单步用例: {len(rows) - sum(multi.values())} 条")
    if changed:
        print(f"从错误链里摘出: {changed} 条（task_id 加了 .extra，不参与整链）")

    if args.dry_run:
        print("\n--dry-run，未写入")
        return 0

    save_gt(rows)
    print(f"\n已写回 {GT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
