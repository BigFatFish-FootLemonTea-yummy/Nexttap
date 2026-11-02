"""FastAPI 入口。

本地：uvicorn app.main:app --reload --port 8000
文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import guide
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
