#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索评测集：老人真实说法 → 正确的 task_id。

为什么要单独一套：
    定位评测（gt.jsonl）测的是「在这一屏能不能找到元素」，
    检索评测测的是「能不能听懂这句话指向哪条任务链」。
    两件事，混在一起报会互相掩盖（见交接文档 §十二）。

这份数据怎么来的：
    手工写的口语说法，刻意贴近老人真实表达，**不照抄 paths.jsonl 的 name**。
    其中一部分刻意与按钮字面不重合（"交税那个" vs "粤税通"），
    正是词面召回的盲区，用来验证向量召回有没有真正补上。

字段：
    query   老人原话
    task_id 应该命中的任务链
    note    为什么这么写（可选，方便后人理解）

用法：
    python tools/eval_retrieval.py --data data/rag/retrieval_eval.jsonl
"""

from __future__ import annotations

import json

# (query, task_id, note)
CASES = [
    # ---------- 微信：口语与按钮字面几乎不重合 ----------
    ("我想看看儿子", "wechat.video_call_son", "看=视频通话，字面完全不重合"),
    ("跟孙子说说话", "wechat.video_call_son", ""),
    ("想给孩子打个电话", "wechat.voice_call_son", "语音通话 vs 视频通话要区分"),
    ("把照片发过去", "wechat.send_photo", ""),
    ("现拍一张发给他", "wechat.take_photo", "拍摄 vs 相册"),
    ("给他转点钱", "wechat.transfer", ""),
    ("给孙子点零花钱", "wechat.red_packet", "零花钱→红包，词面盲区"),
    ("告诉他我在哪儿", "wechat.share_location", ""),
    ("让他能看到我在哪", "wechat.live_location", "共享实时位置"),
    ("我说话它自己打字", "wechat.voice_input", "语音输入"),
    ("最上面那个人的消息", "wechat.open_chat", ""),

    # ---------- 医院：同屏多个相似入口 ----------
    ("我想挂个号", "hospital.book_appointment", ""),
    ("帮我把老伴的信息加上", "hospital.add_patient", ""),
    ("给家里人加个就诊人", "hospital.manage_patient", "与 add_patient 易混"),
    ("看完病在哪儿交钱", "hospital.pay_bill", ""),
    ("药费在哪儿交", "hospital.rx_pay", "与 pay_bill 易混"),
    ("化验单在哪看", "hospital.report", "化验单→检验检查"),
    ("我的病历在哪看", "hospital.medical_record", ""),
    ("第一次来不知道去哪", "hospital.guide", "就诊指南"),
    ("给老人专用的看病通道", "hospital.elder_service", ""),
    ("我的账号在哪", "hospital.profile", "个人中心"),
    ("不知道该看什么科", "hospital.ai_triage", "智慧导诊"),
    ("这家医院怎么样", "hospital.intro", ""),
    ("给医院提意见", "hospital.survey", "满意度调查"),
    ("报销要的那个票据", "hospital.invoice", "门诊发票"),
    ("我想约个检查身体的", "hospital.physical_book", "体检预约"),
    ("上次查体的结果出来没", "hospital.physical_report", ""),
    ("住院要先交多少钱", "hospital.deposit", "住院押金"),
    ("出院结账", "hospital.discharge_pay", "住院结算"),
    ("每天花了多少钱", "hospital.daily_bill", "住院日清单"),
    ("手术做到哪一步了", "hospital.surgery", "手术进度"),
    ("病历能寄到家吗", "hospital.record_mail", "病案邮寄"),
    ("在网上问问大夫", "hospital.online_consult", "线上问诊"),
    ("护士能来家里吗", "hospital.home_nursing", "护理上门"),
    ("这个药能不能吃", "hospital.medicine", "用药查询"),
    ("想看看健康知识", "hospital.article", "科普文章"),
    ("我头晕，该挂哪个科", "hospital.pick_dept", ""),
    ("就找上次给我看的那个大夫", "hospital.pick_doctor", ""),

    # ---------- 政务：缩写、俗称 ----------
    ("去医院要出示的那个码", "gov.insurance_code", "医保电子凭证"),
    ("看病报销那个", "gov.insurance", ""),
    ("我卡里还有多少钱", "gov.insurance_balance", "医保账户余额"),
    ("我交了几年社保了", "gov.social_security", "个人参保信息"),
    ("退休金那个", "gov.pension", "退休金→养老保险，词面盲区"),
    ("我的卡在哪儿看", "gov.my_social_card", ""),
    ("要开社保证明", "gov.social_cert", "社保凭证"),
    ("没工作了要登记", "gov.unemployment", "失业登记"),
    ("在外地看病怎么弄", "gov.cross_region", "异地就医"),
    ("哪家医院能刷医保", "gov.hospital_query", "定点医院查询"),
    ("刷脸登录", "gov.login_face", "人脸识别认证"),
    ("用公安那个认证", "gov.login_wechat_police", "微警认证"),
    ("残疾人能领什么补贴", "gov.disability", "助残"),
    ("想找律师问问", "gov.legal_service", "法律服务"),
    ("困难了能申请补助吗", "gov.social_relief", "社会救助"),
    ("水电费在哪儿交", "gov.utility_pay", "生活缴费"),
    ("我要去看病", "gov.medical_service", "就医"),
    ("交税那个", "gov.tax", "粤税通，词面完全盲区"),
    ("广州本地办事的那个", "gov.suihaoban", "穗好办"),
    ("手机上办政务的那个入口", "gov.enter_yueshengshi", "粤省事"),
    ("帮我儿子也弄一下", "gov.family_service", "亲情服务"),
    ("买房那个钱怎么取", "gov.housing_fund", "公积金"),
    ("字太小了，弄大一点", "gov.elder_mode", "关怀版"),
    ("医保的事想问个明白", "gov.insurance_ai", "医保AI助手"),

    # ---------- 地图 ----------
    ("叫个车", "map.taxi", ""),
    ("坐公交还是坐地铁", "map.transit", ""),
    ("搭个顺路的车", "map.carpool", "顺风车"),
    ("看看地铁怎么坐", "map.subway_map", "地铁图"),
    ("公交车到哪了", "map.bus_live", "实时公交"),
    ("进地铁站要刷的", "map.subway_qr", "乘车码"),
]


def main() -> None:
    from pathlib import Path

    out = Path(__file__).resolve().parent.parent / "data" / "rag" / "retrieval_eval.jsonl"
    seen = set()
    rows = []
    for query, task_id, note in CASES:
        if query in seen:
            raise SystemExit(f"[FATAL] 重复 query：{query}")
        seen.add(query)
        rows.append({"query": query, "task_id": task_id, "note": note})
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"写入 {out}（{len(rows)} 条）")


if __name__ == "__main__":
    main()
