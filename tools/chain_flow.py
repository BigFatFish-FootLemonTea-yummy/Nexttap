"""生成「预约挂号」链路的可视化流程图，供人工检查。

每步裁出标注框附近区域 + 缩略图，横向排成流程，直观看出：
    - 步骤顺序对不对
    - 框有没有框错元素
    - 有没有跳步 / 多步

跑法：
    python tools/chain_flow.py hospital.book_appointment
    python tools/chain_flow.py --all       # 所有多步链
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"
IMAGES = ROOT / "data" / "eval" / "images"
OUT = ROOT / "data" / "eval" / "_flow"

# 每步缩略图的目标宽度（高度按比例）
THUMB_W = 240
# 步骤之间的间隔
GAP = 18
# 顶部标题区高度
HEADER = 60
# 每步下方文字区高度
FOOTER = 96


def load_gt() -> List[dict]:
    return [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]


def font(size: int):
    try:
        return ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", size)
    except Exception:
        return ImageFont.load_default()


def build_chain(tid: str, rows: List[dict]) -> Path:
    steps = sorted([r for r in rows if r.get("task_id") == tid],
                   key=lambda r: int(r.get("step", 1) or 1))
    if not steps:
        raise SystemExit(f"没有找到链：{tid}")

    tiles = []
    for st in steps:
        p = IMAGES / Path(st["file"]).name
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB")
        w, h = im.size
        # 按比例缩到 THUMB_W 宽
        th = max(1, int(h * THUMB_W / w))
        thumb = im.resize((THUMB_W, th), Image.LANCZOS)

        # 在缩略图上画出标注框（按比例换算）
        x0, y0, x1, y1 = st["bbox"]
        s = THUMB_W / w
        d = ImageDraw.Draw(thumb)
        d.rectangle([x0 * s, y0 * s, x1 * s, y1 * s], outline=(0, 220, 255), width=3)

        tiles.append((thumb, st, im, th))

    if not tiles:
        raise SystemExit(f"{tid} 的图片都不存在")

    max_h = max(t[3] for t in tiles)
    total_w = len(tiles) * THUMB_W + (len(tiles) + 1) * GAP
    total_h = HEADER + max_h + FOOTER

    canvas = Image.new("RGB", (total_w, total_h), (250, 250, 252))
    d = ImageDraw.Draw(canvas)

    f_title = font(30)
    f_step = font(22)
    f_goal = font(24)
    f_meta = font(18)

    d.text((GAP, 14), f"{tid}   （{len(tiles)} 步）", fill=(20, 20, 20), font=f_title)

    x = GAP
    for thumb, st, _orig, th in tiles:
        y = HEADER
        canvas.paste(thumb, (x, y))
        # 边框
        d.rectangle([x, y, x + THUMB_W, y + th], outline=(180, 180, 190), width=1)

        # 步骤号
        d.rectangle([x, y - 26, x + 54, y], fill=(0, 150, 200))
        d.text((x + 8, y - 25), f"{st['step']}", fill=(255, 255, 255), font=f_step)

        # 下方文字
        fy = y + max_h + 8
        d.text((x, fy), st["goal"], fill=(20, 20, 20), font=f_goal)
        d.text((x, fy + 32), Path(st["file"]).name, fill=(110, 110, 120), font=f_meta)
        q = st.get("quality", "ok")
        if q != "ok":
            d.text((x, fy + 56), f"quality={q}", fill=(200, 60, 60), font=f_meta)

        # 箭头
        if x + THUMB_W + GAP < total_w - GAP:
            ax = x + THUMB_W + 3
            ay = HEADER + max_h // 2
            d.line([ax, ay, ax + GAP - 6, ay], fill=(120, 120, 130), width=3)
            d.polygon([(ax + GAP - 6, ay - 6), (ax + GAP - 6, ay + 6), (ax + GAP + 2, ay)],
                      fill=(120, 120, 130))

        x += THUMB_W + GAP

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{tid.replace('.', '_')}.png"
    canvas.save(path)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("chain", nargs="?", default="", help="task_id")
    parser.add_argument("--all", action="store_true", help="所有多步链")
    parser.add_argument("--min-steps", type=int, default=2)
    args = parser.parse_args(argv)

    rows = load_gt()

    if args.all:
        counts: Dict[str, int] = {}
        for r in rows:
            t = r.get("task_id", "")
            if t:
                counts[t] = counts.get(t, 0) + 1
        targets = [t for t, n in counts.items() if n >= args.min_steps]
        for t in sorted(targets):
            p = build_chain(t, rows)
            print(f"  {p.name}")
        print(f"\n共 {len(targets)} 张流程图，在 {OUT}")
        return 0

    if not args.chain:
        parser.error("需要指定 task_id 或 --all")

    p = build_chain(args.chain, rows)
    print(f"已生成：{p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
