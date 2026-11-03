# -*- coding: utf-8 -*-
"""跑评测后，把失败用例导出成 data/eval/_failed_cases.json，供 annotate_failed.py 使用。

用法：
    python tools/collect_failed.py
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GT = REPO / "data" / "eval" / "gt.jsonl"
PRED = REPO / "data" / "eval" / "pred.jsonl"
OUT = REPO / "data" / "eval" / "_failed_cases.json"


def center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def inside(box, pt):
    x0, x1 = sorted([box[0], box[2]])
    y0, y1 = sorted([box[1], box[3]])
    return x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1


# ---------------------------------------------------------------------------
# 真值框内是否有 OCR 文字 —— 用来区分「无文字图标」和「有文字按钮」
#
# 数据源：tools/ocr_dump.py 产出的 OCR 缓存。缓存不存在时降级为
# 「所有失败都算需要人工看」，不做错误分类。
# ---------------------------------------------------------------------------
OCR_CACHE = REPO / "data" / "eval" / "_ocr" / "ocr.json"
_OCR = None


def _load_ocr():
    global _OCR
    if _OCR is not None:
        return _OCR
    if OCR_CACHE.exists():
        raw = json.loads(OCR_CACHE.read_text(encoding="utf-8"))
    else:
        # 兼容标注工作目录里的缓存
        alt = Path(r"E:\dsh workspace\_ann\ocr.json")
        raw = json.loads(alt.read_text(encoding="utf-8")) if alt.exists() else {}
    norm = {}
    for k, v in raw.items():
        k2 = k.replace("\\", "/")
        if not k2.startswith(("Chat on WeChat/", "Medical Interpreting/")):
            k2 = "Medical Interpreting/" + k2
        norm[k2] = v
    _OCR = norm
    return _OCR


def gt_texts(gt_file: str, boxes) -> list:
    """返回落在任一真值框中心区域的 OCR 文字。"""
    rel = gt_file[len("images/"):] if gt_file.startswith("images/") else gt_file
    items = _load_ocr().get(rel, [])
    out = []
    for box in boxes:
        x0, y0, x1, y1 = box
        for it in items:
            b = it["box"]
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                t = it["text"].strip()
                if t:
                    out.append(t)
    return list(dict.fromkeys(out))


def main() -> int:
    if not PRED.exists():
        raise SystemExit(f"[FATAL] 找不到 {PRED}，先跑 tools/run_eval.py")

    gt = {}
    for line in GT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = (r["file"], r["task_id"], int(r["step"]), r["goal"])
        gt.setdefault(k, []).append(r["bbox"])

    preds = [json.loads(l) for l in PRED.read_text(encoding="utf-8").splitlines() if l.strip()]

    icon_cases, text_cases = [], []
    for p in preds:
        k = (p.get("file"), p.get("task_id"), int(p.get("step", 1)), p.get("goal"))
        boxes = gt.get(k, [])
        if not boxes:
            continue
        err = p.get("error")
        ok = (not err) and any(inside(b, center(p["bbox"])) for b in boxes)
        if ok:
            continue
        rec = {
            "task_id": k[1],
            "step": k[2],
            "goal": k[3],
            "file": k[0],
            "gt_boxes": boxes,
            "model_bbox": p.get("bbox"),
            "model_text": p.get("text"),
            "model_error": err,
            "gt_text": gt_texts(k[0], boxes),
        }
        # 判断依据是「真值框里有没有 OCR 文字」，不是「模型选中了什么」
        if gt_texts(k[0], boxes):
            text_cases.append(rec)
        else:
            icon_cases.append(rec)

    OUT.write_text(
        json.dumps({"icon": icon_cases, "text": text_cases}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    n_files = len({c["file"] for c in icon_cases + text_cases})
    print(f"失败 {len(icon_cases) + len(text_cases)} 条，涉及 {n_files} 张图")
    print(f"  无文字图标 {len(icon_cases)} 条 / 有文字 {len(text_cases)} 条")
    print(f"写入 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
