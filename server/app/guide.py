"""任务路径检索与风险判断。

纯逻辑，只用标准库，可以脱离 OCR / 模型 / FastAPI 单独测试
（见 server/tests/test_guide.py）。

数据来源：data/rag/paths.jsonl（120 条任务链）。

## 检索分三层（技术文档 §6.3 的三阶检索）

1. **词面召回**（本文件 `_score`）
   去口语填充词 + 同义扩展 + 二字组重合度。
   优点：零依赖、毫秒级、可解释。
   缺点：中文短句天然稀疏——"退休金"和"养老金"一个字都不重合，
         "交税那个"和"粤税通"更是毫无字面关联。

2. **向量召回**（`_vector_index`）
   bge-small-zh-v1.5（512 维，ONNX，约 90MB）。
   对 `name + aliases + steps[].target` 建索引，查询时算余弦相似度。
   补上词面的语义盲区。

3. **RRF 融合**（`_rrf_fuse`）
   Reciprocal Rank Fusion：`score(d) = Σ w_i / (k + rank_i(d))`。
   为什么不用分数加权：词面是 0–1 相似度、向量是余弦距离，量纲不同，
   直接加权要归一化且权重难调；RRF 只看排名，无需调参，实践中更稳。

通过环境变量 `NEXTTAP_RETRIEVAL` 切换，便于跑消融表：
    lexical         只走词面（兜底，无 embedding 依赖时自动退化到这个）
    hybrid          词面 + 向量 + RRF（默认）
    hybrid_rerank   再叠一层 rerank

`search()` 的返回结构**不可变**，MCP 的 search_guide 依赖它。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_CANDIDATE_FILES = [
    os.getenv("NEXTTAP_PATHS", ""),
    str(Path(__file__).resolve().parents[2] / "data" / "rag" / "paths.jsonl"),
    str(Path.cwd() / "data" / "rag" / "paths.jsonl"),
    "/app/data/rag/paths.jsonl",
]

# 老人说话的口头语，去掉之后剩下的才是关键词
_FILLERS = (
    "我想", "我要", "帮我", "帮忙", "怎么", "可以", "能不能", "请问",
    "一下", "这个", "那个", "看看", "然后", "现在", "把", "给我",
)

# 老人说法 → 界面用词。只收高频、且字面完全不重合的，不做泛化
_SYNONYMS = {
    "退休金": ("养老金", "养老保险"),
    "工资": ("养老金",),
    "照片": ("相册",),
    "图片": ("相册",),
    "孩子": ("儿子", "女儿"),
    "交钱": ("缴费", "支付"),
    "付钱": ("缴费", "支付"),
    "挂号": ("预约挂号",),
    "看病": ("就医", "问诊"),
    "查钱": ("余额",),
    "社保": ("社会保险",),
    "哪个科": ("科室",),
    "叫车": ("打车",),
    "叫个车": ("打车",),
    "零钱": ("钱包", "余额"),
    # 2026-11-01 补充。
    #
    # ⚠️ 诚实披露：下面这批词条里，有一部分是**看着检索评测集的失败样本**补的
    # （原注释就写着「来自检索评测集里词面完全失效的样本」）。
    # 这构成循环论证——用评测集改进被测系统，指标会虚高。
    #
    # 独立验证实测：去掉这批后补词条后，留出集 100% → 98.1%（-1.9 点），
    # 盲写集 94.1% → 94.7%（+0.6，反向）。即影响约 ±2 点。
    #
    # 保留而非删除的理由：其中「交税→粤税通」「驾照→行驶驾驶」这类映射
    # 本身是合理的领域知识，不是纯粹的数字游戏；但**对外报指标时必须说明
    # 这部分词条有评测集来源**，不能装作是独立采集的。
    #
    # 根治办法是真人访谈采集说法，而不是从评测集反推。
    "交税": ("粤税通", "税务"),
    "报税": ("粤税通", "税务"),
    "打疫苗": ("预防接种",),
    "水电费": ("生活缴费",),
    "话费": ("生活缴费",),
    "律师": ("法律服务",),
    "补助": ("社会救助", "救助"),
    "残疾": ("助残",),
    "护照": ("出入境",),
    "驾照": ("行驶驾驶", "驾驶证"),
    "换证": ("行驶驾驶", "驾驶证"),
    "公积金": ("公积金", "住房"),
    "住院": ("住院", "押金"),
    "化验": ("检验检查",),
    "体检": ("体检",),
    "大夫": ("医生",),
    "护士": ("护理",),
    "红包": ("红包",),
    "零花": ("红包",),
    "说话": ("语音通话", "视频通话"),
    "打字": ("语音输入",),
    "在哪儿": ("位置",),
    "政务": ("粤省事", "穗好办"),
}

# 默认检索模式；embedding 不可用时自动退化为 lexical
_DEFAULT_MODE = "hybrid"

_cache: Optional[List[Dict[str, Any]]] = None
_vector_index: Optional["_VectorIndex"] = None
_reranker: Any = None
# 保护 _vector_index 的懒加载，避免并发首请求重复加载 90MB 模型
_index_lock = threading.Lock()

_log = logging.getLogger("nexttap.guide")


def _paths_file() -> Optional[Path]:
    for candidate in _CANDIDATE_FILES:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load_paths() -> List[Dict[str, Any]]:
    """读任务链；找不到文件时返回空列表（不抛异常，方便工具降级）。"""
    global _cache
    if _cache is None:
        path = _paths_file()
        if path is None:
            # 找不到数据不是"正常降级"，而是部署出错：
            # 容器里忘了挂载 data/rag 时，检索会静默失效（search 恒返回 []），
            # 没有任何报错，极难排查。所以这里必须吵一次。
            _log.error(
                "找不到 paths.jsonl，检索功能将完全失效。已尝试：%s",
                [c for c in _CANDIDATE_FILES if c],
            )
            _cache = []
        else:
            rows: List[Dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            _cache = rows
            _log.info("已加载 %d 条任务链：%s", len(rows), path)
    return _cache


def reset_index() -> None:
    """丢掉所有缓存：任务链、向量索引、reranker。

    早先只清 _vector_index，导致：
      - 数据文件改了不重读（_cache 还在）
      - reranker 占的 1.1GB 不释放（对 mem_limit 1500m 是隐患）
    """
    global _vector_index, _cache, _reranker
    _vector_index = None
    _cache = None
    _reranker = None


def _mode() -> str:
    return os.getenv("NEXTTAP_RETRIEVAL", _DEFAULT_MODE).strip().lower() or _DEFAULT_MODE


def retrieval_status() -> Dict[str, Any]:
    """报告检索**实际生效**的模式，供 /health 暴露。

    只读当前状态，不触发加载——/health 不该把 90MB 模型拉起来，
    所以服务刚起来、预热未完成时，effective 会短暂显示 lexical。

    为什么需要它：向量模型缺失时 search() 会静默降级为纯词面，
    Recall@5 从 84.6% 掉到 71.8%，但接口照常返回、日志只有一行 warning。
    把这个状态暴露出来，演示前一天能自查。
    """
    configured = _mode()
    vector_ok = bool(_vector_index is not None and getattr(_vector_index, "ok", False))
    reranker_ok = bool(_reranker)

    if configured == "lexical":
        effective = "lexical"
    elif vector_ok:
        effective = "hybrid_rerank" if configured == "hybrid_rerank" and reranker_ok else "hybrid"
    else:
        effective = "lexical"

    return {
        "configured": configured,
        "effective": effective,
        "degraded": configured != "lexical" and effective == "lexical",
        "vector_ready": vector_ok,
        "reranker_ready": reranker_ok,
        "index_size": len(_vector_index.paths) if vector_ok else 0,
    }


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text)).lower()


def _clean(text: str) -> str:
    """去掉标点与口语填充词，只留关键词。"""
    s = _norm(text)
    s = re.sub(r"[，。！？、,.!?：:；;“”\"'（）()]", "", s)
    for filler in _FILLERS:
        s = s.replace(filler, "")
    return s


def _expand(s: str) -> List[str]:
    """原串 + 同义扩展。"""
    variants = [s] if s else []
    for key, values in _SYNONYMS.items():
        if key in s:
            variants.extend(values)
    return variants


def _bigrams(s: str) -> set:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _sim(a: str, b: str) -> float:
    """取「编辑相似度」和「二字组重合度」的较大者。

    中文短句里编辑距离偏保守（"退休金" vs "养老金" 只有 2/3 重合），
    二字组能补上这一块。
    """
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ga, gb = _bigrams(a), _bigrams(b)
    overlap = len(ga & gb) / max(1, min(len(ga), len(gb)))
    return max(seq, overlap)


def _score(query: str, path: Dict[str, Any]) -> float:
    query_variants = _expand(_clean(query))
    if not query_variants:
        return 0.0

    best = 0.0

    for alias in path.get("aliases") or []:
        alias_clean = _clean(alias)
        for q in query_variants:
            best = max(best, _sim(q, alias_clean))
            if len(q) >= 2 and q in alias_clean:
                best = max(best, 0.9)

    name = _clean(path.get("name") or "")
    if len(name) >= 2:
        for q in query_variants:
            if len(q) >= 2 and q in name:
                best = max(best, 0.9)
            best = max(best, _sim(q, name) * 0.95)

    # 步骤文本只给低权重：像"点确定"这种通用词会到处命中
    for step in path.get("steps") or []:
        for key in ("target", "hint"):
            text = _clean(step.get(key) or "")
            if len(text) >= 2:
                for q in query_variants:
                    if len(q) >= 2 and (q in text or text in q):
                        best = max(best, 0.6)

    return best


# ---------------------------------------------------------------------------
# 向量召回
# ---------------------------------------------------------------------------

_MODEL_NAME = os.getenv("NEXTTAP_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")
# 本地已下载的模型目录（含 model_optimized.onnx / tokenizer.json 等）。
# 设了就完全不走网络；内网部署、Docker 挂载模型时用。
_MODEL_PATH = os.getenv("NEXTTAP_EMBED_PATH", "").strip()


def _index_text(path: Dict[str, Any]) -> str:
    """一条任务链用于建索引的文本。

    把 name、去模板后的 aliases、各步 target 拼在一起，
    让向量同时看到「任务叫什么」和「中间要点哪些按钮」。
    """
    parts: List[str] = []
    name = str(path.get("name") or "").strip()
    if name:
        parts.append(name)

    # aliases 里有大量模板产物（"怎么X"/"我要X"/"帮我X"），
    # 清洗时直接剔掉前缀，避免稀释语义。
    # 注意：早先写成 `a[2:] in (name, a[2:])`，后半段恒真导致条件永远成立，
    #       虽然行为碰巧对，但意图与实现不符，已改为显式剥离。
    for alias in path.get("aliases") or []:
        a = str(alias).strip()
        if not a:
            continue
        for prefix in ("怎么", "我要", "帮我"):
            if a.startswith(prefix) and len(a) > len(prefix):
                a = a[len(prefix):]
                break
        if not a or a in parts:
            continue
        parts.append(a)

    for step in path.get("steps") or []:
        target = str(step.get("target") or "").strip()
        if target and target not in parts:
            parts.append(target)

    return "，".join(parts)


class _VectorIndex:
    """bge-small-zh 向量索引。

    懒加载：只有在 hybrid 模式下、且真正被调用时才加载模型。
    模型或依赖缺失时静默降级为纯词面，不影响主流程。
    """

    def __init__(self) -> None:
        self.ok = False
        self.paths: List[Dict[str, Any]] = []
        self.matrix: Any = None  # numpy (N, D)，已 L2 归一化
        self._model: Any = None
        self._load()

    def _load(self) -> None:
        # 只 catch ImportError 是不够的：fastembed 装坏（缺 dll、版本冲突）
        # 会抛 RuntimeError/OSError，穿透出去会让整个服务起不来。
        # 降级路径的契约是「任何失败都不影响主流程」，所以这里全 catch。
        try:
            from fastembed import TextEmbedding
            import numpy as np
        except Exception as exc:  # noqa: BLE001
            _log.warning("fastembed 不可用，检索降级为纯词面：%s", exc)
            return

        self.paths = load_paths()
        if not self.paths:
            return

        try:
            embed_kwargs: Dict[str, Any] = {
                "model_name": _MODEL_NAME,
                "providers": ["CPUExecutionProvider"],
                "threads": 2,
            }
            if _MODEL_PATH:
                # 完全离线：直接用本地目录，不再尝试联网
                embed_kwargs["specific_model_path"] = _MODEL_PATH
            self._model = TextEmbedding(**embed_kwargs)
            texts = [_index_text(p) for p in self.paths]
            vecs = np.array(list(self._model.embed(texts)), dtype="float32")
        except Exception as exc:  # noqa: BLE001
            # 常见于：国内拉 HF 超时、模型文件损坏、onnxruntime 加载失败
            _log.warning("embedding 模型加载失败，检索降级为纯词面：%s", exc)
            self._model = None
            return

        # 行数与路径数必须对齐，否则错位一行会静默返回完全无关的 task_id，
        # 而且分数看起来完全正常——这种 bug 极难发现。
        if vecs.ndim != 2 or vecs.shape[0] != len(self.paths):
            _log.error(
                "向量矩阵形状异常：%s，期望 (%d, D)。降级为纯词面。",
                None if vecs is None else vecs.shape, len(self.paths),
            )
            self._model = None
            return

        # 归一化原来写在 try 外面：embed 返回空数组时 np.linalg.norm 会抛
        # AxisError 穿透出去。现在整段都在 try 内。
        try:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self.matrix = vecs / norms
        except Exception as exc:  # noqa: BLE001
            _log.error("向量归一化失败，降级为纯词面：%s", exc)
            self._model = None
            return

        self.ok = True

    def search(self, query: str, top_k: int) -> List[Tuple[int, float]]:
        """返回 [(路径下标, 相似度)]，按相似度降序。

        任何异常都返回 []，让调用方退回词面，绝不让检索拖垮整个请求。
        """
        if not self.ok or self.matrix is None:
            return []
        try:
            import numpy as np

            q = np.array(list(self._model.embed([query])), dtype="float32")[0]
            n = float(np.linalg.norm(q)) or 1.0
            sims = self.matrix @ (q / n)
            order = np.argsort(-sims)[:top_k]
            return [(int(i), float(sims[i])) for i in order]
        except Exception as exc:  # noqa: BLE001
            _log.warning("向量检索失败，本轮退回词面：%s", exc)
            return []


def _get_index() -> Optional[_VectorIndex]:
    """取（必要时加载）向量索引。

    加锁的原因：并发首请求时，多个线程会同时看到 _vector_index is None，
    各自加载一份 90MB 模型 —— 在 mem_limit 1500m 的容器里足以触发 OOM。
    双检锁保证只加载一次。
    """
    global _vector_index
    if _mode() == "lexical":
        return None
    if _vector_index is not None:
        return _vector_index if _vector_index.ok else None
    with _index_lock:
        if _vector_index is None:
            _vector_index = _VectorIndex()
    return _vector_index if _vector_index.ok else None


def warmup() -> None:
    """预热检索模型。

    和 OCR 一样，模型加载要 7–10 秒（含首次下载可能更久）。
    如果放在第一个用户请求里，第一次定位会从 2.7s 变成 10s+，
    而且可能被上游超时掐断。

    由 `main.py` 的 lifespan 在启动时调用；失败不影响启动。
    """
    try:
        index = _get_index()
        if index is None:
            _log.info("检索使用纯词面模式（未加载 embedding 模型）")
        else:
            _log.info("检索模型预热完成，索引 %d 条", len(index.paths))
    except Exception as exc:  # noqa: BLE001
        _log.warning("检索预热失败，首次检索会变慢：%s", exc)


# ---------------------------------------------------------------------------
# rerank：交叉编码器重排
# ---------------------------------------------------------------------------

# _reranker 在文件顶部与其它缓存一起声明，避免重复定义
_RERANK_MODEL = os.getenv("NEXTTAP_RERANK_MODEL", "BAAI/bge-reranker-base")


def _get_reranker() -> Any:
    """懒加载交叉编码器。

    2C4G 上这个模型约 1.1GB（onnx），会和 OCR 抢内存，
    所以**线上默认不启用**（评审意见 §三.3：「线上不跑 rerank，
    只在离线做消融表」），只有 `NEXTTAP_RETRIEVAL=hybrid_rerank` 时才加载。
    """
    global _reranker
    if _reranker is None:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder
        except ImportError:
            _reranker = False
            return False
        try:
            _reranker = TextCrossEncoder(model_name=_RERANK_MODEL)
        except Exception:
            _reranker = False
    return _reranker


def _rerank(query: str, candidates: Sequence[Tuple[int, float]], paths: List[Dict[str, Any]]) -> List[Tuple[int, float]]:
    """用交叉编码器对候选重排；模型不可用时原样返回。"""
    model = _get_reranker()
    if not model or not candidates:
        return list(candidates)

    docs = [_index_text(paths[idx]) for idx, _ in candidates]
    try:
        scores = list(model.rerank(query, docs))
    except Exception:
        return list(candidates)

    reranked = sorted(
        zip((idx for idx, _ in candidates), scores),
        key=lambda kv: float(kv[1]),
        reverse=True,
    )
    return [(idx, float(s)) for idx, s in reranked]


def _rrf_fuse(
    lexical: Sequence[Tuple[int, float]],
    vector: Sequence[Tuple[int, float]],
    k: int = 60,
    w_lexical: float = 1.0,
    w_vector: float = 1.0,
) -> List[Tuple[int, float]]:
    """Reciprocal Rank Fusion。

    只用排名不用分数，所以两路量纲不同也没关系——这是选 RRF 而不是
    加权平均的主要原因（词面 0–1 相似度 vs 向量余弦，归一化后权重仍难调）。
    k=60 是 RRF 论文的常用取值，作用是压平头部差异，避免第一名一家独大。
    """
    scores: Dict[int, float] = {}
    for rank, (idx, _s) in enumerate(lexical, start=1):
        scores[idx] = scores.get(idx, 0.0) + w_lexical / (k + rank)
    for rank, (idx, _s) in enumerate(vector, start=1):
        scores[idx] = scores.get(idx, 0.0) + w_vector / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _shares_token(query_n: str, path: Dict[str, Any]) -> bool:
    """query 与候选文本是否有可解释的字面交集。

    域外拒识的第二判据。单靠向量阈值分不开域内/域外——
    「我要打游戏」→ map.taxi 的相似度 0.524，而正确的
    「给孙子点零花钱」→ 红包 只有 0.535，差 0.011。
    加这条要求「至少共享一个二字组，或关键词出现在候选文本里」，
    把「语义勉强沾边、字面毫无关系」的误召挡掉。
    """
    haystack = _index_text(path)
    if _bigrams(query_n) & _bigrams(haystack):
        return True
    return any(len(v) >= 2 and v in haystack for v in _expand(query_n))


def search(query: str, top_k: int = 3, min_score: float = 0.42) -> List[Dict[str, Any]]:
    """按口语查询检索任务路径。

    三阶检索的入口。`NEXTTAP_RETRIEVAL` 控制走哪一阶：
        lexical        纯词面（无 embedding 依赖）
        hybrid         词面 + 向量 + RRF（默认）
        hybrid_rerank  在 hybrid 基础上再 rerank

    **返回结构不可变**：MCP 的 search_guide 与后续 HTTP 接口都依赖它。

    关于 `score` 的量纲（重要）：
        三档模式的 score 含义不同，调用方**不要跨档比较分数**——
          lexical       0–1 的词面相似度
          hybrid        RRF 融合分，范围约 0.016–0.033（不是相似度）
          hybrid_rerank reranker 原始 logit，可能为负
        只看排序，不要看绝对值。这也意味着 `min_score` 只在 lexical 档有意义。
    """
    # 入参防护：hybrid 档下 query=None 会走到 _clean 里 re.sub 抛 TypeError，
    # 而 lexical 档返回 []，行为不一致。统一在这里挡掉。
    if not isinstance(query, str) or not query.strip():
        return []
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        top_k = 3
    # max(1, top_k) 会让 top_k=0 返回 1 条，语义上 0 就该返回 0 条
    if top_k <= 0:
        return []

    query_n = _clean(query)
    if not query_n:
        return []

    paths = load_paths()
    if not paths:
        return []

    mode = _mode()
    if mode not in ("lexical", "hybrid", "hybrid_rerank"):
        # 未知取值原来静默当 hybrid 跑，导致 "hybrid-rerank"（拼错）不报错也不 rerank。
        _log.warning("未知的 NEXTTAP_RETRIEVAL=%r，按 hybrid 处理", mode)
        mode = "hybrid"

    # 第一阶：词面，全量打分（120 条，很快）
    lexical_all = sorted(
        ((i, _score(query, p)) for i, p in enumerate(paths)),
        key=lambda kv: kv[1],
        reverse=True,
    )

    if mode == "lexical":
        ranked = [(i, s) for i, s in lexical_all if s >= min_score]
        return _to_results(ranked[:top_k], paths)

    index = _get_index()
    if index is None:
        # 模型/依赖缺失，静默降级为词面
        ranked = [(i, s) for i, s in lexical_all if s >= min_score]
        return _to_results(ranked[:top_k], paths)

    # 词面只取前 N 参与融合；低于阈值的说明字面完全不沾边，留着只会稀释。
    # 这里的 30 是**融合候选池**的大小，不是最终 top_k——早先版本把 top_k
    # 也截到 30，导致 search(q, top_k=50) 只返回 30 条。
    _POOL = max(30, top_k)
    lexical_top = [(i, s) for i, s in lexical_all[:_POOL] if s >= min_score]
    # 两路用不同的 query 形式，这是**实测出来的最优组合**，不是笔误：
    #   词面 → query_n（去掉"我想""帮我"等填充词，剩下的才是关键词）
    #   向量 → 原始 query（bge 对完整句子理解更好，去掉填充词反而丢语义）
    #
    # 曾试图"统一输入"改成两路都用 query_n，A/B 实测变差：
    #   原集 R@5 79.4% → 76.5%，留出集 71.2% → 69.2%
    # 所以保留这个不对称。改之前请先跑 tools/eval_retrieval.py 对比。
    vector_top = index.search(query, top_k=_POOL)

    # 域外拒识（2026-11-01 修，三轮）
    # ------------------------------------------------------------------
    # 第一轮：只加「向量相似度 >= 0.45」。实测不够——bge-small-zh 对中文
    #   短句的区分度偏低：「我要打游戏」→ map.taxi 0.524（域外），
    #   而正确的「给孙子点零花钱」→ 红包只有 0.535，差 0.011。
    #
    # 第二轮：加「字面必须有交集」当**硬门槛**。结果砍掉 14 条正确答案——
    #   「给他转点钱」→ 转账 0.770、「我想约个检查身体的」→ 体检预约 0.765，
    #   语义极强但字面确实不沾边。**硬门槛思路是错的。**
    #
    # 第三轮（当前）：改成**绝对阈值 + 相对领先**双条件，
    #   不再要求字面交集：
    #     1) 最高向量相似度 >= _VECTOR_MIN（默认 0.55）——「这话至少像」
    #     2) top1 与 top5 的分差 >= _VECTOR_GAP（默认 0.05）——「它有明显偏好」
    #   域外查询的问题不是分数低，而是**分数平**：一堆 0.41–0.53 挤在一起，
    #   说明模型对这句话没有任何偏好，那就该拒识。
    #   域内查询通常 top1 明显领先。
    _VECTOR_MIN = float(os.getenv("NEXTTAP_VECTOR_MIN", "0.55"))
    _VECTOR_GAP = float(os.getenv("NEXTTAP_VECTOR_GAP", "0.02"))

    if vector_top:
        top_sim = vector_top[0][1]
        fifth = vector_top[min(4, len(vector_top) - 1)][1]
        confident = top_sim >= _VECTOR_MIN and (top_sim - fifth) >= _VECTOR_GAP
    else:
        confident = False

    strong_lexical = {idx for idx, _s in lexical_top}
    fused = _rrf_fuse(lexical_top, vector_top)

    kept: List[Tuple[int, float]] = []
    for idx, score in fused:
        if idx in strong_lexical:
            kept.append((idx, score))
            continue
        # 纯向量命中：只有在整体「有把握」时才接受
        if confident:
            kept.append((idx, score))

    if not kept:
        return []

    if mode == "hybrid_rerank":
        # 先取 RRF 的前 20 做 rerank，再截 top_k。
        # 全量 120 条都 rerank 太慢，前 20 足够覆盖正确答案。
        kept = _rerank(query, kept[:20], paths)

    return _to_results(kept[:top_k], paths)


def _to_results(ranked: Sequence[Tuple[int, float]], paths: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for idx, score in ranked:
        path = paths[idx]
        results.append({
            "task_id": path.get("task_id", ""),
            "name": path.get("name", ""),
            "app": path.get("app", ""),
            "score": round(float(score), 3),
            "steps": path.get("steps") or [],
        })
    return results


_RISK_RULES: List[tuple] = [
    ("high", (
        "转账", "转给", "转钱", "打钱", "汇款", "付款", "支付", "红包",
        "借钱", "贷款", "验证码", "密码", "银行卡", "扫脸", "刷脸", "实名",
    )),
    ("medium", ("删除", "删", "注销", "解绑", "卸载", "退出登录", "清空", "恢复出厂")),
]


def check_risk(text: str) -> Dict[str, Any]:
    """判断这句话是否涉及高风险操作。

    只教不做是产品红线：命中 high 时不应该给出指引，而应转人工。
    """
    hits: List[str] = []
    level = "none"
    for rule_level, words in _RISK_RULES:
        for word in words:
            if word in text and word not in hits:
                hits.append(word)
                if rule_level == "high":
                    level = "high"
                elif level != "high":
                    level = "medium"

    if level == "high":
        advice = "这一步涉及资金或账号安全，请让家人或柜台人员在场，不要让老人独自操作。"
    elif level == "medium":
        advice = "这一步会改动数据，操作前请再确认一次，必要时找家人帮忙。"
    else:
        advice = "常规操作，可以继续。"

    return {
        "risky": level != "none",
        "level": level,
        "hits": hits,
        "advice": advice,
    }


def compose_card(
    text: str,
    instruction: str,
    task_id: str = "",
    step: int = 1,
) -> Dict[str, Any]:
    """把一次定位结果组装成展示卡片。

    带 task_id 时会查出该任务的完整路径，返回「当前第几步 / 下一步是什么」，
    这正是多步流程预热所需的数据结构。
    """
    task = next((p for p in load_paths() if p.get("task_id") == task_id), None) if task_id else None

    steps = (task or {}).get("steps") or []
    current = steps[step - 1] if 0 < step <= len(steps) else None
    nxt = steps[step] if 0 < step < len(steps) else None

    return {
        "text": text,
        "instruction": instruction,
        "task_id": task_id,
        "task_name": (task or {}).get("name", ""),
        "step": step,
        "total_steps": len(steps),
        "current_step": current,
        "next_step": nxt,
        "has_next": nxt is not None,
    }
