"""RapidOCR 封装。

延迟导入：没有装 OCR 依赖时，也能 import 其它模块（单测跑得起来）。
"""

from __future__ import annotations

from typing import List

from .candidates import Box, Candidate

_engine = None


def _engine_instance():
    global _engine
    if _engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError(
                "缺少 rapidocr-onnxruntime，请先执行 pip install -r requirements.txt"
            ) from exc
        _engine = RapidOCR()
    return _engine


def run_ocr(image_path: str, min_score: float = 0.5) -> List[Candidate]:
    """识别整张图，返回逐行候选（idx 暂时无意义，merge_lines 会重建）。"""
    result, _elapse = _engine_instance()(image_path)
    if not result:
        return []

    cands: List[Candidate] = []
    for item in result:
        # RapidOCR 返回 [points(4x2), text, score]
        try:
            pts, text, score = item[0], item[1], float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        text = str(text).strip()
        if not text or score < min_score:
            continue

        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        cands.append(
            Candidate(
                idx=len(cands),
                text=text,
                box=Box(min(xs), min(ys), max(xs), max(ys)),
                score=score,
            )
        )
    return cands
