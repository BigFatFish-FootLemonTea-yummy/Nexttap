#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建 paths.jsonl 的 name 与 aliases。

## 为什么改

独立数据审查发现三个结构性问题：

1. **424/427 条 alias 是模板拼接**（`name` / `怎么+name` / `我要+name` / `帮我+name`），
   实测净贡献 **0 条**（救回 3 条、弄坏 3 条），却吃掉 **64.2% 的索引文本**。
   真正有信息量的只有 `gov.social_scan` 的 3 条。

2. **name 不是任务名，是「第一个可点按钮」**：
   114/120 条（95.0%）满足 `name == steps[0].target 去掉「点/选/确认/进/展开/查」`。
   后果是 111/120 条（92.5%）老人根本不会这么说：
     - 界面控件值：`待支付` `周六` `居民身份证` `下一步` `马上看看`（23 条）
     - 专有名词：`粤税通` `穗好办` `关怀版`（11 条）
     - 公文黑话：`门诊缴费` `城乡居民医保费申缴` `社会保险参保证明查询`（72 条）
   只有 9 条（7.5%）是老人真会说的。

3. **8 条链 steps 自循环**：`gov.insurance_code` 三步全是「点医保电子凭证」，
   `hospital.add_patient` 是「添加就诊人 → 我的家人 → 添加就诊人」。不可执行。

## 改什么

- `name`：改成「动作 + 对象」的任务名，且用老人能听懂的话
- `aliases`：删掉全部模板产物，改成真实口语说法（5–8 字）
- 自循环的 steps：去重
- 补 `app` 字段（原来 120/120 全空，检索时丢了 App 信息）

## 独立性声明

本文件的 name / aliases 是**独立构思**的，来源是：
  - 对 `steps[].target` 语义的理解
  - 老人日常说话的常识
**没有读取或照抄** `retrieval_eval.jsonl` / `retrieval_heldout.jsonl` /
`retrieval_full.jsonl` 里的 query 原文。这是为了避免「照评测集补词典」的循环论证
（之前踩过这个坑，见 docs/RAG实现记录.md）。

用法：
    python tools/rebuild_paths.py --dry-run    # 只看报告
    python tools/rebuild_paths.py --write      # 落盘（先备份）
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
PATHS = ROOT / "data" / "rag" / "paths.jsonl"

# ---------------------------------------------------------------------------
# 任务名改写 + 口语别名
#
# 格式：task_id: (新 name, [口语别名...])
# name 用「动作 + 对象」，老人能听懂；别名是老人可能说的其他说法。
# 不写界面上的黑话——那些本来就在 steps[].target 里，索引时已经用到了。
# ---------------------------------------------------------------------------
REWRITE: Dict[str, tuple] = {
    # ---------- 微信 ----------
    "wechat.video_call_son": ("给儿子打视频电话", ["想看看儿子", "跟孩子视频", "看看孩子"]),
    "wechat.voice_call_son": ("给儿子打电话", ["打个电话", "听听声音", "打电话过去"]),
    "wechat.send_photo": ("发相片给人家", ["发张相片给他", "发张照片", "发相片"]),
    "wechat.take_photo": ("现在照一张", ["拍一张", "现在照个相", "用相机拍"]),
    "wechat.transfer": ("把钱转过去", ["转点钱", "打钱过去", "给他转钱"]),
    "wechat.red_packet": ("发红包", ["发个红包", "压岁钱", "给点零花钱"]),
    "wechat.share_location": ("发个定位", ["告诉他我在哪", "发个位置"]),
    "wechat.live_location": ("共享位置给他看", ["实时共享位置", "让他看着我走"]),
    "wechat.voice_input": ("用说话代替打字", ["说话自动打字", "语音转文字"]),
    "wechat.open_chat": ("找到聊天记录", ["点开聊天", "找人聊天"]),
    "wechat.open_wechat": ("进微信里", ["回到微信", "进微信"]),

    # ---------- 医院：挂号主线 ----------
    "hospital.book_appointment": ("预约看大夫", ["想去医院看大夫", "挂个号看病", "看大夫要挂号"]),
    "hospital.add_patient": ("加个病人信息", ["把老伴的信息加上", "加个就诊人", "给家里人建档"]),
    "hospital.manage_patient": ("管理看病的人", ["加就诊人", "改就诊人信息"]),
    "hospital.confirm_book": ("核对挂号信息", ["信息都对下一步", "确认挂号详情"]),
    "hospital.pick_dept": ("选科室", ["挂哪个科", "看哪个科", "头晕挂什么科"]),
    "hospital.pick_doctor": ("挑医生", ["找哪个大夫", "挑个医生"]),
    "hospital.pick_slot": ("选看病时间", ["选个时间", "约几点"]),
    "hospital.pick_date": ("选择就诊日期", ["约哪天", "哪天去", "选日期"]),
    "hospital.pick_morning": ("选择上午或下午", ["上午去", "想早点去", "选上午下午"]),
    "hospital.pick_id_type": ("选用什么证件", ["用身份证", "用什么证件"]),
    "hospital.book_guide": ("挂号指引", ["挂号怎么弄", "第一次挂号", "挂号步骤"]),
    "hospital.book_notice": ("挂号须知", ["挂号要注意什么", "挂号须知在哪", "挂号前看什么"]),
    "hospital.dept_detail": ("看这个科介绍", ["这科看什么病", "科室介绍"]),
    "hospital.dept_intro": ("医院有哪些科", ["有哪些科", "科室都有啥"]),
    "hospital.expand_doctor": ("展开更多医生", ["还有别的大夫吗", "看全部医生", "更多医生"]),
    "hospital.doctor_team": ("看医生团队", ["这个医生还有谁", "大夫团队"]),
    "hospital.my_doctor": ("我常看的大夫", ["我一直看的大夫", "常看的医生"]),

    # ---------- 医院：就诊与缴费 ----------
    "hospital.check_in": ("到医院报到", ["到了医院签到", "报到"]),
    "hospital.pay_bill": ("交门诊的钱", ["看完病交钱", "交医药费"]),
    "hospital.pay_pending": ("还没付的订单", ["没交上的那笔", "还没付的钱"]),
    "hospital.pay_done": ("付完的订单", ["付过的单子", "交完钱的记录"]),
    "hospital.rx_pay": ("处方交钱", ["拿药的钱怎么付", "交药钱"]),
    "hospital.quick_pay": ("开通自动扣费", ["不用排队直接扣钱", "自动扣费"]),
    "hospital.invoice": ("开报销发票", ["报销要的票据", "开发票"]),
    "hospital.report": ("检查结果单", ["抽血结果查一下", "检查结果"]),
    "hospital.medical_record": ("看病档案", ["看病记录在哪", "我的病历"]),
    "hospital.visit_record": ("看过病的记录", ["以前看病的记录", "看过几次病"]),
    "hospital.physical_book": ("预约体检", ["约个检查身体", "预约体检"]),
    "hospital.physical_pay": ("交体检的钱", ["体检的钱怎么付", "交体检费"]),
    "hospital.physical_report": ("查体检结果", ["查体结果出来没", "体检报告"]),
    "hospital.online_consult": ("手机上看大夫", ["在网上问大夫", "手机上看病"]),
    "hospital.home_nursing": ("上门护理", ["护士能来家里", "上门护理"]),
    "hospital.medicine": ("查药怎么用", ["这药怎么吃", "查药"]),
    "hospital.article": ("看健康文章", ["看看健康知识", "养生文章"]),
    "hospital.ai_triage": ("帮我判断看哪科", ["不知道看什么科", "帮我判断挂哪科"]),
    "hospital.intro": ("医院简介", ["这医院好不好", "医院简介"]),
    "hospital.survey": ("评价医院服务", ["对医院有意见", "评价医院"]),
    "hospital.guide": ("看病流程指引", ["刚到医院不知道找谁", "看病流程"]),
    "hospital.profile": ("个人信息", ["我的信息在哪改", "我的资料"]),
    "hospital.card_manage": ("管理就诊卡", ["我的就诊卡在哪", "就诊卡", "看我的就诊卡"]),
    "hospital.bind_card": ("把就诊卡绑上", ["把卡绑上", "绑卡"]),
    "hospital.no_card": ("没办过就诊卡", ["我还没有卡", "没办过卡"]),
    "hospital.elder_service": ("老人看病专用", ["给老人专用的", "老人看病通道"]),
    "hospital.enter_app": ("进入白云区人民医院", ["白云区那家医院", "进白云医院", "白云医院"]),
    "hospital.enter_other": ("进入陆丰市人民医院", ["去陆丰那家医院", "进陆丰医院", "陆丰医院"]),

    # ---------- 医院：住院 ----------
    "hospital.deposit": ("交住院押金", ["住院先交多少钱", "交押金"]),
    "hospital.discharge_pay": ("住院费用结算", ["办出院手续", "出院算钱"]),
    "hospital.daily_bill": ("住院每天费用", ["每天花多少钱", "住院费用明细"]),
    "hospital.surgery": ("手术做到哪了", ["手术做到哪了", "手术情况"]),
    "hospital.record_mail": ("把病历寄来", ["病历本寄回家", "邮寄病历"]),

    # ---------- 政务：医保 ----------
    "gov.insurance_code": ("医保的二维码", ["看病要刷的那个码", "医保二维码"]),
    "gov.health_code": ("粤省事的码", ["政府办事的那个码", "健康码"]),
    "gov.health_card": ("电子健康卡在哪", ["去医院要刷的卡", "健康卡"]),
    "gov.insurance": ("医保在哪办", ["医药费报回来", "医保在哪"]),
    "gov.insurance_balance": ("医保还有多少钱", ["卡里还有多少钱", "医保余额"]),
    "gov.insurance_pay": ("用医保付钱", ["看病用医保结账", "医保付钱"]),
    "gov.insurance_anchor": ("定点医院在哪", ["哪家医院能报销", "定点医院"]),
    "gov.insurance_ai": ("问医保问题", ["医保的事想问", "医保咨询"]),
    "gov.insurance_resident": ("居民医保在哪", ["农村户口那种医保", "居民医保"]),
    "gov.insurance_flex": ("自己交的社保", ["自己交社保的", "个体户交社保"]),
    "gov.insurance_pay_fee": ("交居民医保费", ["居民医保的钱该交了", "交居民医保"]),
    "gov.local_medical": ("就在本市看病", ["就在本地看病", "本地看病"]),
    "gov.cross_region": ("异地看病备案", ["外地看病怎么报", "跨省看病"]),
    "gov.reimbursement": ("医疗费报销", ["看病花的钱报回来", "报销医药费"]),
    "gov.hospital_query": ("查能报销的医院", ["哪家医院能用医保", "查定点医院"]),

    # ---------- 政务：社保养老 ----------
    "gov.social_security": ("参保情况", ["我交了几年社保", "社保交了多少年"]),
    "gov.social_cert": ("社保的凭证", ["单位要社保的证明", "社保证明"]),
    "gov.social_cert_query": ("参保证明在哪开", ["单位要交的证明", "参保证明"]),
    "gov.rights_record": ("我的权益单", ["我的参保记录", "权益单"]),
    "gov.rights_query": ("社保缴费信息查询", ["社保缴费记录", "缴费明细", "查缴费"]),
    "gov.pension": ("养老保险", ["养老金在哪查", "养老金", "养老保险查询"]),
    "gov.pension_query": ("农村养老保险", ["我养老保险交了多少", "农保查询"]),
    "gov.pension_proof": ("开养老金证明", ["退休金证明怎么开", "开养老金证明"]),
    "gov.unemployment": ("办失业登记", ["失业了要办手续", "办失业登记"]),
    "gov.unemployment_change": ("改失业信息", ["失业金信息要改", "改失业信息"]),
    "gov.work_injury": ("改工伤信息", ["工伤信息要改", "改工伤信息"]),
    "gov.my_social_card": ("我的社保卡", ["社保卡在哪", "我的社保卡呢", "查社保卡"]),
    "gov.social_card_query": ("查社保卡信息", ["社保卡里有什么", "查社保卡"]),
    "gov.social_card_service": ("社保卡服务", ["社保卡办理", "补办社保卡", "社保卡业务"]),
    "gov.social_scan": ("扫一扫", ["扫个码", "扫社保卡二维码", "扫一下"]),
    "gov.family_service": ("亲情服务", ["替孩子办个事", "帮家人办", "亲情号"]),
    "gov.face_verify": ("刷脸验证", ["照一下我的脸", "刷脸验证"]),

    # ---------- 政务：登录认证 ----------
    "gov.login_face": ("人脸认证", ["对着脸认证", "刷脸"]),
    "gov.login_wechat_police": ("微警认证", ["公安那个实名", "公安认证"]),
    "gov.login_next": ("继续下一步", ["继续往下走", "往下"]),
    "gov.real_name": ("办实名", ["要证明我是我", "实名"]),

    # ---------- 政务：其他服务 ----------
    "gov.housing_fund": ("公积金在哪", ["公积金取出来", "住房公积金"]),
    "gov.tax": ("粤税通", ["报税的地方", "交税", "税务"]),
    "gov.suihaoban": ("穗好办", ["广州本地服务", "穗好办在哪", "广州办事"]),
    "gov.enter_yueshengshi": ("进入粤省事", ["打开粤省事", "粤省事", "办政务"]),
    "gov.digital_space": ("电子证照", ["我的电子证件在哪", "电子证件"]),
    "gov.quick_service": ("马上办的事", ["马上能办的那个", "即办"]),
    "gov.elder_mode": ("大字版界面", ["字太小弄大点", "大字版"]),
    "gov.disability": ("残疾人服务", ["残疾有补助吗", "残疾补贴"]),
    "gov.legal_service": ("找律师", ["有法律问题想问", "找律师"]),
    "gov.social_relief": ("申请救助", ["困难了能申请补助", "困难补助"]),
    "gov.utility_pay": ("交水电燃气", ["交水费电费", "交水电费"]),
    "gov.medical_service": ("去医院看病", ["我要去医院", "去看病"]),
    "gov.driving": ("驾照业务", ["驾照到期换证", "驾照"]),
    "gov.exit_entry": ("办护照", ["办护照要预约", "护照"]),
    "gov.market_supervise": ("商家投诉", ["投诉商家", "办执照"]),
    "gov.start_service": ("开始办", ["可以开始了", "开始"]),
    "gov.promo": ("马上看看", ["看看活动", "有什么活动", "活动入口"]),

    # ---------- 地图 ----------
    "map.taxi": ("叫车", ["打个出租车", "叫出租车"]),
    "map.transit": ("公交地铁查询", ["怎么坐车去", "查公交"]),
    "map.carpool": ("拼车", ["跟人拼个车", "拼车"]),
    "map.subway_map": ("地铁线路图", ["地铁线路", "地铁线路图"]),
    "map.bus_live": ("公交实时位置", ["公交还有几分钟", "公交到站"]),
    "map.subway_qr": ("地铁乘车码在哪", ["地铁扫码进站", "扫码坐地铁"]),
}

# App 归属（原来 app 字段 120/120 全空，检索时丢了 App 信息）
APP_OF = {
    "wechat": "wechat",
    "hospital": "hospital",
    "gov": "yueshengshi",
    "map": "map",
}


def app_for(task_id: str) -> str:
    return APP_OF.get(task_id.split(".")[0], "")


def dedup_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """去掉自循环的 steps（同一 target 出现多次）。"""
    seen = set()
    out = []
    for s in steps:
        t = str(s.get("target") or "").strip()
        if t and t in seen:
            continue
        seen.add(t)
        out.append(s)
    # 重新编号
    for i, s in enumerate(out, 1):
        s["step"] = i
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="重建 paths.jsonl 的 name 与 aliases")
    ap.add_argument("--write", action="store_true", help="真正落盘（默认只干跑）")
    ap.add_argument("--dry-run", action="store_true", help="只打印报告")
    args = ap.parse_args(argv)
    write = args.write and not args.dry_run

    rows = [json.loads(l) for l in PATHS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print(f"[FATAL] 读不到 {PATHS}")
        return 2

    missing = [r["task_id"] for r in rows if r["task_id"] not in REWRITE]
    if missing:
        print(f"[FATAL] 有 {len(missing)} 条任务链没有改写方案：")
        for m in missing:
            print(f"    {m}")
        return 1

    old_alias = sum(len(r.get("aliases") or []) for r in rows)
    new_alias = 0
    old_name_len = sum(len(str(r.get("name") or "")) for r in rows)
    new_name_len = 0
    loops_fixed = 0
    changed_name = 0

    print("=" * 72)
    print("1. 改写预览（前 12 条）")
    print("=" * 72)
    for i, r in enumerate(rows):
        tid = r["task_id"]
        new_name, new_aliases = REWRITE[tid]
        old_name = str(r.get("name") or "")
        if old_name != new_name:
            changed_name += 1
        if len(r.get("steps") or []) != len(dedup_steps(list(r.get("steps") or []))):
            loops_fixed += 1
        if i < 12:
            print(f"  {tid}")
            print(f"    name:    {old_name!r} -> {new_name!r}")
            print(f"    aliases: {len(r.get('aliases') or [])} 条 -> {len(new_aliases)} 条 {new_aliases}")

    for r in rows:
        tid = r["task_id"]
        new_name, new_aliases = REWRITE[tid]
        r["name"] = new_name
        r["aliases"] = list(new_aliases)
        r["app"] = app_for(tid)
        r["steps"] = dedup_steps(list(r.get("steps") or []))
        new_alias += len(new_aliases)
        new_name_len += len(new_name)

    print()
    print("=" * 72)
    print("2. 统计")
    print("=" * 72)
    print(f"  改名           : {changed_name}/{len(rows)}")
    print(f"  alias 总数     : {old_alias} -> {new_alias}")
    print(f"  修自循环的链   : {loops_fixed}")
    print(f"  补 app 字段    : {len(rows)} 条")
    print(f"  name 总字数    : {old_name_len} -> {new_name_len}")

    # 校验：新 alias 不能与任何评测集 query 完全相同（防止反向泄漏）
    eval_dir = ROOT / "data" / "rag"
    leaks = []
    for f in ("retrieval_eval.jsonl", "retrieval_heldout.jsonl", "retrieval_full.jsonl"):
        p = eval_dir / f
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            q = json.loads(line)["query"].strip()
            for r in rows:
                if q == r["name"] or q in r["aliases"]:
                    leaks.append((f, q, r["task_id"]))
    print()
    print("=" * 72)
    print("3. 泄漏检查（新 name/alias 是否与评测集 query 完全相同）")
    print("=" * 72)
    if leaks:
        print(f"  发现 {len(leaks)} 处完全相同：")
        for f, q, tid in leaks[:20]:
            print(f"    {f}: {q!r} == {tid}")
        print("  （完全相同意味着评测时是「送分题」，需要改掉）")
    else:
        print("  PASS 没有与评测集 query 完全相同的 name/alias")

    if not write:
        print()
        print("[DRY-RUN] 未写入。确认后加 --write")
        return 0

    bak = PATHS.with_suffix(".jsonl.bak-rebuild")
    if not bak.exists():
        shutil.copy2(PATHS, bak)
        print(f"\n  备份 -> {bak.name}")

    tmp = PATHS.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, PATHS)
    print(f"  写入 {PATHS.name}（{len(rows)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
