"""多步办事 Agent：规划 → 风险门禁 → 逐步定位 → 观察 → 组装。

为什么不用 LangGraph：
  1. 本项目只有一条线性流程，图框架带来的是依赖和版本风险，不是能力；
  2. 2C4G 上多一个框架就多一份内存和启动时间；
  3. 显式状态机的每个节点都往 trace 里写一条，"它刚才想了什么"可以直接给前端和
     MCP 客户端看，比黑盒图更好答辩。

状态机（`mermaid()` 可导出给技术文档）：

    plan ──► guard ──high──► escalate
      │        │
      │        └─ok─► act ──► observe ──fail──► needs_retake
      │                 ▲          │
      │                 └──next────┘
      └──no_path──► act(单步)

与定稿 D 系列一致：只教不做，高风险直接停。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from . import guide
from .vision.pipeline import locate_image

_log = logging.getLogger("nexttap.agent")

LocateFn = Callable[[str, str], Dict[str, Any]]

# 单次 run 最多处理多少张截图。
# 2026-11-03：评测集里最长的链（三家医院的挂号）都是 8 步，原来设为 8
# 正好卡在边界，再多一步就会被静默截断。提到 12 留出余量。
MAX_STEPS = 12


def mermaid() -> str:
    """导出状态机图，技术文档和答辩直接用。"""
    return (
        "stateDiagram-v2\n"
        "    [*] --> plan\n"
        "    plan --> guard: 命中任务路径\n"
        "    plan --> act: 没有路径，单步定位\n"
        "    guard --> escalate: 高风险（转账/验证码）\n"
        "    guard --> act: 安全\n"
        "    act --> observe\n"
        "    observe --> act: 还有下一步\n"
        "    observe --> needs_retake: 看不清 / 没命中\n"
        "    observe --> [*]: 全部完成\n"
        "    escalate --> [*]\n"
        "    needs_retake --> [*]\n"
    )


def _card(step: int, result: Dict[str, Any], path: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把单步定位结果组装成卡片，带"下一步是什么"。"""
    steps = (path or {}).get("steps") or []
    nxt = steps[step] if 0 < step < len(steps) else None
    return {
        "step": step,
        "text": result.get("text", ""),
        "instruction": result.get("instruction", ""),
        "bbox_norm": result.get("bbox_norm"),
        "confidence": result.get("confidence", 0.0),
        "next_step": nxt,
        "has_next": nxt is not None,
    }


def run(
    goal: str,
    image_paths: List[str],
    locate: LocateFn = locate_image,
    task_id: str = "",
) -> Dict[str, Any]:
    """跑一次完整的多步办事流程。

    Args:
        goal: 老人原话，例如「我想看看儿子」。
        image_paths: 每一步对应的一张截图，按顺序给。
        locate: 定位函数，默认用真实管线；单测可注入假实现。
        task_id: 强制指定任务链（跳过检索）。

    Returns:
        {
          status: done | partial | escalated | needs_retake | no_image,
          task_id, task_name, risk, steps[], trace[], next_step
        }
    """
    trace: List[Dict[str, Any]] = []
    goal = (goal or "").strip()
    images = [p for p in (image_paths or []) if p]

    def emit(node: str, **fields: Any) -> None:
        trace.append({"node": node, **fields})

    # ---------- plan ----------
    path: Optional[Dict[str, Any]] = None
    if task_id:
        path = next((p for p in guide.load_paths() if p.get("task_id") == task_id), None)
    if path is None:
        hits = guide.search(goal, top_k=1)
        path = hits[0] if hits else None

    emit(
        "plan",
        goal=goal,
        task_id=(path or {}).get("task_id", ""),
        task_name=(path or {}).get("name", ""),
        planned_steps=len((path or {}).get("steps") or []),
        images=len(images),
    )

    if not images:
        emit("observe", ok=False, reason="没有输入截图")
        return {
            "status": "no_image",
            "task_id": (path or {}).get("task_id", ""),
            "task_name": (path or {}).get("name", ""),
            "risk": guide.check_risk(goal),
            "steps": [],
            "trace": trace,
            "next_step": (path or {}).get("steps") or None,
        }

    # ---------- guard（整句话级别） ----------
    risk = guide.check_risk(goal)
    emit("guard", scope="goal", level=risk["level"], hits=risk["hits"])
    if risk["level"] == "high":
        return {
            "status": "escalated",
            "task_id": (path or {}).get("task_id", ""),
            "task_name": (path or {}).get("name", ""),
            "risk": risk,
            "steps": [],
            "trace": trace,
            "next_step": None,
        }

    steps_def = (path or {}).get("steps") or []
    cards: List[Dict[str, Any]] = []
    status = "done"

    for i, image_path in enumerate(images[:MAX_STEPS], start=1):
        # 第 2 步起，用任务链里的动作指令；第 1 步永远用用户原话
        step_def = steps_def[i - 1] if i <= len(steps_def) else None
        step_goal = str((step_def or {}).get("target") or goal).strip() if i > 1 else goal

        # ---------- guard（单步级别） ----------
        step_risk = guide.check_risk(step_goal)
        if step_risk["level"] == "high":
            emit("guard", scope="step", step=i, level="high", hits=step_risk["hits"])
            status = "escalated"
            break
        emit("guard", scope="step", step=i, level=step_risk["level"])

        # ---------- act ----------
        emit("act", step=i, image=os.path.basename(image_path), step_goal=step_goal)
        result = locate(image_path, step_goal)

        # ---------- replan ----------
        # 计划里的动作指令对不上这一屏时（路径数据不完美、或用户走了别的路），
        # 退回用用户原话再试一次。只有真的找不到才要求重拍。
        if not result.get("ok") and step_goal != goal:
            emit(
                "replan",
                step=i,
                from_goal=step_goal,
                to_goal=goal,
                reason=result.get("error", ""),
            )
            result = locate(image_path, goal)

        # ---------- observe ----------
        if not result.get("ok"):
            emit("observe", step=i, ok=False, reason=result.get("error", "未知原因"))
            status = "needs_retake"
            break

        emit(
            "observe",
            step=i,
            ok=True,
            text=result.get("text", ""),
            confidence=result.get("confidence", 0.0),
        )
        cards.append(_card(i, result, path))

        if len(cards) >= MAX_STEPS:
            break

    # 图用完了但任务链还没走完 —— 正常的中间状态，不是失败
    if status == "done" and steps_def and len(cards) < len(steps_def):
        status = "in_progress"

    emit("compose", status=status, done_steps=len(cards))
    _log.info("agent 结束：status=%s steps=%d", status, len(cards))

    nxt = steps_def[len(cards)] if status == "in_progress" else None

    return {
        "status": status,
        "task_id": (path or {}).get("task_id", ""),
        "task_name": (path or {}).get("name", ""),
        "risk": risk,
        "steps": cards,
        "trace": trace,
        "next_step": nxt,
    }
