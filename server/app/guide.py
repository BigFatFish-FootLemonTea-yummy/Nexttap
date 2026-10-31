"""任务路径检索与风险判断。

纯逻辑，只用标准库，可以脱离 OCR / 模型 / FastAPI 单独测试
（见 server/tests/test_guide.py）。

数据来源：data/rag/paths.jsonl（120 条任务链）。

关于检索质量：这是 RAG 的第一层（词面召回）。中文短句天然稀疏，
"退休金"和"养老金"一个字都不重合，所以这里做了两件事——
去掉口语填充词、按小词典做同义扩展。它不追求语义理解，
只是把候选收敛到 3 条，真正的语义判断交给后面的模型。
"""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional

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
}

_cache: Optional[List[Dict[str, Any]]] = None


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
    return _cache


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


def search(query: str, top_k: int = 3, min_score: float = 0.42) -> List[Dict[str, Any]]:
    """按口语查询检索任务路径。

    词面召回的定位是「把候选收敛到 3 条」，不负责语义判断。
    以后接向量检索与 rerank，只需替换本函数内部排序，上层无感。
    """
    query_n = _clean(query)
    if not query_n:
        return []

    scored = []
    for path in load_paths():
        score = _score(query, path)
        if score >= min_score:
            scored.append((score, path))

    scored.sort(key=lambda item: item[0], reverse=True)

    results: List[Dict[str, Any]] = []
    for score, path in scored[: max(1, top_k)]:
        results.append({
            "task_id": path.get("task_id", ""),
            "name": path.get("name", ""),
            "app": path.get("app", ""),
            "score": round(score, 3),
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
