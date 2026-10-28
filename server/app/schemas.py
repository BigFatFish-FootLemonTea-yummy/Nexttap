"""请求与响应模型。"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field


class ScreenshotOut(BaseModel):
    image_id: str
    width: int
    height: int


class LocateIn(BaseModel):
    image_id: str = Field(..., min_length=8, max_length=64)
    goal: str = Field(..., min_length=1, max_length=100)


class LocateOut(BaseModel):
    # 命中的元素
    text: str
    # 像素坐标 [x0, y0, x1, y1]（已加 padding，对应"可点区域"）
    bbox_px: List[int]
    # 归一化坐标 0–1000
    bbox_norm: List[float]
    # 置信度与标记
    confidence: float
    low_confidence: bool
    # 给老人看的一句话
    instruction: str
    reason: str = ""
    candidate_count: int
    elapsed_ms: int
