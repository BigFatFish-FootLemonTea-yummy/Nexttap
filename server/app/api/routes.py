"""HTTP 路由：上传截图与定位。"""

from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, HTTPException, UploadFile
from PIL import Image

from ..config import settings
from ..llm.select import choose
from ..schemas import LocateIn, LocateOut, ScreenshotOut
from ..vision.candidates import expand_to_tappable, merge_lines, to_norm
from ..vision.icons import detect_icons, merge_icons_into_candidates
from ..vision.ocr import run_ocr

logger = logging.getLogger("nexttap.routes")

router = APIRouter(prefix="/api", tags=["locate"])

ALLOWED_SUFFIX = {".png", ".jpg", ".jpeg", ".webp"}


def _find_screenshot(image_id: str) -> Path:
    # image_id 由 uuid4().hex 生成，只含字母数字，这里再校验一次防止目录穿越
    if not image_id.isalnum():
        raise HTTPException(status_code=400, detail="image_id 非法")

    for suffix in sorted(ALLOWED_SUFFIX):
        candidate = settings.screenshots_dir / f"{image_id}{suffix}"
        if candidate.exists():
            return candidate

    # 找不到时把排查信息直接放进报错，省得两头猜
    try:
        existing = sorted(p.name for p in settings.screenshots_dir.iterdir())[-10:]
    except OSError as exc:
        existing = [f"<目录读不了: {exc}>"]

    raise HTTPException(
        status_code=404,
        detail=(
            f"截图不存在：{image_id}｜"
            f"查找目录 {settings.screenshots_dir.resolve()}｜"
            f"最近文件 {existing}"
        ),
    )


@router.post("/screenshot", response_model=ScreenshotOut)
async def upload_screenshot(file: UploadFile = File(...)) -> ScreenshotOut:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIX:
        raise HTTPException(status_code=400, detail=f"不支持的格式：{suffix or '未知'}")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413, detail=f"图片超过 {settings.max_upload_mb}MB，请先压缩"
        )

    image_id = uuid.uuid4().hex
    settings.screenshots_dir.mkdir(parents=True, exist_ok=True)
    path = settings.screenshots_dir / f"{image_id}{suffix}"
    path.write_bytes(raw)

    try:
        with Image.open(path) as im:
            width, height = im.size
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="图片无法解析")

    return ScreenshotOut(image_id=image_id, width=width, height=height)


@router.post("/locate", response_model=LocateOut)
def locate(body: LocateIn) -> LocateOut:
    started = time.perf_counter()
    path = _find_screenshot(body.image_id)

    with Image.open(path) as im:
        img_w, img_h = im.size

    raw_candidates = run_ocr(str(path))
    if not raw_candidates:
        raise HTTPException(
            status_code=422,
            detail="没有识别到任何文字，请对准屏幕重拍，避免反光",
        )

    candidates = merge_lines(raw_candidates)

    # 与 pipeline.locate_image 保持一致：把无文字图标也加进候选。
    # 两处都要改，否则网页和 Agent 的结果会不一致（见 pipeline.py 顶部注释）。
    if os.getenv("NEXTTAP_ICONS", "1") != "0":
        try:
            icons = detect_icons(str(path), candidates)
            candidates = merge_icons_into_candidates(
                candidates, icons, img_w=img_w, img_h=img_h
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("图标检测失败，降级为纯文字候选：%s", exc)

    decision = choose(candidates, body.goal, img_w, img_h)

    idx = decision.get("id")
    if idx is None:
        raise HTTPException(
            status_code=422,
            detail=decision.get("instruction") or "看不出该点哪里，请换一张更清楚的图",
        )

    if not 0 <= int(idx) < len(candidates):
        raise HTTPException(status_code=502, detail="模型返回了越界的候选编号")

    target = candidates[int(idx)]
    # 图标候选的 text 形如 `<右上角加号按钮>`，走图标专用外扩规则
    box = expand_to_tappable(
        target.box,
        [c.box for c in candidates],
        img_w,
        img_h,
        is_icon=target.text.startswith("<"),
    )

    return LocateOut(
        text=target.text,
        bbox_px=box.as_list(),
        bbox_norm=to_norm(box, img_w, img_h),
        confidence=round(float(decision["confidence"]), 3),
        low_confidence=float(decision["confidence"]) < settings.min_confidence,
        instruction=decision["instruction"],
        reason=decision["reason"],
        candidate_count=len(candidates),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )
