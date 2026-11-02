FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# 预下载 embedding 模型，避免运行时去 HuggingFace 拉取。
# 国内网络经常拉不动，而 guide.py 拉不到只会静默降级为纯词面
# （Recall@5 从 84.6% 掉到 71.8%），接口照常返回，很难发现。
# 放在构建期：拉不下来会直接让 build 失败，而不是在演示当天才暴露。
ARG EMBED_MODEL=BAAI/bge-small-zh-v1.5
RUN python -c "from fastembed import TextEmbedding; \
    TextEmbedding(model_name='${EMBED_MODEL}')"

# 任务链数据不 COPY 进镜像，改为 compose 挂载：
#   ./data/rag:/app/data/rag:ro
# 理由：数据会随访谈更新，打进镜像每次都要重建；
#      而且构建上下文是 ./server，够不到仓库根的 data/。
#
# 挂载点 /app/data/rag 正好命中 guide.py 里 _CANDIDATE_FILES 的 cwd 候选
# （WORKDIR=/app，所以 Path.cwd()/data/rag/paths.jsonl = /app/data/rag/paths.jsonl）。
#
# 若忘记挂载，guide.py 会在启动时打 WARN 日志（见 _paths_file 的告警）。

EXPOSE 8000

# 2C4G 单机，固定 1 个 worker，避免多进程把内存翻倍
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
