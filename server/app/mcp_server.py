"""MCP 服务：把 NextTap 的能力暴露给任意 MCP 客户端。

两种启动方式：
    python -m app.mcp_server              # stdio，给 Claude Desktop / Cursor 连
    mcp dev server/app/mcp_server.py      # 官方 Inspector，网页里点着测

注意：stdio 模式下 stdout 被协议占用，任何 print 都必须走 stderr，
否则客户端会报解析错误。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

from PIL import Image

from . import agent, guide
from .llm.select import choose
from .vision.candidates import merge_lines, pad_box, to_norm
from .vision.ocr import run_ocr

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 mcp 依赖，请先执行：pip install mcp") from exc

mcp = FastMCP("nexttap")


def _log(message: str) -> None:
    """stdio 模式下只能往 stderr 打日志。"""
    print(f"[nexttap-mcp] {message}", file=sys.stderr)


@mcp.tool()
def locate_element(image_path: str, goal: str) -> Dict[str, Any]:
    """在手机截图里定位「下一步该点的元素」。

    Args:
        image_path: 截图的本地绝对路径（jpg/png）。
        goal: 老人想做什么，用口语描述，例如「我想看看儿子」。
              不要填按钮名，否则等于把答案告诉了模型。

    Returns:
        ok=True 时包含 text（命中的界面文字）、bbox_px（原图像素框）、
        bbox_norm（0-1000 归一化框，前端叠圈用）、confidence、
        instruction（不超过 20 字、可直接念给老人听的指引）。
        无法确定时 ok=False，error 说明原因，此时不应硬指。
    """
    path = Path(image_path)
    if not path.is_file():
        return {"ok": False, "error": f"图片不存在：{image_path}"}

    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        return {"ok": False, "error": f"图片无法解析：{exc}"}

    raw = run_ocr(str(path))
    if not raw:
        return {"ok": False, "error": "没有识别到任何文字，请对准屏幕重拍、避免反光"}

    candidates = merge_lines(raw)
    decision = choose(candidates, goal, width, height)

    index = decision.get("id")
    if index is None:
        return {
            "ok": False,
            "error": decision.get("instruction") or "看不出该点哪里，请换一张更清楚的图",
        }

    try:
        index = int(index)
    except (TypeError, ValueError):
        return {"ok": False, "error": f"模型返回了非整数编号：{index!r}"}

    if not 0 <= index < len(candidates):
        return {"ok": False, "error": "模型返回了越界的候选编号"}

    target = candidates[index]
    box = pad_box(target.box, width, height)
    return {
        "ok": True,
        "text": target.text,
        "bbox_px": box.as_list(),
        "bbox_norm": to_norm(box, width, height),
        "confidence": decision["confidence"],
        "instruction": decision["instruction"],
        "candidate_count": len(candidates),
    }


@mcp.tool()
def search_guide(query: str, top_k: int = 3) -> Dict[str, Any]:
    """按老人的口语检索标准操作路径。

    返回若干条候选任务链，每条带 task_id、名称和完整步骤，
    可用于预判下一步，避免每步都重新截图。

    Args:
        query: 老人的原话，例如「我想挂个号」「退休金那个」。
        top_k: 返回条数，默认 3。
    """
    hits = guide.search(query, top_k=top_k)
    return {
        "query": query,
        "count": len(hits),
        "results": hits,
        "note": "" if hits else "没有匹配到标准路径，可以只用 locate_element 逐步定位",
    }


@mcp.tool()
def check_risk(text: str) -> Dict[str, Any]:
    """判断一句话是否涉及高风险操作。

    产品红线是「只教不做」：命中 high 时不应继续给出指引，
    而应提示让家人或柜台人员在場。

    Args:
        text: 老人的原话或即将执行的操作描述。
    """
    return guide.check_risk(text)


@mcp.tool()
def compose_card(
    text: str,
    instruction: str,
    task_id: str = "",
    step: int = 1,
) -> Dict[str, Any]:
    """把一次定位结果组装成可展示的指路卡片。

    带上 task_id 时，会返回该任务的当前步骤与下一步，
    用于多步流程预热（第 2 步不用再重新截图）。

    Args:
        text: locate_element 命中的界面文字。
        instruction: 给老人看的一句话。
        task_id: 可选，任务链编号，例如 wechat.video_call_son。
        step: 当前是第几步，从 1 开始。
    """
    return guide.compose_card(text, instruction, task_id, step)


@mcp.tool()
def run_task(goal: str, image_paths: list[str], task_id: str = "") -> Dict[str, Any]:
    """跑完整的多步办事流程：规划 → 风险门禁 → 逐步定位 → 组装卡片。

    与 locate_element 的区别：locate_element 只处理一张图、一个动作；
    本工具会先检索标准任务路径，再按顺序走完多步，并返回每一步的决策记录。

    Args:
        goal: 老人原话，例如「我想看看儿子」。
        image_paths: 按步骤顺序排列的截图本地绝对路径。
        task_id: 可选，强制指定任务链，例如 wechat.video_call_son。

    Returns:
        status（done / in_progress / needs_retake / escalated / no_image）、
        task_name、每步的 text 与 instruction、next_step，以及 trace。
        status=escalated 时**不要**继续操作，必须转人工。
    """
    return agent.run(goal, image_paths, task_id=task_id)


def main() -> None:
    _log("starting nexttap MCP server on stdio")
    mcp.run()


if __name__ == "__main__":
    main()
