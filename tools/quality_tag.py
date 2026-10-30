"""给用例打质量标记，评测时可排除低质量样本。

不删数据（删了就找不回来），只在 gt.jsonl 的每条上加一个可选字段 `quality`：
    "ok"    默认，正常参与评测
    "low"   可用但价值低（界面信息太少、框过大）
    "bad"   不该参与评测（分辨率过低、界面被遮挡、OCR 提不出候选）

用法：
    python tools/quality_tag.py              # 打标记并写回
    python tools/quality_tag.py --dry-run    # 只看结果
    python tools/quality_tag.py --list       # 列出所有非 ok 的
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"
IMAGES = ROOT / "data" / "eval" / "images"
OCR_DIR = ROOT / "data" / "eval" / "_ocr"

# 手动判定：这些图有明确问题
MANUAL: Dict[str, Tuple[str, str]] = {
    "035": ("bad", "分辨率 456x654，OCR 小字识别率不足"),
    "051": ("bad", "分辨率 552x971，OCR 小字识别率不足"),
    "046": ("bad", "相机取景界面，几乎全是图标，OCR 只提取到 3 个词"),
    "047": ("bad", "图片编辑界面，OCR 只提取到「完成」一个词"),
    "050": ("low", "微信顶栏截图，画面只有一条，可点元素极少"),
    "016": ("low", "提示文案为主的界面，可点元素只有 1 个"),
}

# 自动判定阈值
MIN_WIDTH = 800
MIN_OCR = 4


def load_gt() -> List[dict]:
    if not GT.exists():
        raise SystemExit(f"找不到 {GT}")
    return [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]


def save_gt(rows: List[dict]) -> None:
    tmp = GT.with_suffix(".jsonl.tmp")
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    tmp.write_text(text, encoding="utf-8")
    import os

    os.replace(tmp, GT)


def image_size(name: str) -> Tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(IMAGES / name) as im:
            return im.size
    except Exception:
        return (0, 0)


def ocr_count(stem: str) -> int:
    f = OCR_DIR / f"{stem}.json"
    if not f.exists():
        return 0
    try:
        return len(json.loads(f.read_text(encoding="utf-8")))
    except Exception:
        return 0


def judge(row: dict) -> Tuple[str, str]:
    """返回 (quality, reason)。"""
    stem = Path(str(row["file"])).stem

    if stem in MANUAL:
        return MANUAL[stem]

    w, h = image_size(Path(str(row["file"])).name)
    if w and w < MIN_WIDTH:
        return ("bad", f"宽度仅 {w}px，低于 {MIN_WIDTH}px 阈值")

    n = ocr_count(stem)
    if 0 < n < MIN_OCR:
        return ("low", f"OCR 只提取到 {n} 个候选，可选元素太少")

    # 框占全图比例过大 → 判定过松
    if w and h:
        x0, y0, x1, y1 = row["bbox"]
        if (x1 - x0) * (y1 - y0) > (w * h) * 0.35:
            return ("low", "框面积超过画面 35%，命中判定偏松")

    return ("ok", "")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true", help="只列出非 ok 的")
    args = parser.parse_args(argv)

    rows = load_gt()

    if args.list:
        for r in rows:
            q = r.get("quality", "ok")
            if q != "ok":
                print(f"  [{q:4}] {Path(r['file']).name:<11} {r['goal']:<18} {r.get('reason','')}")
        counts: Dict[str, int] = {}
        for r in rows:
            q = r.get("quality", "ok")
            counts[q] = counts.get(q, 0) + 1
        print(f"\n统计: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        return 0

    changed = 0
    counts: Dict[str, int] = {}
    for r in rows:
        q, reason = judge(r)
        if r.get("quality") != q:
            changed += 1
        r["quality"] = q
        if reason:
            r["reason"] = reason
        else:
            r.pop("reason", None)
        counts[q] = counts.get(q, 0) + 1

    print(f"共 {len(rows)} 条，标记变更 {changed} 条")
    print("分布: " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    bad = [(Path(r["file"]).name, r["goal"], r.get("reason", ""))
           for r in rows if r.get("quality") == "bad"]
    low = [(Path(r["file"]).name, r["goal"], r.get("reason", ""))
           for r in rows if r.get("quality") == "low"]

    if bad:
        print(f"\n-- bad（{len(bad)} 条，建议不参与评测）--")
        for n, g, why in bad:
            print(f"  {n:<11} {g:<18} {why}")
    if low:
        print(f"\n-- low（{len(low)} 条，可用但价值低）--")
        for n, g, why in low:
            print(f"  {n:<11} {g:<18} {why}")

    if args.dry_run:
        print("\n--dry-run，未写入")
        return 0

    save_gt(rows)
    print(f"\n已写回 {GT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
