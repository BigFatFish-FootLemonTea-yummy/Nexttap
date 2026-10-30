"""给评测图打码：遮盖隐私信息，同时保持 OCR 结果可预测。

设计原则：
    1. 用纯色块覆盖，不用模糊/马赛克 —— 模糊后 OCR 可能仍识别出部分字，
       产生"半截候选"，评测结果不可控
    2. 只遮 PII 区域，不动其他像素 —— 保证已标注的框位置仍然有效
    3. 先备份原图，可随时还原

跑法：
    python tools/redact.py --scan            # 只扫描，列出要遮的区域
    python tools/redact.py --dry-run         # 生成预览图，不改原图
    python tools/redact.py --apply           # 真正打码（会先备份）
    python tools/redact.py --restore         # 从备份还原
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data" / "eval" / "images"
OCR_DIR = ROOT / "data" / "eval" / "_ocr"
BACKUP = ROOT / "data" / "eval" / "images_original"
PREVIEW = ROOT / "data" / "eval" / "_redact_preview"

# 纯色块的颜色：中性灰，和大多数界面背景接近，不会引入新的视觉干扰
FILL = (200, 200, 200)

# --------------------------------------------------------------------------
# PII 识别规则
# --------------------------------------------------------------------------
PATTERNS: Dict[str, re.Pattern] = {
    "手机号明文": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "手机号掩码": re.compile(r"1[3-9]\d\*{3,}\d{2,4}"),
    "身份证明文": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "身份证掩码": re.compile(r"\d{6}\*{6,}\d{0,4}"),
    "银行卡号": re.compile(r"(?<!\d)\d{16,19}(?!\d)"),
    "邮箱": re.compile(r"[\w.-]+@[\w.-]+\.\w{2,}"),
    "姓名冒号": re.compile(r"姓名[：:]\s*[\u4e00-\u9fa5]{2,4}"),
    "掩码姓名": re.compile(r"^\*[\u4e00-\u9fa5]{1,3}$"),
}

# 手动追加：OCR 没识别出来但肉眼可见的隐私
MANUAL: List[Tuple[str, List[int], str]] = [
    # (文件名, bbox, 说明)  —— 目前 OCR 已经全部识别到了，留空
]


def load_ocr(stem: str) -> List[dict]:
    f = OCR_DIR / f"{stem}.json"
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def find_pii(stem: str) -> List[Tuple[List[int], str, str]]:
    """返回 [(bbox, 命中的文字, 规则名)]。"""
    out: List[Tuple[List[int], str, str]] = []
    for cand in load_ocr(stem):
        text = str(cand.get("text", "")).strip()
        if not text:
            continue
        for kind, pat in PATTERNS.items():
            if pat.search(text):
                out.append((list(cand["bbox"]), text, kind))
                break
    return out


def pad_box(box: List[int], w: int, h: int, pad: int = 8) -> List[int]:
    x0, y0, x1, y1 = box
    return [max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)]


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测图打码")
    parser.add_argument("--scan", action="store_true", help="只扫描并列出")
    parser.add_argument("--dry-run", action="store_true", help="生成预览，不改原图")
    parser.add_argument("--apply", action="store_true", help="真正打码（先备份）")
    parser.add_argument("--restore", action="store_true", help="从备份还原")
    parser.add_argument("--fill", default="", help="填充色，如 200,200,200 或 white")
    args = parser.parse_args(argv)

    if args.restore:
        if not BACKUP.exists():
            raise SystemExit(f"没有备份目录：{BACKUP}")
        n = 0
        for p in BACKUP.iterdir():
            if p.is_file():
                shutil.copy2(p, IMAGES / p.name)
                n += 1
        print(f"已从备份还原 {n} 张图")
        return 0

    fill = FILL
    if args.fill:
        if args.fill.lower() == "white":
            fill = (255, 255, 255)
        elif args.fill.lower() == "gray":
            fill = (200, 200, 200)
        else:
            parts = [int(v) for v in args.fill.split(",")]
            if len(parts) != 3:
                raise SystemExit("--fill 格式应为 R,G,B")
            fill = tuple(parts)  # type: ignore

    imgs = sorted(p for p in IMAGES.iterdir() if p.suffix.lower() in {".jpg", ".png", ".jpeg", ".webp"})
    plan: List[Tuple[Path, List[Tuple[List[int], str, str]]]] = []
    for p in imgs:
        pii = find_pii(p.stem)
        pii += [(box, "", note) for f, box, note in MANUAL if f == p.name]
        if pii:
            plan.append((p, pii))

    total = sum(len(v) for _, v in plan)
    print("=" * 72)
    print(f"扫描 {len(imgs)} 张图，发现 {len(plan)} 张含隐私，共 {total} 处")
    print("=" * 72)
    for p, items in plan:
        with Image.open(p) as im:
            w, h = im.size
        print(f"\n{p.name}  ({w}x{h})")
        for box, text, kind in items:
            print(f"   [{kind:8}] \"{text}\"  bbox={box}")

    if not plan:
        print("\n没有需要打码的内容")
        return 0

    if args.scan:
        return 0

    if args.dry_run:
        PREVIEW.mkdir(parents=True, exist_ok=True)
        for p, items in plan:
            im = Image.open(p).convert("RGB")
            w, h = im.size
            d = ImageDraw.Draw(im)
            for box, _, _ in items:
                d.rectangle(pad_box(box, w, h), fill=fill)
            im.save(PREVIEW / p.name)
        print(f"\n预览已存到 {PREVIEW}（原图未改）")
        return 0

    if not args.apply:
        print("\n请指定 --scan / --dry-run / --apply 之一")
        return 1

    # 备份
    if not BACKUP.exists():
        BACKUP.mkdir(parents=True, exist_ok=True)
        for p in imgs:
            shutil.copy2(p, BACKUP / p.name)
        print(f"\n已备份 {len(imgs)} 张原图到 {BACKUP}")
    else:
        print(f"\n备份目录已存在，跳过备份：{BACKUP}")

    # 打码
    done = 0
    for p, items in plan:
        im = Image.open(p).convert("RGB")
        w, h = im.size
        d = ImageDraw.Draw(im)
        for box, _, _ in items:
            d.rectangle(pad_box(box, w, h), fill=fill)
        # 用高质量保存，避免打码后图片整体变糊
        im.save(p, quality=95, subsampling=0)
        done += 1
    print(f"已打码 {done} 张图")

    # 提醒后续步骤
    print()
    print("!" * 72)
    print("!! 打码后必须重新跑 OCR 和自动标注，否则标注和图像对不上")
    print("!! 依次执行：")
    print("!!   python tools/ocr_dump.py --force")
    print("!!   python tools/auto_annotate.py")
    print("!!   python tools/sync_paths.py")
    print("!!   python tools/quality_tag.py")
    print("!" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
