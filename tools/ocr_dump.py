"""用 OCR 提取每张图的文字候选，供人工/AI 判断入口位置。

输出 data/eval/_ocr/<name>.json，每行一个候选：
    {"text": "预约挂号", "bbox": [x0,y0,x1,y1], "score": 0.98}

跑法：
    python tools/ocr_dump.py            # 全部
    python tools/ocr_dump.py --only 001 # 只跑 001
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data" / "eval" / "images"
OUT_DIR = ROOT / "data" / "eval" / "_ocr"
SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_engine = None


def engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _engine = RapidOCR()
    return _engine


def run(path: Path) -> List[dict]:
    result, _elapsed = engine()(str(path))
    if not result:
        return []
    cands: List[dict] = []
    for item in result:
        try:
            pts, text, score = item[0], item[1], float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        text = str(text).strip()
        if not text or score < 0.5:
            continue
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        cands.append({
            "text": text,
            "bbox": [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))],
            "score": round(score, 3),
        })
    cands.sort(key=lambda c: (c["bbox"][1], c["bbox"][0]))
    return cands


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只跑文件名包含该串的图")
    parser.add_argument("--force", action="store_true", help="忽略缓存，强制重跑")
    args = parser.parse_args(argv)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in SUFFIXES)
    if args.only:
        files = [p for p in files if args.only in p.name]

    print(f"共 {len(files)} 张图\n")
    t0 = time.perf_counter()
    for i, p in enumerate(files, 1):
        out = OUT_DIR / f"{p.stem}.json"
        if not args.force and out.exists() and out.stat().st_size > 2:
            print(f"[{i}/{len(files)}] {p.name}  已有缓存，跳过")
            continue
        cands = run(p)
        out.write_text(json.dumps(cands, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[{i}/{len(files)}] {p.name}  {len(cands)} 个候选")

    print(f"\n耗时 {time.perf_counter() - t0:.1f}s，结果在 {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
