"""批量跑定位：把 gt.jsonl 里的每个用例逐条送给后端，产出 pred.jsonl。

支持「一张图多个用例」：同一张截图只上传一次，然后按不同的 goal 反复调 /api/locate。

前置：
    1. 后端已启动：uvicorn app.main:app --reload --port 8000
    2. .env 里填好了 LLM_API_KEY
    3. gt.jsonl 已标注完（python tools/annotate.py）

用法：
    python tools/run_eval.py                     # 跑全部
    python tools/run_eval.py --limit 10          # 先试 10 个用例
    python tools/run_eval.py --base http://127.0.0.1:8000
    python tools/run_eval.py --file "Add Patient"   # 只跑路径里含该字符串的图

输出：data/eval/pred.jsonl（每行一个用例的预测结果）

路径说明：gt 的 `file` 是相对 `data/eval/` 的路径（新集是
`images/<App>/<医院>/<流程>/xxx.jpg`），所以 `EVAL_DIR / rel` 能直接拼出真实路径。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
GT_PATH = EVAL_DIR / "gt.jsonl"
PRED_PATH = EVAL_DIR / "pred.jsonl"


def case_key(row: Dict[str, Any]) -> str:
    """用例唯一键。必须和 eval.py 的 case_key 完全一致，否则永远匹配不上。"""
    rel = str(row.get("file", "")).replace("\\", "/")
    goal = str(row.get("goal", "")).strip()
    tid = str(row.get("task_id", "") or "").strip()
    step = int(row.get("step", 1) or 1)
    return f"{rel}::{tid}::{step}::{goal}"


_case_key = case_key


def load_gt(path: Path = GT_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"[FATAL] 找不到 {path}，先跑 python tools/annotate.py")
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"[FATAL] gt.jsonl 第 {lineno} 行有坏 JSON：{exc}")
        if not row.get("file") or not row.get("bbox"):
            raise SystemExit(f"[FATAL] gt.jsonl 第 {lineno} 行缺 file 或 bbox")
        if not str(row.get("goal", "")).strip():
            raise SystemExit(f"[FATAL] gt.jsonl 第 {lineno} 行缺 goal")
        rows.append(row)
    if not rows:
        raise SystemExit("[FATAL] gt.jsonl 是空的")
    return rows


MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}


def upload(base: str, path: Path, timeout: int) -> str:
    """上传截图。MIME 必须按真实后缀给，写死 image/jpeg 会被后端按后缀拒掉。"""
    suffix = path.suffix.lower()
    if suffix not in MIME_BY_SUFFIX:
        raise RuntimeError(
            f"后端只接受 png/jpg/jpeg/webp，这张是 {suffix}：{path.name}"
        )
    with path.open("rb") as fh:
        resp = requests.post(
            f"{base}/api/screenshot",
            files={"file": (path.name, fh, MIME_BY_SUFFIX[suffix])},
            timeout=timeout,
        )
    if resp.status_code != 200:
        raise RuntimeError(f"上传失败 {resp.status_code}: {resp.text[:200]}")
    return str(resp.json()["image_id"])


def locate(base: str, image_id: str, goal: str, timeout: int) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        resp = requests.post(
            f"{base}/api/locate",
            json={"image_id": image_id, "goal": goal},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {"error": f"请求异常：{exc}", "elapsed_ms": int((time.perf_counter() - started) * 1000)}

    elapsed = int((time.perf_counter() - started) * 1000)
    if resp.status_code != 200:
        detail = ""
        try:
            detail = str(resp.json().get("detail", ""))
        except Exception:
            detail = resp.text[:200]
        return {"error": f"{resp.status_code} {detail}", "elapsed_ms": elapsed}

    data = resp.json()
    return {
        "bbox": data["bbox_px"],
        "bbox_norm": data.get("bbox_norm"),
        "text": data.get("text"),
        "confidence": data.get("confidence"),
        "low_confidence": data.get("low_confidence"),
        "instruction": data.get("instruction"),
        "elapsed_ms": data.get("elapsed_ms", elapsed),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="批量跑 hit@1 评测（支持一图多用例）")
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个用例，0 = 全部")
    parser.add_argument("--file", default="", help="只跑文件路径包含该字符串的图（支持子目录名，如 'Add Patient'）")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--gt", type=Path, default=GT_PATH, help="真值文件，默认 gt.jsonl")
    parser.add_argument("--out", type=Path, default=PRED_PATH)
    args = parser.parse_args(argv)

    rows = load_gt(args.gt)
    if args.file:
        rows = [r for r in rows if args.file in str(r["file"])]

    # 去重：同一 (图片, task_id, step, goal) 只跑一次
    # 多个框是「可接受入口」，预测结果相同，跑一次就够
    deduped: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
    for row in rows:
        deduped.setdefault(_case_key(row), row)
    skipped = len(rows) - len(deduped)
    rows = list(deduped.values())

    # --limit 必须在去重之后截断，否则「可接受框」会占掉名额，
    # 导致实际跑的用例数比 --limit 少，容易误判进度
    if args.limit > 0:
        rows = rows[: args.limit]
    if not rows:
        raise SystemExit("[FATAL] 过滤后没有用例可跑")
    if skipped:
        print(f"（同一目标有多个可接受框，去重后少跑 {skipped} 次，结果通用）\n")

    # 同一张图只上传一次
    image_ids: "OrderedDict[str, str]" = OrderedDict()
    results: List[Dict[str, Any]] = []
    ok = fail = 0
    t0 = time.perf_counter()

    for i, row in enumerate(rows, 1):
        rel = str(row["file"]).replace("\\", "/")
        goal = str(row["goal"]).strip()
        tid = str(row.get("task_id", "") or "").strip()
        step = int(row.get("step", 1) or 1)
        img = EVAL_DIR / rel
        tag = f"[{tid} 第{step}步] " if tid else ""
        # 新集里同名文件分布在不同目录（Screenshot_xxx.jpg 会重名），
        # 只打印 basename 分不清是哪一张，这里带上目录尾部两级
        shown = Path(rel)
        shown = shown.parent.name + "/" + shown.name if shown.parent.name else shown.name
        print(f"[{i}/{len(rows)}] {tag}{shown}  goal={goal}")

        if not img.exists():
            print(f"    ! 图片不存在：{img}")
            results.append({"file": rel, "goal": goal, "task_id": tid, "step": step,
                            "error": "图片文件不存在"})
            fail += 1
            continue

        try:
            if rel not in image_ids:
                image_ids[rel] = upload(args.base, img, args.timeout)
                print(f"    ↑ 上传完成 image_id={image_ids[rel][:12]}…")
            pred = locate(args.base, image_ids[rel], goal, args.timeout)
        except Exception as exc:
            print(f"    ! 异常：{exc}")
            pred = {"error": f"异常：{exc}"}

        # 关键：task_id / step 必须原样写回，否则 eval.py 匹配不上，全部算「缺预测」
        pred["file"] = rel
        pred["goal"] = goal
        pred["task_id"] = tid
        pred["step"] = step
        results.append(pred)

        if pred.get("error"):
            print(f"    x {pred['error']}")
            fail += 1
        else:
            ok += 1
            print(
                f"    + bbox={pred['bbox']} conf={pred.get('confidence')} "
                f"{pred.get('elapsed_ms')}ms  {pred.get('instruction', '')}"
            )

        args.out.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(json.dumps(r, ensure_ascii=False) for r in results) + "\n"
        args.out.write_text(text, encoding="utf-8")

    dt = time.perf_counter() - t0
    print("\n" + "=" * 62)
    print(f"跑完 {len(rows)} 个用例（{len(image_ids)} 张图）：成功 {ok}，失败 {fail}，"
          f"总耗时 {dt:.1f}s")
    print(f"结果写入：{args.out}")
    print("下一步：python tools/eval.py --ci")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
