"""单张截图的定位管线：OCR → 候选合并 → 模型选目标 → 加 padding → 归一化。

注意：这段逻辑与 `api/routes.py` 里的 `/api/locate` 是同一套，
目前两处各写了一遍（routes 那边要转成 HTTPException）。
改判据时两边都要改，否则网页和 Agent 的结果会不一致。

返回统一的 dict，不抛异常——调用方（Agent / MCP）需要拿到"失败原因"而不是栈。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from PIL import Image

from ..llm.select import choose
from .candidates import expand_to_tappable, merge_lines, to_norm
from .ocr import run_ocr


def locate_image(image_path: str, goal: str) -> Dict[str, Any]:
    """在截图里定位目标元素。

    Args:
        image_path: 截图路径。
        goal: 用户口语目标，不能是按钮名（否则等于把答案告诉模型）。

    Returns:
        ok=True 时含 text / bbox_px / bbox_norm / confidence / instruction /
        candidate_count；ok=False 时含 error。
    """
    if not os.path.isfile(image_path):
        return {"ok": False, "error": f"图片不存在：{image_path}"}

    try:
        with Image.open(image_path) as image:
            width, height = image.size
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"图片无法解析：{exc}"}

    try:
        raw = run_ocr(image_path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"OCR 失败：{exc}"}

    if not raw:
        return {"ok": False, "error": "没有识别到任何文字，请对准屏幕重拍、避免反光"}

    candidates = merge_lines(raw)

    try:
        decision = choose(candidates, goal, width, height)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"模型调用失败：{exc}"}

    index = decision.get("id")
    if index is None:
        return {
            "ok": False,
            "error": decision.get("instruction") or "看不出该点哪里，请换一张更清楚的图",
        }

    try:
        index = int(index)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"模型返回了非整数编号：{index!r}"}

    if not 0 <= index < len(candidates):
        return {"ok": False, "error": "模型返回了越界的候选编号"}

    target = candidates[index]
    box = expand_to_tappable(target.box, [c.box for c in candidates], width, height)

    return {
        "ok": True,
        "text": target.text,
        "bbox_px": box.as_list(),
        "bbox_norm": to_norm(box, width, height),
        "confidence": decision["confidence"],
        "instruction": decision["instruction"],
        "candidate_count": len(candidates),
    }
