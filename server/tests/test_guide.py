#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""guide 模块单测：不依赖 OCR / 模型 / 网络。

    python server/tests/test_guide.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import guide  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    if not ok:
        fails += 1
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got={got!r} want={want!r}")


paths = guide.load_paths()
check("路径条数", len(paths), 120)

# 口语 → 路径
check("我想看看儿子", guide.search("我想看看儿子")[0]["task_id"], "wechat.video_call_son")
check("把照片发过去", guide.search("把照片发过去")[0]["task_id"], "wechat.send_photo")
check("叫个车", guide.search("叫个车")[0]["task_id"], "map.taxi")
check(
    "退休金命中养老金",
    any(h["task_id"] == "gov.pension" for h in guide.search("退休金那个")),
    True,
)
check("空查询", guide.search(""), [])

# 风险判断
check("转账高风险", guide.check_risk("帮我把钱转给儿子")["level"], "high")
check("验证码高风险", guide.check_risk("输一下验证码")["level"], "high")
check("删除中风险", guide.check_risk("把这张照片删了")["level"], "medium")
check("普通操作", guide.check_risk("点开设置")["level"], "none")

# 卡片组装
card = guide.compose_card("A.儿子", "点最上面那一行", "wechat.video_call_son", 1)
check("卡片 total_steps", card["total_steps"], 4)
check("卡片有下一步", card["has_next"], True)
check("卡片下一步序号", card["next_step"]["step"], 2)

card_last = guide.compose_card("x", "y", "map.taxi", 1)
check("最后一步没有下一步", card_last["has_next"], False)

print()
print("全部通过" if fails == 0 else f"{fails} 项失败")
sys.exit(1 if fails else 0)
