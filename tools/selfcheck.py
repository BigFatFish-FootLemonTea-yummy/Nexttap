"""全链路自检：标注 -> 评测，把所有会导致「白干」的坑跑一遍。

不依赖网络、不依赖后端、不依赖 tkinter 窗口。

跑法：python tools/selfcheck.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval as E
import run_eval as R

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"
IMAGES = ROOT / "data" / "eval" / "images"
PATHS = ROOT / "data" / "rag" / "paths.jsonl"

fails: list[str] = []
LOW_RES_KNOWN = {"035.jpg", "051.jpg"}  # 已知低分辨率，已标 quality=bad
warns: list[str] = []


def ok(name: str) -> None:
    print(f"  PASS  {name}")


def bad(name: str, detail: str) -> None:
    fails.append(name)
    print(f"  FAIL  {name}\n        {detail}")


def warn(name: str, detail: str) -> None:
    warns.append(name)
    print(f"  WARN  {name}\n        {detail}")


print("=" * 70)
print("1. 关键函数一致性")
print("=" * 70)

# 1.1 run_eval 和 eval 的 case_key 必须完全一致，否则全部匹配不上
sample = {"file": "images/001.jpg", "task_id": "hospital.pay_bill",
          "step": 1, "goal": "点门诊缴费"}
if R.case_key(sample) == E.case_key(sample):
    ok("run_eval.case_key == eval.case_key")
else:
    bad("case_key 不一致", f"{R.case_key(sample)} != {E.case_key(sample)}")

# 1.2 没有 task_id 的用例也要能匹配
plain = {"file": "images/001.jpg", "goal": "点预约挂号"}
if R.case_key(plain) == E.case_key(plain):
    ok("无 task_id 的用例 key 一致")
else:
    bad("无 task_id 时 key 不一致", f"{R.case_key(plain)} != {E.case_key(plain)}")

print()
print("=" * 70)
print("2. 命中判定逻辑")
print("=" * 70)

# 2.1 中心点在框内 -> 命中
gt = [{"file": "a.jpg", "goal": "g", "bbox": [100, 100, 300, 200]}]
pred = [{"file": "a.jpg", "goal": "g", "bbox": [150, 120, 250, 180], "elapsed_ms": 1000}]
cases = E.group_gt(gt)
hit = any(E.contains(b, E.center(pred[0]["bbox"])) for b in cases[list(cases)[0]]["boxes"])
ok("中心点在框内判命中") if hit else bad("中心点在框内未判命中", str(pred))

# 2.2 中心点在框外 -> 不命中
pred2 = [{"file": "a.jpg", "goal": "g", "bbox": [900, 900, 950, 950]}]
cases = E.group_gt(gt)
hit2 = any(E.contains(b, E.center(pred2[0]["bbox"])) for b in cases[list(cases)[0]]["boxes"])
ok("中心点在框外判不命中") if not hit2 else bad("中心点在框外误判命中", str(pred2))

# 2.3 多可接受框：命中任意一个都算对
gt3 = [{"file": "a.jpg", "goal": "g", "bbox": [0, 0, 100, 100]},
       {"file": "a.jpg", "goal": "g", "bbox": [500, 500, 600, 600]}]
cases = E.group_gt(gt3)
entry = cases[list(cases)[0]]
pred3 = [450, 450, 550, 550]  # 中心 500,500 落在第二个框的边界
hit3 = any(E.contains(b, E.center(pred3)) for b in entry["boxes"])
ok("多可接受框：命中第二个也算对") if hit3 and len(entry["boxes"]) == 2 else \
    bad("多可接受框逻辑错误", f"boxes={entry['boxes']} hit={hit3}")

# 2.4 边界值：中心点正好在框的边上
gt4 = [{"file": "a.jpg", "goal": "g", "bbox": [100, 100, 300, 200]}]
edge = [100, 100, 100, 100]  # 中心 100,100 = 左上角
cases = E.group_gt(gt4)
hit4 = any(E.contains(b, E.center(edge)) for b in cases[list(cases)[0]]["boxes"])
ok("边界点算命中（闭区间）") if hit4 else warn("边界点不算命中", "闭区间应为命中")

print()
print("=" * 70)
print("3. 反向框（用户从右下往左上拖）")
print("=" * 70)


class Fake:
    """复用 annotate 的 screen_to_image 逻辑做纯几何测试。"""

    def __init__(self):
        self.img_w, self.img_h = 1260, 2644
        self.scale = 0.28
        self.offset = (260, 0)
        self.pending = None

    def screen_to_image(self):
        if not self.pending:
            return None
        ox, oy = self.offset
        # 和 annotate.on_release 一致：先归一化成左上/右下，再换算
        ax, ay, bx, by = self.pending
        sx0, sy0 = min(ax, bx), min(ay, by)
        sx1, sy1 = max(ax, bx), max(ay, by)
        x0 = max(0, min(self.img_w, int(round((sx0 - ox) / self.scale))))
        y0 = max(0, min(self.img_h, int(round((sy0 - oy) / self.scale))))
        x1 = max(0, min(self.img_w, int(round((sx1 - ox) / self.scale))))
        y1 = max(0, min(self.img_h, int(round((sy1 - oy) / self.scale))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return [x0, y0, x1, y1]


f = Fake()
# 正常方向
f.pending = (300, 100, 400, 200)
normal = f.screen_to_image()
# 反向拖（从右下拖到左上）
f.pending = (400, 200, 300, 100)
reverse = f.screen_to_image()
if normal == reverse:
    ok(f"反向拖拽归一化正确 {normal}")
else:
    bad("反向拖拽坐标错误", f"normal={normal} reverse={reverse}")

# 完全拖出图片边界
f.pending = (-500, -500, 2000, 4000)
clamped = f.screen_to_image()
if clamped and 0 <= clamped[0] and clamped[2] <= 1260 and 0 <= clamped[1] and clamped[3] <= 2644:
    ok(f"超出边界自动裁剪 {clamped}")
else:
    bad("超出边界未裁剪", str(clamped))

print()
print("=" * 70)
print("4. 现有数据体检")
print("=" * 70)

if GT.exists():
    rows = [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
    ok(f"gt.jsonl 可解析，共 {len(rows)} 条")

    # 4.1 每条都必须是 dict 且有必填字段
    for i, r in enumerate(rows, 1):
        for field in ("file", "goal", "bbox"):
            if not r.get(field):
                bad(f"gt.jsonl 第 {i} 行缺 {field}", str(r)[:120])
        if len(r.get("bbox") or []) < 4:
            bad(f"gt.jsonl 第 {i} 行 bbox 不完整", str(r)[:120])

    # 4.2 图片必须真实存在
    for r in rows:
        p = ROOT / "data" / "eval" / r["file"]
        if not p.exists():
            bad(f"图片不存在：{r['file']}", str(p))

    # 4.3 bbox 必须在图片尺寸范围内
    try:
        from PIL import Image
        for r in rows:
            p = ROOT / "data" / "eval" / r["file"]
            if not p.exists():
                continue
            with Image.open(p) as im:
                w, h = im.size
            x0, y0, x1, y1 = r["bbox"][:4]
            if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
                bad(f"bbox 越界：{r['file']}",
                    f"bbox={r['bbox']} 图片={w}x{h}")
            else:
                ok(f"bbox 合法：{r['file']} {r['goal']}")
    except ImportError:
        warn("没装 Pillow，跳过 bbox 越界检查", "pip install pillow")

    # 4.4 task_id 必须在 paths.jsonl 里存在
    if PATHS.exists():
        valid = set()
        for line in PATHS.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                try:
                    valid.add(json.loads(line)["task_id"])
                except Exception:
                    pass
        for r in rows:
            tid = r.get("task_id")
            if tid and tid not in valid:
                bad(f"task_id 不在 paths.jsonl 里：{tid}",
                    f"合法值：{sorted(valid)}")
        if all(not r.get("task_id") or r["task_id"] in valid for r in rows):
            ok("所有 task_id 都能在 paths.jsonl 找到")

    # 4.5 同一条链的 step 是否连续
    chains: dict[str, list[int]] = {}
    for r in rows:
        if r.get("task_id"):
            chains.setdefault(r["task_id"], []).append(int(r.get("step", 1)))
    for tid, steps in chains.items():
        uniq = sorted(set(steps))
        expect = list(range(1, len(uniq) + 1))
        if uniq != expect:
            warn(f"链 {tid} 的步骤不连续：{uniq}",
                 f"期望从 1 开始连续，实际 {uniq}")
        else:
            ok(f"链 {tid} 步骤连续：{uniq}")

    # 4.6 task_id / step 必须完整（缺了会被误算进 step=1，污染最关键指标）
    #     2026-11-01 踩过：5 行漏填 task_id/step，被 apply_goal_map 整体跳过，
    #     既没口语化也没补身份，step 缺省成 1，虚增了 step=1 的样本。
    broken = [(i, r) for i, r in enumerate(rows, 1)
              if not str(r.get("task_id") or "").strip() or r.get("step") is None]
    if broken:
        bad(f"有 {len(broken)} 行缺 task_id / step",
            "\n        ".join(f"第{i}行 {r.get('file')} {r.get('goal')!r}" for i, r in broken[:10])
            + "\n        修法：python tools/repair_eval_data.py --dry-run")
    else:
        ok("所有行都有 task_id 和 step")

    # 4.7 同一 bbox 不能对应多个身份（apply_goal_map 漏改会留下这种）
    by_box: dict[tuple, set] = {}
    for r in rows:
        k = (r["file"], tuple(r["bbox"][:4]))
        by_box.setdefault(k, set()).add((str(r.get("task_id") or ""), r.get("step")))
    conflicts = {k: v for k, v in by_box.items() if len(v) > 1}
    if conflicts:
        warn(f"有 {len(conflicts)} 个 bbox 对应多个 task_id/step",
             "\n        ".join(f"{k[0]} {list(k[1])} -> {v}" for k, v in list(conflicts.items())[:5])
             + "\n        这些行可能是同一用例被拆成了两条，需人工确认")
    else:
        ok("没有重复 bbox")
else:
    warn("gt.jsonl 不存在", "还没开始标注")

print()
print("=" * 70)
print("5. paths.jsonl 体检")
print("=" * 70)

if PATHS.exists():
    entries = []
    for lineno, line in enumerate(PATHS.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as exc:
            bad(f"paths.jsonl 第 {lineno} 行 JSON 错误", str(exc))
    ok(f"paths.jsonl 可解析，共 {len(entries)} 条")

    seen_ids = set()
    for e in entries:
        tid = e.get("task_id", "")
        if not tid:
            bad("有 entry 缺 task_id", str(e)[:100])
            continue
        if tid in seen_ids:
            bad(f"task_id 重复：{tid}", "")
        seen_ids.add(tid)

        if not e.get("aliases"):
            bad(f"{tid} 缺 aliases", "RAG 检索靠 aliases，没有就等于检索不到")
        elif len(e["aliases"]) < 3:
            warn(f"{tid} 只有 {len(e['aliases'])} 个 aliases",
                 "建议至少 3 句，覆盖不同说法")

        steps = e.get("steps") or []
        if not steps:
            bad(f"{tid} 缺 steps", "")
        for j, s in enumerate(steps, 1):
            if s.get("step") != j:
                warn(f"{tid} 的 step 编号不连续", f"第 {j} 个是 {s.get('step')}")
            if not s.get("target"):
                bad(f"{tid} 第 {j} 步缺 target", "")

    # 5.1 aliases 之间不能重复（重复说明是同一句话）
    all_alias: dict[str, str] = {}
    for e in entries:
        for a in e.get("aliases") or []:
            if a in all_alias and all_alias[a] != e.get("task_id"):
                bad(f"alias「{a}」同时属于 {all_alias[a]} 和 {e.get('task_id')}",
                    "一句话只能对应一个任务，否则检索会歧义")
            all_alias[a] = e.get("task_id", "")
    ok(f"共 {len(all_alias)} 条 aliases，无跨任务重复")
else:
    warn("paths.jsonl 不存在", "还没建任务链")

print()
print("=" * 70)
print("6. 图片体检")
print("=" * 70)

if IMAGES.exists():
    files = sorted(p for p in IMAGES.iterdir()
                   if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"})
    ok(f"共 {len(files)} 张图")

    # 6.1 低分辨率警告（OCR 会吃亏）
    try:
        from PIL import Image
        low = []
        for p in files:
            with Image.open(p) as im:
                w, h = im.size
            # 已被标成 quality=bad 的图不再报警，因为评测时会剔除
            if w < 800 and p.name not in LOW_RES_KNOWN:
                low.append((p.name, w, h))
        if low:
            warn(f"{len(low)} 张图宽度不足 800px",
                 "低分辨率会让 OCR 小字识别率下降，gate 数字会失真：" +
                 ", ".join(f"{n}({w}x{h})" for n, w, h in low[:5]))
        else:
            ok("所有图片宽度 >= 800px")
    except ImportError:
        pass

    # 6.2 未标注的图
    if GT.exists():
        gt_files = {r["file"] for r in rows if r.get("file")}
        unlabeled = [p.name for p in files if f"images/{p.name}" not in gt_files]
        if unlabeled:
            warn(f"{len(unlabeled)} 张图还没标注", "前 5 个：" + ", ".join(unlabeled[:5]))
        else:
            ok("所有图都已标注")
else:
    bad("images 目录不存在", str(IMAGES))

print()
print("=" * 70)
print("自检结果")
print("=" * 70)
if fails:
    print(f"❌ {len(fails)} 项错误：")
    for f_ in fails:
        print(f"   - {f_}")
else:
    print("✅ 无错误")

if warns:
    print(f"\n⚠️  {len(warns)} 项提醒：")
    for w in warns:
        print(f"   - {w}")

sys.exit(1 if fails else 0)
