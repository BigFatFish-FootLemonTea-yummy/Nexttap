"""候选元素构建与几何工具。

纯逻辑，无第三方依赖，可直接单测（见 tests/test_candidates.py）。
坐标约定：像素坐标为原图像素；归一化坐标为 0–1000。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return max(1.0, self.x1 - self.x0)

    @property
    def h(self) -> float:
        return max(1.0, self.y1 - self.y0)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def as_list(self) -> List[int]:
        return [
            int(round(self.x0)),
            int(round(self.y0)),
            int(round(self.x1)),
            int(round(self.y1)),
        ]

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def iou(self, other: "Box") -> float:
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        if inter <= 0:
            return 0.0
        union = self.w * self.h + other.w * other.h - inter
        return inter / union if union > 0 else 0.0


@dataclass
class Candidate:
    idx: int
    text: str
    box: Box
    score: float = 1.0

    def prompt_line(self, img_w: int, img_h: int) -> str:
        pos = vertical_band(self.box.cy, img_h)
        align = horizontal_band(self.box.cx, img_w)
        return (
            f'[{self.idx}] "{self.text}" | 位置:{pos}/{align} | '
            f"中心({int(self.box.cx)},{int(self.box.cy)}) | "
            f"尺寸{int(self.box.w)}x{int(self.box.h)}"
        )


def vertical_band(cy: float, img_h: int) -> str:
    r = cy / max(1, img_h)
    if r <= 0.15:
        return "顶部"
    if r <= 0.35:
        return "上部"
    if r <= 0.65:
        return "中部"
    if r <= 0.85:
        return "下部"
    return "底部"


def horizontal_band(cx: float, img_w: int) -> str:
    r = cx / max(1, img_w)
    if r <= 0.33:
        return "靠左"
    if r <= 0.66:
        return "居中"
    return "靠右"


def merge_lines(
    cands: Sequence[Candidate],
    gap_ratio: float = 0.6,
    y_tol_ratio: float = 0.5,
) -> List[Candidate]:
    """把同一行里被 OCR 切碎的文字拼回去（"视频" + "通话" -> "视频通话"）。"""
    if not cands:
        return []

    ordered = sorted(cands, key=lambda c: (c.box.cy, c.box.cx))
    rows: List[List[Candidate]] = []
    for c in ordered:
        for row in rows:
            if abs(row[-1].box.cy - c.box.cy) <= max(row[-1].box.h, c.box.h) * y_tol_ratio:
                row.append(c)
                break
        else:
            rows.append([c])

    merged: List[Candidate] = []
    for row in rows:
        row.sort(key=lambda c: c.box.cx)
        cur = row[0]
        for nxt in row[1:]:
            gap = nxt.box.x0 - cur.box.x1
            if gap <= max(cur.box.h, nxt.box.h) * gap_ratio:
                cur = Candidate(
                    idx=-1,
                    text=cur.text + nxt.text,
                    box=Box(
                        min(cur.box.x0, nxt.box.x0),
                        min(cur.box.y0, nxt.box.y0),
                        max(cur.box.x1, nxt.box.x1),
                        max(cur.box.y1, nxt.box.y1),
                    ),
                    score=min(cur.score, nxt.score),
                )
            else:
                merged.append(cur)
                cur = nxt
        merged.append(cur)

    merged.sort(key=lambda c: (c.box.cy, c.box.cx))
    for i, c in enumerate(merged):
        c.idx = i
    return merged


def pad_box(box: Box, img_w: int, img_h: int, factor: float = 0.35, min_pad: int = 6) -> Box:
    """把文字紧框扩成"可点区域"。

    老人会去点文字本身，实际要点的是整行/整张卡片；宁可大一圈。
    结果限制在画面内。
    """
    pad = max(float(min_pad), box.h * factor)
    return Box(
        x0=max(0.0, box.x0 - pad),
        y0=max(0.0, box.y0 - pad),
        x1=min(float(img_w), box.x1 + pad),
        y1=min(float(img_h), box.y1 + pad),
    )


def to_norm(box: Box, img_w: int, img_h: int) -> List[float]:
    """像素框 -> 0–1000 归一化坐标（前端按容器尺寸缩放）。"""
    w = max(1, img_w)
    h = max(1, img_h)
    return [
        round(box.x0 / w * 1000, 1),
        round(box.y0 / h * 1000, 1),
        round(box.x1 / w * 1000, 1),
        round(box.y1 / h * 1000, 1),
    ]


def from_norm(norm: Sequence[float], img_w: int, img_h: int) -> List[int]:
    """归一化坐标 -> 像素框，主要给离线评测用。"""
    w = max(1, img_w)
    h = max(1, img_h)
    return [
        int(round(norm[0] / 1000 * w)),
        int(round(norm[1] / 1000 * h)),
        int(round(norm[2] / 1000 * w)),
        int(round(norm[3] / 1000 * h)),
    ]


def expand_to_tappable(
    box: Box,
    others: Sequence[Box],
    img_w: int,
    img_h: int,
) -> Box:
    """把"文字紧框"扩成"可点区域"。

    OCR 给的是文字本身的紧框，但用户要点的是整块：
    中文 App 宫格里图标在上、文字在下；列表里则是整行卡片。
    只按字高外扩一点点，圈会死贴在文字上，老人会去点字。

    规则：
      - 左右各扩 0.5 倍字高
      - 向上最多扩 1.8 倍字高（把上方的图标含进来）
      - 但不能越过同一列里最近的元素，避免盖住上一行
    """
    h = box.h
    pad_x = max(8.0, 0.5 * h)

    x0 = max(0.0, box.x0 - pad_x)
    x1 = min(float(img_w), box.x1 + pad_x)

    up_edge = box.y0 - 1.8 * h
    for other in others:
        if other.y1 >= box.y0:
            continue
        # 只有水平方向有交叠才算"同一列"，否则会被旁边的元素误挡
        if other.x1 <= x0 or other.x0 >= x1:
            continue
        up_edge = max(up_edge, other.y1 + 0.35 * h)

    y0 = max(0.0, min(up_edge, box.y0))
    y1 = min(float(img_h), box.y1 + 0.6 * h)

    return Box(x0, y0, x1, y1)
