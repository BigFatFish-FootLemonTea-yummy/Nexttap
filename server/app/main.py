"""FastAPI 入口。

本地：uvicorn app.main:app --reload --port 8000
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import agent, guide
from .api.routes import router
from .config import settings
from .vision.ocr import _engine_instance

logger = logging.getLogger("nexttap")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时预热 OCR 与检索模型。

    把模型加载的 5–10 秒从"第一次请求"挪到"服务启动"，避免首个用户等到超时。
    预热失败不阻塞启动（例如依赖没装），只是首次调用会变慢。

    两处预热都要做：
      - OCR：RapidOCR，约 5–10 秒
      - 检索：bge-small-zh（ONNX），约 7–10 秒；不预热的话第一次
        search_guide 会从 20ms 变成 10s，MCP 客户端很可能直接超时。
    """
    try:
        logger.info("正在预热 OCR ...")
        _engine_instance()
        logger.info("OCR 预热完成")
    except Exception as exc:
        logger.warning("OCR 预热失败，首次定位会明显变慢：%s", exc)

    try:
        logger.info("正在预热检索模型 ...")
        guide.warmup()
    except Exception as exc:
        logger.warning("检索预热失败，首次检索会变慢：%s", exc)

    yield


app = FastAPI(title="指一下 · NextTap API", version="0.1.0", lifespan=lifespan)

# 开发期放开跨域；部署时由 Nginx 同域反代，不依赖 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


class AgentRunIn(BaseModel):
    """多步办事 Agent 的入参。

    image_ids   —— 前端上传后拿到的 id，按步骤顺序给（Web 用这个）
    image_paths —— 本地绝对路径，MCP / 命令行调试用
    task_id     —— 可选，强制指定任务链
    """

    goal: str = Field(..., min_length=1, max_length=100)
    image_ids: List[str] = Field(default_factory=list)
    image_paths: List[str] = Field(default_factory=list)
    task_id: str = ""


def _resolve_image_id(image_id: str) -> Optional[str]:
    """把上传返回的 image_id 还原成本地路径；找不到返回 None。"""
    if not image_id or not image_id.isalnum():
        return None
    for suffix in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = settings.screenshots_dir / f"{image_id}{suffix}"
        if candidate.exists():
            return str(candidate)
    return None


@app.post("/api/agent/run")
def agent_run(body: AgentRunIn) -> dict:
    """多步办事：检索路径 → 逐步定位 → 风险门禁 → 出步骤链。

    返回里的 trace 记录每一步的决策，前端可直接展示"它刚才想了什么"。
    """
    paths = list(body.image_paths)
    missing: List[str] = []
    for image_id in body.image_ids:
        resolved = _resolve_image_id(image_id)
        if resolved:
            paths.append(resolved)
        else:
            missing.append(image_id)

    if missing:
        raise HTTPException(status_code=404, detail=f"这些截图不存在：{missing}")

    return agent.run(body.goal, paths, task_id=body.task_id)


@app.get("/health")
def health() -> dict:
    retrieval = guide.retrieval_status()
    return {
        "ok": True,
        "model": settings.llm_model,
        "llm_key_set": bool(settings.llm_api_key),
        # 一眼可见的检索档位：hybrid_rerank / hybrid / lexical
        "retrieval_mode": retrieval["effective"],
        "retrieval": retrieval,
    }
