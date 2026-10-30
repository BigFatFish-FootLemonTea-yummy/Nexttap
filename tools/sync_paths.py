"""从 gt.jsonl 反推 paths.jsonl：把标注里出现的 task_id 全部登记成任务链。

已有的条目保留（不覆盖 aliases 和 name），只补缺失的。
中文名从 goal 里推（去掉「点」「选」前缀）。

跑法：
    python tools/sync_paths.py --dry-run
    python tools/sync_paths.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"
PATHS = ROOT / "data" / "rag" / "paths.jsonl"

# 域 -> 老人会用的口语模板
DOMAIN_ALIAS: Dict[str, List[str]] = {
    "hospital": ["{name}", "怎么{name}", "我要{name}", "帮我{name}"],
    "gov": ["{name}", "怎么{name}", "我要{name}"],
    "wechat": ["{name}", "怎么{name}"],
    "map": ["{name}", "怎么{name}"],
    "setting": ["{name}", "怎么{name}"],
}


def clean_name(goal: str) -> str:
    """「点预约挂号」-> 「预约挂号」；「选科室骨科」-> 「科室骨科」。"""
    name = re.sub(r"^(点|选|拖|输|确认|查看)", "", goal.strip())
    return name or goal


def guess_domain(task_id: str) -> str:
    return task_id.split(".")[0] if "." in task_id else "misc"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not GT.exists():
        raise SystemExit(f"找不到 {GT}")
    rows = [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]

    # 现有 paths.jsonl
    existing: "OrderedDict[str, dict]" = OrderedDict()
    if PATHS.exists():
        for line in PATHS.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            tid = str(e.get("task_id", "")).strip()
            if tid:
                existing[tid] = e

    # 从 gt 收集
    collected: "OrderedDict[str, List[dict]]" = OrderedDict()
    for r in rows:
        tid = str(r.get("task_id", "")).strip()
        if not tid:
            continue
        collected.setdefault(tid, []).append(r)

    added = 0
    # 先算出所有 alias，用来检测跨任务撞名
    alias_owner: Dict[str, str] = {}
    for tid, e in existing.items():
        for a in e.get("aliases") or []:
            alias_owner.setdefault(a, tid)

    for tid, cases in collected.items():
        cases.sort(key=lambda c: int(c.get("step", 1) or 1))
        first = cases[0]
        name = clean_name(str(first["goal"]))
        domain = guess_domain(tid)
        templates = DOMAIN_ALIAS.get(domain, ["{name}", "怎么{name}"])

        # 已有条目：保留其 name，但 aliases 为空时要补
        old = existing.get(tid)
        if old and old.get("aliases"):
            continue

        aliases = []
        for t in templates:
            s = t.format(name=name)
            # 如果这句已经被别的任务占了，就加上域前缀区分
            # 例如「扫一扫」在微信和电子社保卡里都有，必须说清楚是哪个 App
            if s in alias_owner and alias_owner[s] != tid:
                s = f"{domain}里的{name}"
            if s in aliases or (s in alias_owner and alias_owner[s] != tid):
                continue
            aliases.append(s)
            alias_owner.setdefault(s, tid)

        steps = []
        for i, c in enumerate(cases, 1):
            steps.append({
                "step": i,
                "screen": "",
                "target": str(c["goal"]),
                "hint": f"点「{clean_name(str(c['goal']))}」",
            })

        existing[tid] = {
            "task_id": tid,
            "name": name,
            "app": "",
            "aliases": aliases,
            "steps": steps,
        }
        added += 1

    print(f"原有 {len(existing) - added} 条，新增 {added} 条，合计 {len(existing)} 条\n")

    # 兜底：任何条目 aliases 为空时，用 name 生成最小可用集合
    for tid, e in existing.items():
        if not e.get("aliases"):
            nm = e.get("name") or tid
            dom = guess_domain(tid)
            e["aliases"] = [f"{dom}里的{nm}", f"怎么{nm}"]
            print(f"  补 aliases: {tid} -> {e['aliases']}")

    if args.dry_run:
        for tid in list(existing)[:80]:
            e = existing[tid]
            print(f"{tid:34} {e['name']:14} aliases={len(e['aliases'])} steps={len(e['steps'])}")
        print("\n--dry-run，未写入")
        return 0

    PATHS.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(e, ensure_ascii=False) for e in existing.values()) + "\n"
    PATHS.write_text(text, encoding="utf-8")
    print(f"已写入 {PATHS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
