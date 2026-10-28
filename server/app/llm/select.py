"""用语言模型在候选元素中选目标。

设计取舍见技术文档 2.3.4(1)：OCR 负责测量，模型负责判断。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Sequence

from ..config import settings
from ..vision.candidates import Candidate

SYSTEM_PROMPT = (
    "你是手机界面操作助手，服务对象是不会用智能手机的老人。"
    "根据用户的自然语言目标，从候选元素中选出【下一步应该点击的那一个】。\n"
    "规则：\n"
    "1. 只选一个；有多个文字相同的候选时，必须用位置或上下文区分。\n"
    "2. 如果没有任何候选能推进目标，id 返回 null，不要硬猜。\n"
    "3. instruction 必须是一句老人能听懂的话，不超过 20 个字，"
    "必须包含方位和按钮上的字，例如：点屏幕最下方中间的\"视频通话\"。\n"
    "4. instruction 里只念按钮上的核心文字，不要念数字、日期、时间、未读数；"
    "候选文字里如果混进了\"5.24\"\"3条\"这类辅助信息，只取名称部分，"
    "例如说「点最上面「依依」那一行」，而不是「依依 5.24」。\n"
    "5. confidence 是你对这次选择的把握，0 到 1 之间的小数。\n"
    "6. 只输出 JSON，不要解释。"
)


def build_user_prompt(
    cands: Sequence[Candidate],
    goal: str,
    img_w: int,
    img_h: int,
) -> str:
    lines = "\n".join(c.prompt_line(img_w, img_h) for c in cands)
    return (
        f"用户目标：{goal}\n"
        f"屏幕尺寸：{img_w}x{img_h}\n"
        f"候选元素（共 {len(cands)} 个）：\n{lines}\n\n"
        '输出格式：{"id": 3, "confidence": 0.86, '
        '"instruction": "点屏幕最下方中间的\\"视频通话\\"", "reason": "简短理由"}'
    )


def call_llm(system: str, user: str) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("缺少 LLM_API_KEY，请检查 .env")

    from openai import OpenAI

    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    kwargs: Dict[str, Any] = dict(
        model=settings.llm_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, **kwargs
        )
    except Exception:
        # 部分兼容接口不支持 response_format，退化重试一次
        resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def parse_json(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            raise ValueError(f"模型输出不是 JSON：{text[:200]}")
        return json.loads(match.group(0))


def choose(
    cands: Sequence[Candidate],
    goal: str,
    img_w: int,
    img_h: int,
) -> Dict[str, Any]:
    """返回 {id, confidence, instruction, reason}；id 可能是 None。"""
    reply = call_llm(SYSTEM_PROMPT, build_user_prompt(cands, goal, img_w, img_h))
    data = parse_json(reply)

    # 归一化 field，避免模型偶尔返回字符串
    idx = data.get("id")
    if idx is not None:
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = None

    try:
        conf = float(data.get("confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    return {
        "id": idx,
        "confidence": conf,
        "instruction": str(data.get("instruction") or "").strip(),
        "reason": str(data.get("reason") or "").strip(),
    }
