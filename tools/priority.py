"""按优先级挑出「第一批要跑的 50 条用例」。

排序依据（对应《项目定稿》的 P0 和真实使用场景）：

    P0  必跑：真实高频场景 + 文字清晰 + 代表性
    P1  该跑：场景成立但频次低
    P2  可缓：界面上有但老人未必会问
    SKIP 不跑：分辨率过低 / 界面被遮挡 / 元素无文字（OCR 提不出）

跑法：
    python tools/priority.py                 # 打印 Top 50
    python tools/priority.py --n 60          # 换个数
    python tools/priority.py --export first50.jsonl   # 导出成可直接跑的 gt
    python tools/priority.py --list          # 看全部 172 条的评级
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
GT = ROOT / "data" / "eval" / "gt.jsonl"
IMAGES = ROOT / "data" / "eval" / "images"

# --------------------------------------------------------------------------
# 每个 task_id 的基础优先级
#   1 = 真实高频（老人真的会问）
#   2 = 场景成立但频次低
#   3 = 界面上有，但老人未必会问
# --------------------------------------------------------------------------
TASK_PRIORITY: Dict[str, int] = {
    # ---- 医院：老人最常问的就是挂号、缴费、查报告 ----
    "hospital.book_appointment": 1,
    "hospital.pay_bill": 1,
    "hospital.report": 1,
    "hospital.medical_record": 1,
    "hospital.add_patient": 1,
    "hospital.pick_dept": 1,
    "hospital.pick_doctor": 1,
    "hospital.confirm_book": 1,
    "hospital.visit_record": 1,
    "hospital.check_in": 2,
    "hospital.manage_patient": 1,
    "hospital.guide": 2,
    "hospital.profile": 2,
    "hospital.elder_service": 1,
    "hospital.no_card": 2,
    "hospital.bind_card": 2,
    "hospital.pick_id_type": 3,
    "hospital.book_guide": 2,
    "hospital.book_notice": 3,
    "hospital.pick_date": 2,
    "hospital.pick_morning": 2,
    "hospital.expand_doctor": 3,
    "hospital.pay_pending": 1,
    "hospital.pay_done": 2,
    "hospital.deposit": 2,
    "hospital.daily_bill": 2,
    "hospital.physical_book": 2,
    "hospital.physical_pay": 2,
    "hospital.physical_report": 2,
    "hospital.online_consult": 2,
    "hospital.my_doctor": 2,
    "hospital.medicine": 2,
    "hospital.invoice": 2,
    "hospital.card_manage": 2,
    "hospital.rx_pay": 2,
    "hospital.dept_detail": 3,
    "hospital.dept_intro": 3,
    "hospital.doctor_team": 3,
    "hospital.home_nursing": 3,
    "hospital.article": 3,
    "hospital.intro": 3,
    "hospital.survey": 3,
    "hospital.ai_triage": 3,
    "hospital.quick_pay": 3,
    "hospital.record_mail": 3,
    "hospital.surgery": 3,
    "hospital.discharge_pay": 3,
    "hospital.enter_app": 2,
    "hospital.enter_other": 3,

    # ---- 政务：健康码、医保、社保是高频 ----
    "gov.health_code": 1,
    "gov.insurance_code": 1,
    "gov.insurance": 1,
    "gov.insurance_balance": 1,
    "gov.insurance_pay": 2,
    "gov.insurance_anchor": 2,
    "gov.insurance_ai": 3,
    "gov.insurance_resident": 2,
    "gov.insurance_flex": 2,
    "gov.insurance_pay_fee": 2,
    "gov.social_security": 1,
    "gov.social_cert": 2,
    "gov.social_cert_query": 2,
    "gov.social_card_query": 2,
    "gov.social_card_service": 2,
    "gov.social_scan": 3,
    "gov.my_social_card": 1,
    "gov.pension": 1,
    "gov.pension_query": 2,
    "gov.pension_proof": 2,
    "gov.rights_query": 3,
    "gov.rights_record": 3,
    "gov.housing_fund": 1,
    "gov.medical_service": 1,
    "gov.local_medical": 2,
    "gov.cross_region": 2,
    "gov.reimbursement": 2,
    "gov.hospital_query": 2,
    "gov.elder_mode": 1,
    "gov.digital_space": 2,
    "gov.quick_service": 3,
    "gov.start_service": 2,
    "gov.face_verify": 2,
    "gov.login_face": 2,
    "gov.login_wechat_police": 3,
    "gov.login_next": 3,
    "gov.real_name": 2,
    "gov.health_card": 1,
    "gov.driving": 2,
    "gov.exit_entry": 2,
    "gov.market_supervise": 3,
    "gov.disability": 2,
    "gov.legal_service": 3,
    "gov.social_relief": 2,
    "gov.utility_pay": 2,
    "gov.work_injury": 3,
    "gov.unemployment": 2,
    "gov.unemployment_change": 3,
    "gov.tax": 2,
    "gov.suihaoban": 3,
    "gov.enter_yueshengshi": 2,
    "gov.promo": 3,
    "gov.family_service": 1,

    # ---- 微信：视频通话、发照片、收付款是最高频 ----
    "wechat.video_call_son": 1,
    "wechat.send_photo": 1,
    "wechat.take_photo": 2,
    "wechat.pay_code": 1,
    "wechat.scan": 1,
    "wechat.voice_call_son": 2,
    "wechat.voice_input": 2,
    "wechat.transfer": 1,
    "wechat.red_packet": 2,
    "wechat.share_location": 2,
    "wechat.live_location": 3,
    "wechat.open_chat": 1,
    "wechat.open_wechat": 2,
    "wechat.add_friend": 2,
    "wechat.new_group": 2,
    "wechat.save_photo": 2,
    "wechat.camera": 3,

    # ---- 地图 / 出行 ----
    "map.subway_qr": 1,
    "map.taxi": 1,
    "map.transit": 2,
    "map.carpool": 2,
    "map.subway_map": 3,
    "map.bus_live": 2,
}

# 分辨率过低、会让 OCR 失真，从首批排除
LOW_RES = {"035.jpg", "051.jpg"}

# 界面被遮挡 / 元素无文字，OCR 提不出候选，首批排除
HARD_IMAGES = {"046.jpg", "047.jpg"}


def load_gt() -> List[dict]:
    if not GT.exists():
        raise SystemExit(f"找不到 {GT}")
    return [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]


def image_size(name: str) -> Tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(IMAGES / name) as im:
            return im.size
    except Exception:
        return (0, 0)


def grade(row: dict) -> Tuple[int, str]:
    """返回 (优先级数字, 理由)。数字越小越该先跑。"""
    tid = str(row.get("task_id", ""))
    fname = Path(str(row["file"])).name
    base = TASK_PRIORITY.get(tid, 3)

    if fname in LOW_RES:
        return 99, "分辨率过低，OCR 会失真"
    if fname in HARD_IMAGES:
        return 98, "界面被遮挡/元素无文字"

    w, h = image_size(fname)
    if w and w < 800:
        return 97, f"宽度仅 {w}px"

    x0, y0, x1, y1 = row["bbox"]
    if (x1 - x0) * (y1 - y0) > (w * h) * 0.3:
        return base + 5, "框过大，判定宽松"

    return base, {1: "真实高频", 2: "场景成立", 3: "频次低"}.get(base, "其他")


def select_balanced(graded, n, per_image_cap=3, per_task_cap=4):
    """在保持优先级的前提下，限制单张图和单个任务的贡献条数。

    两个约束的原因：
      1. 单图上限：001.jpg 有 9 个用例，不限制会把前 10 条全占满
      2. 单任务上限：hospital.pick_dept 有 22 条（科室列表的 22 个候选），
         它们本质是同一屏的不同选项，不该占掉 44% 的样本

    先按双上限挑一轮，不够再放宽单任务上限补，最后才放宽单图上限。
    """
    picked = []
    chosen = set()

    def take(cap_img, cap_task):
        used_img = {}
        used_task = {}
        for it in picked:
            used_img[it[2]["file"]] = used_img.get(it[2]["file"], 0) + 1
            t = it[2].get("task_id", "")
            used_task[t] = used_task.get(t, 0) + 1
        for it in graded:
            if len(picked) >= n:
                return
            if id(it[2]) in chosen:
                continue
            f = it[2]["file"]
            t = it[2].get("task_id", "")
            if used_img.get(f, 0) >= cap_img:
                continue
            if cap_task and used_task.get(t, 0) >= cap_task:
                continue
            picked.append(it)
            chosen.add(id(it[2]))
            used_img[f] = used_img.get(f, 0) + 1
            used_task[t] = used_task.get(t, 0) + 1

    take(per_image_cap, per_task_cap)
    take(per_image_cap, 0)
    take(999, 0)
    return picked[:n]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=50, help="取前 N 条")
    parser.add_argument("--cap", type=int, default=3, help="单张图最多贡献几条")
    parser.add_argument("--export", default="", help="导出成 jsonl 路径")
    parser.add_argument("--list", action="store_true", help="列出全部评级")
    args = parser.parse_args(argv)

    rows = [r for r in load_gt() if r.get("quality", "ok") != "bad"]
    graded = []
    for r in rows:
        p, why = grade(r)
        graded.append((p, why, r))

    # 排序：优先级 -> 链里第 1 步优先 -> 文件名
    graded.sort(key=lambda t: (t[0], int(t[2].get("step", 1) or 1), t[2]["file"]))

    if args.list:
        print(f"{'P':>3}  {'文件':<12} {'goal':<18} {'task_id':<32} 理由")
        print("-" * 96)
        for p, why, r in graded:
            print(f"{p:>3}  {Path(r['file']).name:<12} {r['goal']:<18} "
                  f"{r.get('task_id',''):<32} {why}")
        return 0

    picked = select_balanced(graded, args.n, args.cap)

    print("=" * 78)
    print(f"第一批要跑的 {len(picked)} 条")
    print("=" * 78)
    cur = None
    for i, (p, why, r) in enumerate(picked, 1):
        tid = r.get("task_id", "")
        if tid != cur:
            print(f"\n[{tid}]  (P{p} {why})")
            cur = tid
        print(f"  {i:>2}. {Path(r['file']).name:<12} "
              f"step{r.get('step',1)}  {r['goal']:<16} bbox={r['bbox']}")

    # 统计
    print("\n" + "=" * 78)
    pcount: Dict[int, int] = {}
    dcount: Dict[str, int] = {}
    fcount: Dict[str, int] = {}
    for p, why, r in picked:
        pcount[p] = pcount.get(p, 0) + 1
        dom = r.get("task_id", "").split(".")[0]
        dcount[dom] = dcount.get(dom, 0) + 1
        fcount[r["file"]] = fcount.get(r["file"], 0) + 1
    print(f"优先级分布: " + "  ".join(f"P{k}={v}" for k, v in sorted(pcount.items())))
    print(f"领域分布:   " + "  ".join(f"{k}={v}" for k, v in sorted(dcount.items(), key=lambda x: -x[1])))
    print(f"覆盖图片:   {len(fcount)} 张")
    print(f"多步链:     {len({r.get('task_id') for p, w, r in picked if sum(1 for x in rows if x.get('task_id')==r.get('task_id')) > 1})} 条")

    excluded = [(p, why, r) for p, why, r in graded if p >= 97]
    if excluded:
        print(f"\n首批排除 {len(excluded)} 条：")
        for p, why, r in excluded[:10]:
            print(f"  - {Path(r['file']).name} {r['goal']}: {why}")

    if args.export:
        out = Path(args.export)
        if not out.is_absolute():
            out = ROOT / out
        text = "\n".join(json.dumps(r, ensure_ascii=False) for _, _, r in picked) + "\n"
        out.write_text(text, encoding="utf-8")
        print(f"\n已导出到 {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
