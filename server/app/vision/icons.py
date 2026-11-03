"""无文字图标的检测与标注。

## 为什么需要这个

`pipeline.py` 只把 OCR 认出的**文字**当候选，模型只能从候选里挑。
于是「微信右上角加号」「搜索镜」「相机快门」这类纯图标永远进不了候选列表，
用户说「想扫码付钱」时模型只能回「屏幕上没有这个按钮」——
不是它判断错，是它根本没看见那个按钮。

这个模块把图标也变成候选，并给它一个有区分度的语义标签。

## 实现要点（都是踩过的坑）

1. **不缩图**。1/2 分辨率下细线图标（加号、搜索镜）笔画被插值糊断，
   连通域裂成几段，全部漏检。
2. **全图一次连通域**，不按格子切。按格子切会把同一个图标拆成多块，
   产生几十个重复候选。
3. **用 cv2.connectedComponentsWithStats**。纯 numpy 迭代传播要 100 秒/张，
   cv2 是毫秒级。cv2 随 RapidOCR 一起装，不是新依赖。
4. **先做形态学闭运算**再找连通域，把断开的笔画连上，
   否则加号会被拆成「横」和「竖」两块。

## 已知局限

- 只覆盖「深色图标画在浅色背景」；深色模式走反向分支
- 靠几何特征分类，美术风格特殊的图标会落到 `<图标按钮>`
- 图标密集区（九宫格）可能漏检相邻项
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from PIL import Image

from .candidates import Box, Candidate

# 尺寸约束（原图像素，按 1260x2800 手机截图调过）
MIN_SIDE = 30
# 上限放到 260：相机快门是 201x201 的大圆，原来的 200 刚好把它卡掉
MAX_SIDE = 260
MAX_ASPECT = 1.5
# 形态学闭运算核：把断开的笔画连起来（加号的横竖、搜索镜的圆+柄）
CLOSE_KERNEL = 5


@dataclass
class IconSpec:
    box: Box
    label: str
    confidence: float


def _classify(
    w: int,
    h: int,
    fill: float,
    inner_fill: float = -1.0,
    row_mid: float = -1.0,
    col_mid: float = -1.0,
    corner: float = -1.0,
    is_dark_scene: bool = False,
) -> Tuple[str, float]:
    """按几何特征贴标签。

    实测数据（`_ann/calibrate_icons.py` 标定）：
        图标          fill   inner   中行   中列   四角
        加号         0.24   0.28   0.619  0.619  0.21
        表情         0.24   0.28   0.619  0.619  0.21
        搜索镜       0.24   0.00   0.161  0.143  0.29
        喇叭         0.25   0.35   0.345  0.345  0.21
        快门         0.57   1.00   0.849  0.849  0.30

    关键发现：`fill` 全都是 0.24 左右，**完全没有区分度**（早先靠它分类
    导致加号被标成喇叭）。真正能分开的是「中行/中列 vs 四角」的关系：
      - 十字（加号/表情）：中行、中列都很高（>0.5），四角低
      - 搜索镜：中行、中列都低，四角反而更高（圆环在角上）
      - 喇叭：中行、中列中等（0.3~0.4），四角低
    """
    aspect = max(w, h) / max(1, min(w, h))
    if not (0.75 <= aspect <= 1.35):
        return "", 0.0

    # 实心圆 / 快门：整体填充率高。
    # **小尺寸**（<=150px）多半是功能按钮，收；
    # **大尺寸**（如 201px 的相机快门）只在深色取景页才收，
    # 否则会把头像、封面图、聊天气泡也当成按钮
    # （实测「长按说话」那页把头像误检成了圆形按钮）。
    if fill > 0.45:
        if max(w, h) <= 150:
            return "圆形按钮", 0.62
        if is_dark_scene:
            return "快门按钮", 0.68
        return "", 0.0

    # 有中行/中列数据时按十字特征判定
    if row_mid >= 0 and col_mid >= 0 and corner >= 0:
        # 十字：中行、中列都很高
        if row_mid > 0.50 and col_mid > 0.50:
            return "加号按钮", 0.72
        # 圆环：四角比中行高（环在角上），中行/中列很低
        if corner > row_mid and corner > col_mid:
            return "搜索按钮", 0.62
        # 内部有实心块但中心不交叉 -> 喇叭 / 播放类
        if 0.25 <= row_mid <= 0.45 and inner_fill >= 0.30:
            return "喇叭按钮", 0.60
        if row_mid > 0 and row_mid < 0.25:
            return "空心按钮", 0.50

    # 兜底：老逻辑
    if fill < 0.26 and inner_fill < 0.28:
        return "加号按钮", 0.60
    if fill < 0.40:
        return "圆环按钮", 0.52
    return "图标按钮", 0.45


def detect_icons(
    image_path: str,
    existing: Sequence[Candidate] = (),
    dark_threshold: int = 120,
    max_icons: int = 8,
) -> List[IconSpec]:
    """检测图上的无文字图标。

    只保留最可能是「功能按钮」的若干个：
      - 优先尺寸接近典型按钮（40~120px）
      - 优先靠近屏幕边缘（顶部栏、底部栏）
      - 优先填充率高的（实心/清晰线条），碎笔画填充率低
    """
    try:
        import cv2
        import numpy as np
    except ImportError:  # pragma: no cover
        return []

    with Image.open(image_path) as im:
        arr = np.array(im.convert("L"), dtype=np.uint8)
    H, W = arr.shape

    # 前景 mask 的选择：
    #   - 常规界面（浅底深图标）：取深色前景
    #   - 相机取景页（黑底白图标）：取亮色前景
    # 早先试过「两种前景都跑」，结果浅底页面上大量浅灰元素被当成图标，
    # 候选列表充满噪音，hit@1 从 94.5% 掉到 89.1%。所以只取一套前景，
    # 用「有没有大面积纯黑区」来判断是不是相机/深色取景页。
    median = int(np.median(arr))
    dark_ratio = float((arr < 40).mean())      # 纯黑像素占比
    if dark_ratio > 0.10:
        # 相机取景页：黑底白图标（实测相机页 18.7%，普通界面 1~2%）
        primary = (arr > 200).astype(np.uint8)
    else:
        primary = (arr < dark_threshold).astype(np.uint8)
    masks = [primary]

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (CLOSE_KERNEL, CLOSE_KERNEL))
    text_boxes = [c.box for c in existing]
    scored: List[Tuple[float, IconSpec]] = []
    seen: List[Tuple[float, float]] = []

    for mask in masks:
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(closed, 8)

        for i in range(1, n):
            x, y, w, h, area = (int(v) for v in stats[i])
            if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE <= h <= MAX_SIDE):
                continue
            if max(w, h) / max(1, min(w, h)) > MAX_ASPECT:
                continue

            box = Box(float(x), float(y), float(x + w), float(y + h))
            # 与文字区域有任何明显重叠都排除。
            # 早先用 IoU>0.5 判定，结果「公众号」的「公」字被 OCR 框住的同时
            # 又被当成图标（两者 IoU 只有 0.4 左右），模型就去点那个假图标了。
            if any(box.iou(tb) > 0.15 for tb in text_boxes):
                continue
            if any(tb.contains_point(box.cx, box.cy) for tb in text_boxes):
                continue
            if box.y1 < H * 0.03 or box.y0 > H * 0.97:
                continue
            # 两个 mask 会重复检出同一个图标，按中心点去重
            if any(abs(box.cx - scx) < 18 and abs(box.cy - scy) < 18 for scx, scy in seen):
                continue

            sub = mask[y:y + h, x:x + w]
            raw = int(sub.sum())
            fill = raw / max(1.0, w * h)
            # 中心 40% 区域的前景占比
            ix0, iy0 = int(w * 0.3), int(h * 0.3)
            ix1, iy1 = int(w * 0.7), int(h * 0.7)
            inner = sub[iy0:iy1, ix0:ix1]
            inner_fill = float(inner.mean()) if inner.size else -1.0
            # 十字特征：中心行/列 vs 四角
            row_mid = float(sub[h // 2, :].mean()) if h > 0 else -1.0
            col_mid = float(sub[:, w // 2].mean()) if w > 0 else -1.0
            th, tw = max(1, h // 3), max(1, w // 3)
            corner = (float(sub[:th, :tw].mean()) + float(sub[:th, -tw:].mean())
                      + float(sub[-th:, :tw].mean()) + float(sub[-th:, -tw:].mean())) / 4

            label, conf = _classify(
                w, h, fill, inner_fill, row_mid, col_mid, corner,
                is_dark_scene=(dark_ratio > 0.10),
            )
            if not label:
                continue
            seen.append((box.cx, box.cy))

            edge = 1.0 if (box.cy < H * 0.15 or box.cy > H * 0.85) else 0.0
            size = max(w, h)
            size_score = 1.0 - abs(size - 70) / 70.0
            score = conf * 0.5 + edge * 0.3 + max(0.0, size_score) * 0.2
            scored.append((score, IconSpec(box=box, label=label, confidence=conf)))

    scored.sort(key=lambda t: -t[0])
    return [spec for _, spec in scored[:max_icons]]


def merge_icons_into_candidates(
    cands: List[Candidate],
    icons: Sequence[IconSpec],
    max_icons: int = 12,
    img_w: int = 0,
    img_h: int = 0,
) -> List[Candidate]:
    """把图标追加到候选列表并重编号。

    **标签必须带方位**。早先只给 `<加号按钮>`，结果 8 个候选里有 5 个叫这个名字，
    模型分不清哪个是右上角那个，照样选错。现在写成 `<右上角加号按钮>`，
    模型就能靠「右上角」这三个字区分。
    """
    merged = list(cands)
    base = len(merged)

    def band(cx: float, cy: float) -> str:
        if img_w and img_h:
            rx, ry = cx / img_w, cy / img_h
            v = "顶部" if ry < 0.15 else ("上部" if ry < 0.35 else
                                         "中部" if ry < 0.65 else
                                         "下部" if ry < 0.85 else "底部")
            hz = "靠左" if rx < 0.33 else ("中间" if rx < 0.67 else "靠右")
            # 「右上角」「左下角」这种说法老人最熟
            if v in ("顶部", "底部") and hz in ("靠左", "靠右"):
                return ("右上角" if (v == "顶部" and hz == "靠右")
                        else "左上角" if (v == "顶部" and hz == "靠左")
                        else "右下角" if (v == "底部" and hz == "靠右")
                        else "左下角")
            return f"{v}{hz}"
        return ""

    for i, icon in enumerate(icons[:max_icons]):
        loc = band(icon.box.cx, icon.box.cy)
        label = f"<{loc}{icon.label}>" if loc else f"<{icon.label}>"
        # 同一个标签只保留一次：两个 `<右下角加号按钮>` 会让模型分不清选哪个
        # （实测：微信聊天页的喇叭和加号都被判成加号，模型选了左边那个）
        if any(c.text == label for c in merged):
            continue
        merged.append(
            Candidate(
                idx=len(merged),
                text=label,
                box=icon.box,
                score=icon.confidence,
            )
        )
    for i, c in enumerate(merged):
        c.idx = i
    return merged
