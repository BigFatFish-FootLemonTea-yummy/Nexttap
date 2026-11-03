#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent 状态机单测：注入假 locate，不依赖 OCR / 模型 / 网络。

    python server/tests/test_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import agent  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


def ok_locate(_path, _goal):
    return {
        "ok": True,
        "text": "A.儿子",
        "bbox_norm": [1.0, 2.0, 3.0, 4.0],
        "confidence": 0.9,
        "instruction": "点最上面那一行",
        "candidate_count": 20,
    }


def fail_locate(_path, _goal):
    return {"ok": False, "error": "没有识别到任何文字"}


IMGS4 = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
GOAL = "想给这个人打个电话"  # 命中 wechat.call，3 步

# 1) 走完全部 3 步
r = agent.run(GOAL, IMGS4[:3], locate=ok_locate)
check("三步完成 status", r["status"], "done")
check("三步完成 cards", len(r["steps"]), 3)
check("三步完成 next_step", r["next_step"], None)
# 任务名会随数据重建变化，只断言 id 稳定存在、名字非空
check("任务 ID", r["task_id"], "wechat.call")
check("任务名非空", bool(r["task_name"]), True)

# 2) 只给 2 张：中途状态，不是失败
r2 = agent.run(GOAL, IMGS4[:2], locate=ok_locate)
check("两步中途 status", r2["status"], "in_progress")
check("两步中途 cards", len(r2["steps"]), 2)
check("两步中途 next_step 序号", r2["next_step"]["step"], 3)

# 3) 高风险目标：直接拦，不产生任何步骤
r3 = agent.run("帮我把钱转给儿子", ["a.jpg"], locate=ok_locate)
check("高风险 status", r3["status"], "escalated")
check("高风险无步骤", len(r3["steps"]), 0)
check("高风险 trace 有 guard", any(t["node"] == "guard" and t.get("level") == "high" for t in r3["trace"]), True)

# 4) 定位失败：要求重拍
r4 = agent.run(GOAL, ["a.jpg"], locate=fail_locate)
check("失败 status", r4["status"], "needs_retake")
check("失败无卡片", len(r4["steps"]), 0)

# 5) 没有图
r5 = agent.run(GOAL, [], locate=ok_locate)
check("无图 status", r5["status"], "no_image")

# 6) trace 顺序：plan 在 guard 前，act 在 observe 前
nodes = [t["node"] for t in r["trace"]]
check("trace 首个是 plan", nodes[0], "plan")
check("trace 含 compose", nodes[-1], "compose")
check("plan 在首个 act 之前", nodes.index("plan") < nodes.index("act"), True)

# 7) 状态图能导出
check("mermaid 非空", len(agent.mermaid()) > 50, True)

print()
print("全部通过" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
