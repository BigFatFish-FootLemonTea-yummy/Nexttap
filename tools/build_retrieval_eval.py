# -*- coding: utf-8 -*-
"""Rebuild data/rag/retrieval_{eval,heldout,full}.jsonl for the new 30 chains.

Writes into D:\\Nexttap\\data\\rag\\ and runs a leakage check on the heldout set.
"""
from __future__ import annotations

import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(r"D:\Nexttap")
RAG = REPO / "data" / "rag"

PATHS = [json.loads(l) for l in (RAG / "paths.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
VALID = {p["task_id"] for p in PATHS}
ALIASES = {p["task_id"]: list(p.get("aliases") or []) for p in PATHS}
NAMES = {p["task_id"]: p.get("name", "") for p in PATHS}

# ---------------------------------------------------------------------------
# 1. retrieval_eval.jsonl —— 回归基线集（3 条/链）
# ---------------------------------------------------------------------------
EVAL = [
    # ---- 微信 ----
    ("想跟老伴说说话", "wechat.call", "目的是「说说话」，不含任何按钮字面"),
    ("想看看孩子，跟他视个频", "wechat.call", "「视个频」是老人对视频通话的说法"),
    ("按哪儿能跟人通话", "wechat.call", "只记得「通话」两个字"),
    ("我要给老李发个消息，找不着他人", "wechat.open_chat_v2", "先找人再聊天"),
    ("想给一个人发消息，翻来翻去找不到他", "wechat.open_chat_v2", "描述的是「翻不到人」"),
    ("怎么从微信里翻出某个人的聊天", "wechat.open_chat_v2", ""),
    ("在店里买东西，要照商家的码给钱", "wechat.pay_scan", "商家码 vs 我的码，最易混的一对"),
    ("怎么拿手机对着码给钱", "wechat.pay_scan", ""),
    ("买东西不想给现金，扫一下就行", "wechat.pay_scan", ""),
    ("收银的要扫我，码在哪儿", "wechat.pay_qr", "方向相反：别人扫我"),
    ("我付钱给他扫，怎么弄", "wechat.pay_qr", ""),
    ("超市结账，让人拿机器照我手机", "wechat.pay_qr", ""),
    ("我知道他电话号码，想加他微信", "wechat.friend_phone", "唯一能区分加好友三兄弟的线索是「号码」"),
    ("加个人，手上只有手机号", "wechat.friend_phone", ""),
    ("有个号码，怎么把人加成好友", "wechat.friend_phone", ""),
    ("我那个二维码在哪儿，让人扫一下", "wechat.friend_qr_show", "我的码给别人扫"),
    ("他想加我，我该给他看什么", "wechat.friend_qr_show", ""),
    ("怎么把我自己推给别人加", "wechat.friend_qr_show", ""),
    ("他给我一个码，让我扫一下", "wechat.friend_qr_scan", "别人的码给我扫"),
    ("用手机照一下那个码把人加上", "wechat.friend_qr_scan", ""),
    ("对面递过来二维码，怎么加他", "wechat.friend_qr_scan", ""),
    ("懒得打字，直接对着手机讲两句", "wechat.voice_msg", "语音消息 vs 语音通话要区分"),
    ("按住哪儿说话", "wechat.voice_msg", ""),
    ("发一条我自己的声音给他", "wechat.voice_msg", ""),
    ("我找不到路了，怎么让人来接我", "wechat.share_location_v2", "说的是处境，不是按钮"),
    ("让人知道我现在在哪个地方", "wechat.share_location_v2", ""),
    ("闺女问我到哪儿了，我怎么发给她", "wechat.share_location_v2", ""),
    ("举起手机就照，不用以前存的", "wechat.take_photo_v2", "现拍 vs 相册"),
    ("不用相册里的，现在照一张", "wechat.take_photo_v2", ""),
    ("手机举起来照一下再发过去", "wechat.take_photo_v2", ""),

    # ---- 白云区人民医院 ----
    ("怎么进白云区人民医院那个页面", "baiyun.open_app", "只想知道门在哪"),
    ("想进白云那家医院的小程序", "baiyun.open_app", ""),
    ("白云区人民医院从哪儿点进去", "baiyun.open_app", ""),
    ("白云医院想约个号", "baiyun.book", "约号 = 挂号"),
    ("身上不舒服，白云医院挂哪个科", "baiyun.book", "先选科室，属挂号链路"),
    ("白云医院看门诊要先做什么", "baiyun.book", ""),
    ("白云医院要给老伴添个看病的名字", "baiyun.add_patient", "「添名字」= 加就诊人"),
    ("第一次去白云医院，家里人信息怎么录", "baiyun.add_patient", ""),
    ("白云医院把儿子的信息也加上", "baiyun.add_patient", ""),
    ("白云医院看完病了，钱在哪儿交", "baiyun.pay", "缴费 vs 查记录，最易混"),
    ("白云医院开的单子要付钱", "baiyun.pay", ""),
    ("白云医院这次看病的费用怎么结", "baiyun.pay", ""),
    ("白云医院那个医保的码在首页哪儿", "baiyun.insurance_path1", "从医院首页进"),
    ("在白云医院要刷医保，从哪儿点进去", "baiyun.insurance_path1", ""),
    ("白云医院结算要用医保凭证", "baiyun.insurance_path1", ""),
    ("不想在医院页面里绕，换个地方找那个码", "baiyun.insurance_path2", "不走医院小程序"),
    ("微信搜一搜能搜到医保凭证吗", "baiyun.insurance_path2", ""),
    ("不进白云医院，在微信里把医保码弄出来", "baiyun.insurance_path2", ""),
    ("白云医院以前看过的病在哪儿翻", "baiyun.records", "记录类，与缴费要分开"),
    ("我在白云医院看过几回，想查查", "baiyun.records", ""),
    ("白云医院上次开的什么药，能查到吗", "baiyun.records", ""),

    # ---- 陆丰市人民医院 ----
    ("陆丰市人民医院那个小程序怎么找", "lufeng.open_app", ""),
    ("想进陆丰那家医院", "lufeng.open_app", ""),
    ("陆丰市人民医院从哪儿点进去", "lufeng.open_app", ""),
    ("陆丰医院想约个号", "lufeng.book", ""),
    ("去陆丰医院看病，号怎么挂", "lufeng.book", ""),
    ("陆丰医院想找个大夫看看", "lufeng.book", ""),
    ("陆丰医院要添个家里人的信息", "lufeng.add_patient", ""),
    ("给老伴在陆丰医院挂个名字", "lufeng.add_patient", ""),
    ("陆丰医院第一次去，儿子信息怎么加", "lufeng.add_patient", ""),
    ("陆丰医院看完病了，钱在哪儿交", "lufeng.pay", ""),
    ("陆丰医院开的单子要付钱", "lufeng.pay", ""),
    ("陆丰医院这次看病的费用怎么结", "lufeng.pay", ""),
    ("陆丰医院那边要电子医保卡，怎么弄出来", "lufeng.insurance", ""),
    ("在陆丰医院看病医保怎么用", "lufeng.insurance", ""),
    ("陆丰医院结算要用医保凭证", "lufeng.insurance", ""),
    ("陆丰医院以前交的钱在哪儿看", "lufeng.records", ""),
    ("我在陆丰医院花过多少，想查一下", "lufeng.records", ""),
    ("陆丰医院上次看病花了多少", "lufeng.records", ""),

    # ---- 中山三院 ----
    ("中山三院网上看病那个门怎么开", "sysu.open_app", ""),
    ("三院的小程序在哪儿", "sysu.open_app", ""),
    ("想上中山三院看病，先怎么进去", "sysu.open_app", ""),
    ("中山三院想约个号", "sysu.book", ""),
    ("去三院看病，号怎么挂", "sysu.book", ""),
    ("中山三院想找个大夫看看", "sysu.book", ""),
    ("中山三院要给老伴添个名字", "sysu.add_patient", ""),
    ("三院第一次去，家里人信息怎么加", "sysu.add_patient", ""),
    ("中山三院把儿子的信息也加上", "sysu.add_patient", ""),
    ("中山三院看完病了，钱在哪儿交", "sysu.pay", ""),
    ("三院开的单子要付钱", "sysu.pay", ""),
    ("中山三院这次看病的费用怎么结", "sysu.pay", ""),
    ("中山三院那边要电子医保卡，怎么弄出来", "sysu.insurance", ""),
    ("在三院看病医保怎么用", "sysu.insurance", ""),
    ("中山三院结算要用医保凭证", "sysu.insurance", ""),
    ("中山三院以前挂的号在哪儿翻", "sysu.records", "挂号记录，不是就诊记录"),
    ("我在三院挂过哪些号，想查查", "sysu.records", ""),
    ("三院上回约的号还能查到吗", "sysu.records", ""),
    ("中山三院老是让我登录，怎么弄", "sysu.login", ""),
    ("三院要手机号登录，在哪儿登", "sysu.login", ""),
    ("中山三院进不去，说要先登录", "sysu.login", ""),
]

# ---------------------------------------------------------------------------
# 2. retrieval_heldout.jsonl —— 留出集（刻意避开 aliases 用词）
# ---------------------------------------------------------------------------
HELDOUT = [
    ("手机上怎么才能互相看见着讲话", "wechat.call", "说「看见着讲话」，不说「视频通话」"),
    ("想看见对方的脸再聊", "wechat.call", ""),
    ("通讯录里找不到要联系的那位", "wechat.open_chat_v2", "用「通讯录」代替「微信」"),
    ("想进某个人的页面", "wechat.open_chat_v2", ""),
    ("在商店里照一下那个方块就把账结了", "wechat.pay_scan", "用「方块」代指二维码"),
    ("摊子上买东西，怎么给人家钱", "wechat.pay_scan", ""),
    ("柜台的人拿机器照我手机，我该亮哪个", "wechat.pay_qr", ""),
    ("轮到我结账，得让人家照我的手机", "wechat.pay_qr", ""),
    ("只知道他的电话号码，别的啥也没有", "wechat.friend_phone", ""),
    ("有个号码在我本子上，想把人弄进微信", "wechat.friend_phone", ""),
    ("怎么让别人把我存到他手机里", "wechat.friend_qr_show", "「存到手机里」= 加好友"),
    ("他要加我，我这边该亮什么", "wechat.friend_qr_show", ""),
    ("他手机上有个方块，我照一下就成朋友了", "wechat.friend_qr_scan", ""),
    ("对面递过来一张卡片让我照一下", "wechat.friend_qr_scan", ""),
    ("一个字一个字敲太慢，直接讲", "wechat.voice_msg", "说「敲太慢」，不说「打字」"),
    ("想把我的声音传给他", "wechat.voice_msg", ""),
    ("家里人问我在哪，我怎么给他说清楚", "wechat.share_location_v2", ""),
    ("我在外头不认路，怎么让人找过来", "wechat.share_location_v2", ""),
    ("拿手机当场拍一个给他", "wechat.take_photo_v2", ""),
    ("眼前这东西想马上给他看", "wechat.take_photo_v2", ""),
    ("刚拍的那张，想立刻就给他看", "wechat.take_photo_v2", ""),
    ("白云区那家公立医院，手机上从哪儿进去", "baiyun.open_app", ""),
    ("在手机上把白云区的医院调出来", "baiyun.open_app", ""),
    ("白云医院想找个大夫看看，先干哪一步", "baiyun.book", ""),
    ("白云医院看门诊要怎么排上", "baiyun.book", ""),
    ("白云医院要带上老伴一起看，她的资料怎么录", "baiyun.add_patient", ""),
    ("白云医院第一次用，家里人的资料从哪儿填", "baiyun.add_patient", ""),
    ("白云医院看完病了，账还没结", "baiyun.pay", ""),
    ("白云医院开的收费单子怎么处理", "baiyun.pay", ""),
    ("白云医院收银的地方要我亮个东西", "baiyun.insurance_path1", ""),
    ("白云医院往下拉，找医保那一栏", "baiyun.insurance_path1", ""),
    ("不在医院页面里，另外找个地方把码弄出来", "baiyun.insurance_path2", ""),
    ("在微信上搜一搜，能不能弄出那个医保的码", "baiyun.insurance_path2", ""),
    ("白云医院前几回看病的事，想翻出来看看", "baiyun.records", ""),
    ("白云医院以前开的什么药，还能不能查到", "baiyun.records", ""),
    ("陆丰市那家公立医院，手机上从哪儿进去", "lufeng.open_app", ""),
    ("在手机上把陆丰的医院调出来", "lufeng.open_app", ""),
    ("陆丰医院想找个大夫看看，先干哪一步", "lufeng.book", ""),
    ("陆丰医院看门诊要怎么排上", "lufeng.book", ""),
    ("陆丰医院要带上老伴一起看，她的资料怎么录", "lufeng.add_patient", ""),
    ("陆丰医院第一次用，家里人的资料从哪儿填", "lufeng.add_patient", ""),
    ("陆丰医院看完病了，账还没结", "lufeng.pay", ""),
    ("陆丰医院开的收费单子怎么处理", "lufeng.pay", ""),
    ("陆丰医院收银的地方要我亮个东西", "lufeng.insurance", ""),
    ("陆丰医院结账时要用医保那个东西", "lufeng.insurance", ""),
    ("陆丰医院前几回看病的事，想翻出来看看", "lufeng.records", ""),
    ("陆丰医院以前开的什么药，还能不能查到", "lufeng.records", ""),
    ("中山三院在手机上的那个入口怎么找", "sysu.open_app", ""),
    ("三院网上看病的地方从哪儿进", "sysu.open_app", ""),
    ("中山三院想找个大夫看看，先干哪一步", "sysu.book", ""),
    ("中山三院看门诊要怎么排上", "sysu.book", ""),
    ("中山三院要带上老伴一起看，她的资料怎么录", "sysu.add_patient", ""),
    ("中山三院第一次用，家里人的资料从哪儿填", "sysu.add_patient", ""),
    ("中山三院看完病了，账还没结", "sysu.pay", ""),
    ("中山三院开的收费单子怎么处理", "sysu.pay", ""),
    ("中山三院收银的地方要我亮个东西", "sysu.insurance", ""),
    ("中山三院结账时要用医保那个东西", "sysu.insurance", ""),
    ("中山三院前几回约的，想翻出来看看", "sysu.records", ""),
    ("中山三院以前约过哪个大夫，还能不能查到", "sysu.records", ""),
    ("中山三院得先有自己的号才能用", "sysu.login", ""),
    ("中山三院老弹出来让我弄手机号", "sysu.login", ""),
]

# 医院名 / App 名是「实体」，不是「答案」。留出集当然要提到它们，
# 否则三家医院的任务无法区分。泄漏检查先把这些实体词遮掉，
# 再看剩下的动作词有没有撞车。
BRAND = [
    "白云区人民医院", "白云医院", "白云区", "白云",
    "陆丰市人民医院", "陆丰医院", "陆丰市", "陆丰",
    "中山大学附属第三医院", "中山三院", "三院",
    "互联网医院", "微信",
]


def norm(s: str) -> str:
    return "".join(str(s).split()).lower()


def lcs_len(a: str, b: str) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def mask_brand(s: str) -> str:
    for b in sorted(BRAND, key=len, reverse=True):
        s = s.replace(b, "")
    return s


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def difficulty(query: str, task_id: str) -> str:
    """难度只看 query 与 name/aliases 的字面重合度，与 guide 的打分实现无关。"""
    q = norm(query)
    cands = [norm(NAMES[task_id])] + [norm(a) for a in ALIASES[task_id]]
    best = max(ratio(q, c) for c in cands)
    best = max(best, max((lcs_len(q, c) / max(1, len(q)) for c in cands), default=0.0))
    if best >= 0.75:
        return "easy"
    if best >= 0.5:
        return "medium"
    return "hard"


def check_set(rows, label, strict_leak):
    errs = []
    seen = set()
    for q, tid, _note in rows:
        if tid not in VALID:
            errs.append("[%s] 失效 task_id: %s (%r)" % (label, tid, q))
        if not q.strip():
            errs.append("[%s] 空 query" % label)
        if q in seen:
            errs.append("[%s] 重复 query: %r" % (label, q))
        seen.add(q)
    covered = {tid for _q, tid, _n in rows}
    missing = VALID - covered
    if missing:
        errs.append("[%s] 未覆盖 task_id: %s" % (label, sorted(missing)))

    leak = []
    if strict_leak:
        for q, tid, _note in rows:
            qn = norm(q)
            qm = mask_brand(qn)
            for alias in ALIASES[tid]:
                an = norm(alias)
                am = mask_brand(an)
                if qn == an:
                    leak.append((q, tid, alias, "完全相等", 1.0, lcs_len(qm, am)))
                    continue
                if qn in an or an in qn:
                    leak.append((q, tid, alias, "子串包含", ratio(qn, an), lcs_len(qm, am)))
                    continue
                # 两套口径都要过，但**各自查什么**不一样：
                #   未遮蔽 —— 只查整句相似度。这里不能查 LCS：「白云医院」四个字
                #             本身就占满 4 字阈值，一查就是满屏假阳性。
                #   遮蔽后 —— 查相似度和 LCS。品牌名抹掉之后，剩下的才是
                #             「动作词有没有撞车」，LCS 才有意义。
                # 早先只查「未遮蔽 ratio + 遮蔽后 LCS」，漏掉了遮蔽后 ratio，
                # 放过了一条 0.606 的样本。
                r_u = ratio(qn, an)
                r_m, l_m = ratio(qm, am), lcs_len(qm, am)
                if r_u >= 0.6 or r_m >= 0.6 or l_m >= 4:
                    leak.append((q, tid, alias,
                                 "高相似(未遮蔽 ratio=%.2f | 遮蔽 ratio=%.2f LCS=%d字)"
                                 % (r_u, r_m, l_m),
                                 max(r_u, r_m), l_m))
    return errs, leak


def main() -> int:
    e_errs, _ = check_set(EVAL, "eval", False)
    h_errs, h_leak = check_set(HELDOUT, "heldout", True)
    errs = e_errs + h_errs
    if errs:
        print("\n".join(errs))
        return 1

    # 留出集里也不该出现和 eval 集一模一样的句子
    dup = {q for q, _t, _n in EVAL} & {q for q, _t, _n in HELDOUT}
    if dup:
        print("eval 与 heldout 有重复 query:", dup)
        return 1

    if h_leak:
        print("留出集泄漏检查未通过（%d 处）：" % len(h_leak))
        for q, tid, alias, kind, r, l in h_leak[:40]:
            print("  %r -> %s | alias=%r | %s ratio=%.2f 遮蔽后LCS=%d" % (q, tid, alias, kind, r, l))
        return 1

    # ---- 写文件 ----
    def dump(path, rows, with_difficulty):
        with path.open("w", encoding="utf-8", newline="\n") as f:
            for q, tid, note in rows:
                rec = {"query": q, "task_id": tid}
                if with_difficulty:
                    rec["difficulty"] = difficulty(q, tid)
                rec["note"] = note
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    dump(RAG / "retrieval_eval.jsonl", EVAL, False)
    dump(RAG / "retrieval_heldout.jsonl", HELDOUT, False)

    full = list(EVAL) + list(HELDOUT)
    dump(RAG / "retrieval_full.jsonl", full, True)

    # ---- 报告 ----
    print("retrieval_eval.jsonl    %3d 条 / %2d 个 task_id" % (len(EVAL), len({t for _q, t, _n in EVAL})))
    print("retrieval_heldout.jsonl %3d 条 / %2d 个 task_id" % (len(HELDOUT), len({t for _q, t, _n in HELDOUT})))
    print("retrieval_full.jsonl    %3d 条 / %2d 个 task_id" % (len(full), len({t for _q, t, _n in full})))

    dist = {}
    for q, tid, _n in full:
        dist[difficulty(q, tid)] = dist.get(difficulty(q, tid), 0) + 1
    print("难度分布：", dict(sorted(dist.items())))

    # 留出集相似度统计
    stats = []
    for q, tid, _n in HELDOUT:
        qn, qm = norm(q), mask_brand(norm(q))
        best_r, best_l = 0.0, 0
        for alias in ALIASES[tid]:
            an, am = norm(alias), mask_brand(norm(alias))
            best_r = max(best_r, ratio(qn, an))
            best_l = max(best_l, lcs_len(qm, am))
        stats.append((best_r, best_l))
    print("留出集 vs 本任务 aliases：最大 ratio=%.3f，最大遮蔽后LCS=%d 字"
          % (max(s[0] for s in stats), max(s[1] for s in stats)))
    print("  完全相等 0 条 / 子串包含 0 条 / ratio>=0.6 的 %d 条 / 遮蔽后LCS>=4 的 %d 条"
          % (sum(1 for r, _l in stats if r >= 0.6), sum(1 for _r, l in stats if l >= 4)))

    # eval 集与 aliases 的重合度（只报告，不强制）
    #
    # 基线集不要求零重合：它就是「对着同一批任务再问一遍」的回归基线，
    # 和 aliases 撞一点是正常的。但撞太多就没信息量了——
    # 旧数据就是因为「看着失败项补别名」变成自评，所以这里把最像的几条打出来。
    exact = 0
    worst_eval = []
    for q, tid, _n in EVAL:
        qn, qm = norm(q), mask_brand(norm(q))
        if qn in {norm(a) for a in ALIASES[tid]}:
            exact += 1
        bm = bl = 0.0
        bl = 0
        for alias in ALIASES[tid]:
            an, am = norm(alias), mask_brand(norm(alias))
            bm = max(bm, ratio(qm, am))
            bl = max(bl, lcs_len(qm, am))
        worst_eval.append((bm, bl, q, tid))
    worst_eval.sort(reverse=True)
    print("eval 集与本任务 alias 完全相等的条数：%d / %d" % (exact, len(EVAL)))
    print("eval 集 vs 本任务 alias（遮蔽品牌后）：ratio>=0.6 的 %d 条，LCS>=4 的 %d 条"
          % (sum(1 for b, _l, _q, _t in worst_eval if b >= 0.6),
             sum(1 for _b, l, _q, _t in worst_eval if l >= 4)))
    print("  最像的 5 条（基线集允许重合，列出来便于后人判断）：")
    for bm, bl, q, tid in worst_eval[:5]:
        print("    ratio=%.3f LCS=%d  %r -> %s" % (bm, bl, q, tid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
