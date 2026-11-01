#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索评测：Recall@K + MRR + 消融对比。

指标口径（与定位评测分开，不要混报）：
    Recall@K  正确答案出现在前 K 条里的比例
    MRR       平均倒数排名（正确答案排第 1 = 1.0，排第 3 = 0.33）

用法：
    # 单次
    python tools/eval_retrieval.py

    # 消融表（技术文档要放这张）
    python tools/eval_retrieval.py --ablation

消融三档（对应交接文档 §十二）：
    1. lexical    纯词面召回（现状）
    2. +vector    RRF 融合词面与向量
    3. +rerank    再叠一层 rerank
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

DEFAULT_EVAL = ROOT / "data" / "rag" / "retrieval_full.jsonl"
# 默认用盲写集（170 条，覆盖 120/120 任务链）。
# 不要默认用 retrieval_eval.jsonl——那 68 条里有 64 条逐字等于 gt.jsonl 的 goal，
# 属同源数据，跑出来的数字没有独立信息量。


def load_eval(path: Path) -> List[Dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def metrics(ranks: List[int], ks=(1, 3, 5)) -> Dict[str, float]:
    """ranks: 每条 query 正确答案的排名（1-based），未命中记为 0。"""
    n = len(ranks)
    if not n:
        return {}
    out: Dict[str, float] = {}
    for k in ks:
        out[f"Recall@{k}"] = sum(1 for r in ranks if 0 < r <= k) / n
    out["MRR"] = sum(1.0 / r for r in ranks if r > 0) / n
    out["Miss"] = sum(1 for r in ranks if r == 0) / n
    return out


def run(searcher: Callable[[str, int], List[Dict[str, Any]]], rows, top_k: int = 5):
    ranks: List[int] = []
    misses: List[Dict[str, Any]] = []
    for row in rows:
        hits = searcher(row["query"], top_k)
        ids = [h.get("task_id") for h in hits]
        try:
            rank = ids.index(row["task_id"]) + 1
        except ValueError:
            rank = 0
            misses.append({
                "query": row["query"],
                "want": row["task_id"],
                "got": ids[:3],
            })
        ranks.append(rank)
    return ranks, misses


def print_block(title: str, m: Dict[str, float], ranks: List[int], misses, verbose: bool):
    print(f"  {title}")
    if not m:
        print("    （无数据）")
        return
    print("    " + "  ".join(f"{k}={v * 100:.1f}%" if k != "MRR" else f"MRR={v:.3f}"
                             for k, v in m.items()))
    if verbose and misses:
        print(f"    未命中 {len(misses)} 条：")
        for mm in misses:
            print(f"      {mm['query']!r}  want={mm['want']}  got={mm['got']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="检索评测：Recall@K / MRR / 消融")
    ap.add_argument("--data", type=Path, default=DEFAULT_EVAL)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--ablation", action="store_true", help="跑消融三档对比")
    ap.add_argument("--verbose", action="store_true", help="打印未命中明细")
    ap.add_argument("--mode", default="hybrid",
                    choices=["lexical", "hybrid", "hybrid_rerank"],
                    help="单模式评测时用哪一档（默认 hybrid，即线上实际配置）")
    args = ap.parse_args(argv)

    if not args.data.exists():
        print(f"[FATAL] 评测集不存在：{args.data}")
        print("        可用的评测集：data/rag/retrieval_full.jsonl（盲写，推荐）")
        print("                      data/rag/retrieval_heldout.jsonl（留出）")
        print("                      data/rag/retrieval_eval.jsonl（初版，与 gt 同源，别引用）")
        return 2

    rows = load_eval(args.data)
    print("=" * 70)
    print(f"检索评测：{len(rows)} 条 query  →  {args.data.name}")
    print("=" * 70)
    print()

    from app import guide

    def run_mode(mode: str):
        """切到指定检索档位跑一轮。

        注意：模式是 guide 每次调用时读环境变量决定的，
        所以这里改环境变量 + reset_index() 就够了，不用重新 import。
        """
        import os

        os.environ["NEXTTAP_RETRIEVAL"] = mode
        guide.reset_index()
        return run(lexical, rows, args.top_k)

    def lexical(query: str, k: int):
        return guide.search(query, top_k=k)

    results: List[tuple] = []

    if args.ablation:
        ranks, misses = run_mode("lexical")
        m = metrics(ranks)
        results.append(("纯词面（基线）", m))
        print_block("1. 纯词面召回 lexical", m, ranks, misses, args.verbose)

        ranks2, misses2 = run_mode("hybrid")
        m2 = metrics(ranks2)
        results.append(("+ 向量召回 + RRF", m2))
        print()
        print_block("2. 词面 + 向量 + RRF 融合 hybrid", m2, ranks2, misses2, args.verbose)

        ranks3, misses3 = run_mode("hybrid_rerank")
        m3 = metrics(ranks3)
        results.append(("+ rerank", m3))
        print()
        print_block("3. 再叠 rerank", m3, ranks3, misses3, args.verbose)

        print()
        print("=" * 70)
        print("消融表（技术文档可直接引用）")
        print("=" * 70)
        header = f"{'配置':<24}" + "".join(f"{k:>12}" for k in ("Recall@1", "Recall@3", "Recall@5", "MRR"))
        print(header)
        print("-" * len(header))
        for name, mm in results:
            if not mm:
                continue
            print(f"{name:<24}"
                  + f"{mm['Recall@1'] * 100:>11.1f}%"
                  + f"{mm['Recall@3'] * 100:>11.1f}%"
                  + f"{mm['Recall@5'] * 100:>11.1f}%"
                  + f"{mm['MRR']:>12.3f}")
        print()
        print(f"评测集规模：{len(rows)} 条（{args.data.name}）")
        best = max((mm.get("Recall@5", 0) for _, mm in results), default=0)
    else:
        # 单模式：默认跑 hybrid（线上实际用的档位），可用 --mode 指定
        ranks, misses = run_mode(args.mode)
        m = metrics(ranks)
        results.append((args.mode, m))
        print_block(f"{args.mode}", m, ranks, misses, args.verbose)
        best = m.get("Recall@5", 0)

    print()
    # 不给单一 PASS/FAIL 判定，只报数字 + 风险提示。
    #
    # 原因（2026-11-01 独立验证指出）：
    #   1. 只看一个数字会助长虚报。实测留出集曾被刷到 100%，脚本当时直接
    #      打印「PASS」，等于工具在帮实现者背书。
    #   2. 指标高低取决于评测集有没有被"看过"。同一份数据，净化前后能差 11 个点。
    print(f"Recall@5 = {best * 100:.1f}%")
    print()
    print("⚠️  引用前请确认：")
    print("   - 这个数字有没有被「看着失败项补别名」污染？")
    print("     本项目曾出现：留出集 86.5% -> 100%，但净化后只有 88.5%")
    print("   - 同义词表（guide._SYNONYMS）里有没有评测集来源的词条？")
    print("   - 原集 68 条有 64 条逐字等于 gt.jsonl 的 goal，属同源数据，不要引用")
    print()
    print("   对外建议报「净化后」的数字，并披露净化口径。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
