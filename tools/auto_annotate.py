"""自动标注：用 OCR 文字坐标 + 像素分析定位可点区域，生成 gt.jsonl。

核心思路：
    1. OCR 给出「文字在哪」（精确到像素）
    2. 图标在文字正上方，用像素扫描找出图标的真实上下边界
    3. 合并成「图标 + 文字」的可点区域

命中判定是「预测框中心落在真值框内」，所以真值框必须同时覆盖
图标和文字 —— 模型指哪个都算对。

跑法：
    python tools/auto_annotate.py --dry-run    # 只打印，不写文件
    python tools/auto_annotate.py              # 写入 gt.jsonl
    python tools/auto_annotate.py --preview    # 顺便生成预览图
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "data" / "eval" / "images"
OCR_DIR = ROOT / "data" / "eval" / "_ocr"
GT = ROOT / "data" / "eval" / "gt.jsonl"
PREVIEW_DIR = ROOT / "data" / "eval" / "_preview"

Box = List[int]


# --------------------------------------------------------------------------
# 像素分析：找出文字上方的图标范围
# --------------------------------------------------------------------------

def _is_content(px: Tuple[int, int, int], bg: Tuple[int, int, int], tol: int = 18) -> bool:
    return (abs(px[0] - bg[0]) > tol or abs(px[1] - bg[1]) > tol
            or abs(px[2] - bg[2]) > tol)


def background_color(im: Image.Image, text_box: Box) -> Tuple[int, int, int]:
    """取文字框左右两侧的空白处作为背景色。"""
    x0, y0, x1, y1 = text_box
    w, h = im.size
    cy = (y0 + y1) // 2
    for sx in (max(0, x0 - 12), min(w - 1, x1 + 12)):
        try:
            return im.getpixel((sx, cy))[:3]
        except Exception:
            continue
    return (255, 255, 255)


def is_gradient_background(im: Image.Image, text_box: Box) -> bool:
    """判断文字周围是不是彩色/渐变背景（例如顶部的橙色卡片）。

    做法：取文字框左右各 30px 之外的几列像素，
    看它们的颜色差异是否很大。渐变背景会让「找图标」失效。
    """
    x0, y0, x1, y1 = text_box
    w, h = im.size
    samples = []
    for dx in (-40, -30, 30, 40):
        for fy in (0.2, 0.5, 0.8):
            x = int((x0 if dx < 0 else x1) + dx)
            y = int(y0 + (y1 - y0) * fy)
            if 0 <= x < w and 0 <= y < h:
                samples.append(im.getpixel((x, y))[:3])
    if len(samples) < 3:
        return True
    for ch in range(3):
        vals = [s[ch] for s in samples]
        if max(vals) - min(vals) > 40:
            return True
    return False


def find_icon_above(im: Image.Image, text_box: Box, max_scan: int = 300) -> Optional[Box]:
    """在文字正上方扫描，找出连续的内容块（图标）范围。

    图标和文字之间通常有 20-60px 空隙，所以空隙容忍度要够大（默认 45px），
    否则永远找不到图标（实测曾因 gap<6 导致所有框只框住文字）。

    关键约束：图标高度不超过文字高度的 3 倍，否则会把整块横幅圈进去。
    """
    x0, y0, x1, y1 = text_box
    w, h = im.size
    text_h = max(1, y1 - y0)
    text_w = max(1, x1 - x0)
    limit = min(max_scan, int(text_h * 4.5))
    bg = background_color(im, text_box)
    # 图标与文字的空隙实测 20–80px，这里放宽到 2 倍字高（最小 60）
    GAP_TOL = max(60, int(text_h * 2.0))

    def row_has_content(y: int) -> bool:
        if y < 0:
            return False
        step = max(1, text_w // 30)
        for x in range(x0, x1 + 1, step):
            try:
                if _is_content(im.getpixel((x, y))[:3], bg):
                    return True
            except Exception:
                continue
        return False

    # 往上找，跳过图标和文字之间的空隙
    y = y0 - 2
    gap = 0
    while y > 0 and gap <= GAP_TOL:
        if row_has_content(y):
            break
        y -= 1
        gap += 1
    if gap > GAP_TOL:
        return None  # 上方没有图标

    icon_bottom = y
    gap = 0
    top = y
    while top > max(0, icon_bottom - limit) and gap < 10:
        top -= 1
        if row_has_content(top):
            gap = 0
        else:
            gap += 1
    icon_top = top + gap

    if icon_bottom - icon_top < 15:
        return None

    # 横向：在图标高度范围内找左右边界。
    # 扩展幅度限制在「半个字宽」以内，否则密集宫格会把隔壁图标也框进来。
    mid_y = (icon_top + icon_bottom) // 2
    max_ext = max(10, int(text_w * 0.5))

    left = x0
    left_gap = 0
    while left > max(0, x0 - max_ext) and left_gap < 12:
        left -= 1
        if _is_content(im.getpixel((left, mid_y))[:3], bg):
            left_gap = 0
        else:
            left_gap += 1
    left += left_gap

    right = x1
    right_gap = 0
    while right < min(w - 1, x1 + max_ext) and right_gap < 12:
        right += 1
        if _is_content(im.getpixel((right, mid_y))[:3], bg):
            right_gap = 0
        else:
            right_gap += 1
    right -= right_gap

    return [min(left, x0), icon_top, max(right, x1), icon_bottom]


# --------------------------------------------------------------------------
# 三种框模式
# --------------------------------------------------------------------------

def box_text_only(im: Image.Image, text_box: Box, pad: int = 10) -> Box:
    """纯文字（按钮、列表项），四周留一点余量。

    按钮类元素文字四周通常有内边距，pad 给大一点更贴近「手指会点的区域」。
    """
    x0, y0, x1, y1 = text_box
    w, h = im.size
    py = max(pad, int((y1 - y0) * 0.5))
    return [max(0, x0 - pad * 2), max(0, y0 - py), min(w, x1 + pad * 2), min(h, y1 + py)]


def box_grid(im: Image.Image, text_box: Box, pad: int = 14) -> Box:
    """宫格图标：图标 + 文字整体。

    彩色/渐变背景（例如顶部横幅）上「找图标」会失控，
    这类情况直接退回纯文字框，宁可小也不要圈一大片。
    """
    x0, y0, x1, y1 = text_box
    w, h = im.size
    if is_gradient_background(im, text_box):
        return box_text_only(im, text_box, pad)

    icon = find_icon_above(im, text_box)
    if icon is None:
        return box_text_only(im, text_box, pad * 2)
    box = [
        max(0, min(icon[0], x0) - pad),
        max(0, icon[1] - pad),
        min(w, max(icon[2], x1) + pad),
        min(h, y1 + pad),
    ]
    # 兜底：只拦「明显失控」的情况。
    # 正常宫格图标 = 图标(约 2-3 倍字高) + 间隙 + 文字，
    # 所以总高到 6 倍字高都算正常；超过 8 倍才认为是扫歪了。
    # 宽度限制在 1.6 倍文字宽以内，避免密集宫格串到隔壁。
    box_h = box[3] - box[1]
    box_w = box[2] - box[0]
    if box_h > (y1 - y0) * 8 or box_w > max((x1 - x0) * 1.6, 220):
        return box_text_only(im, text_box, pad)
    return box


def box_row(im: Image.Image, text_box: Box, pad: int = 6) -> Box:
    """列表整行：从左侧头像区一直撑到右侧时间，高度覆盖整行。

    微信聊天列表的行高约等于文字高度的 3 倍（含头像和副标题），
    所以上下各留 1 倍文字高度。
    """
    x0, y0, x1, y1 = text_box
    w, h = im.size
    th = y1 - y0
    return [8, max(0, int(y0 - th * 1.1)), w - 8, min(h, int(y1 + th * 1.1))]


MODES = {"text": box_text_only, "grid": box_grid, "row": box_row}


# --------------------------------------------------------------------------
# 标注映射表
#   (图片名, 要找的文字, task_id, goal, 模式)
#   文字用「包含」匹配，取 OCR 得分最高的那个
# --------------------------------------------------------------------------

MAPPING: List[Tuple[str, str, str, str, str]] = [
    # ===== 医院首页 001 =====
    ("001", "预约挂号",   "hospital.book_appointment", "点预约挂号",     "grid"),
    ("001", "添加就诊人", "hospital.add_patient",      "点添加就诊人",   "grid"),
    ("001", "门诊缴费",   "hospital.pay_bill",         "点门诊缴费",     "grid"),
    ("001", "就诊人管理", "hospital.manage_patient",   "点就诊人管理",   "grid"),
    ("001", "自助报到",   "hospital.check_in",         "点自助报到",     "grid"),
    ("001", "检验检查",   "hospital.report",           "点检验检查",     "grid"),
    ("001", "病历中心",   "hospital.medical_record",   "点病历中心",     "grid"),
    ("001", "就诊指南",   "hospital.guide",            "点就诊指南",     "grid"),
    ("001", "去使用",     "hospital.elder_service",    "点长辈就医服务", "text"),
    ("001", "个人中心",   "hospital.profile",          "点个人中心",     "text"),

    # ===== 门诊服务 002 =====
    ("002", "智慧导诊",   "hospital.ai_triage",        "点智慧导诊",     "grid"),
    ("002", "门诊发票",   "hospital.invoice",          "点门诊发票",     "grid"),
    ("002", "医院介绍",   "hospital.intro",            "点医院介绍",     "grid"),
    ("002", "满意度调查", "hospital.survey",           "点满意度调查",   "grid"),
    ("002", "医保电子凭", "gov.insurance_code",        "点医保电子凭证", "grid"),
    ("002", "签约无感支", "hospital.quick_pay",        "点签约无感支付", "grid"),
    ("002", "医保AI助手", "gov.insurance_ai",          "点医保AI助手",   "grid"),
    ("002", "医保定点",   "gov.insurance_anchor",      "点医保定点",     "grid"),
    ("002", "门诊专科",   "hospital.dept_intro",       "点门诊专科介绍", "grid"),

    # ===== 体检 / 住院 003 =====
    ("003", "体检预约",   "hospital.physical_book",    "点体检预约",     "grid"),
    ("003", "体检缴费",   "hospital.physical_pay",     "点体检缴费",     "grid"),
    ("003", "体检报告查", "hospital.physical_report",  "点体检报告查询", "grid"),
    ("003", "住院押金",   "hospital.deposit",          "点住院押金",     "grid"),
    ("003", "住院结算",   "hospital.discharge_pay",    "点住院结算",     "grid"),
    ("003", "日清单",     "hospital.daily_bill",       "点住院日清单",   "grid"),
    ("003", "手术进度",   "hospital.surgery",          "点手术进度",     "grid"),
    ("003", "病案邮寄",   "hospital.record_mail",      "点病案邮寄",     "grid"),

    # ===== 互联网医院 004 =====
    ("004", "线上问诊",   "hospital.online_consult",   "点线上问诊",     "grid"),
    ("004", "医生团队",   "hospital.doctor_team",      "点医生团队",     "grid"),
    ("004", "我的医生",   "hospital.my_doctor",        "点我的医生",     "grid"),
    ("004", "用药查询",   "hospital.medicine",         "点用药查询",     "grid"),
    ("004", "护理上门",   "hospital.home_nursing",     "点护理上门",     "grid"),
    ("004", "科普文章",   "hospital.article",          "点科普文章",     "grid"),

    # ===== 科室列表 005-008 =====
    # 注意：科室选择是「同一屏多个可接受答案」，不是链的第 2/3/4 步。
    # 所以它们是各自独立的单步用例（task_id 相同、goal 不同），
    # 但评测时按「同一张图的多个候选」处理，不参与整链成功率。
    ("005", "神经医学科", "hospital.pick_dept", "选神经医学科", "text"),
    ("005", "心血管内科", "hospital.pick_dept", "选心血管内科", "text"),
    ("005", "骨科",       "hospital.pick_dept", "选骨科",       "text"),
    ("005", "消化内科",   "hospital.pick_dept", "选消化内科",   "text"),
    ("005", "内分泌代谢科", "hospital.pick_dept", "选内分泌代谢科", "text"),

    # ===== 排班 / 医生 =====
    ("010", "中医康复科", "hospital.pick_slot",        "确认科室",         "text"),
    ("011", "袁嘉蔚",     "hospital.pick_doctor",      "选医生袁嘉蔚",     "text"),
    ("012", "刘进",       "hospital.pick_doctor",      "选医生刘进",       "text"),
    ("013", "挂号详情",   "hospital.confirm_book",     "确认挂号详情",     "text"),

    # ===== 个人中心 014 =====
    ("014", "我的家人",   "hospital.add_patient",      "点我的家人",       "text"),
    ("014", "就诊记录",   "hospital.visit_record",     "点就诊记录",       "text"),
    ("014", "电子健康卡", "gov.health_card",           "点电子健康卡",     "text"),
    ("014", "实名认证",   "gov.real_name",             "点实名认证",       "text"),
    ("014", "长辈就医",   "hospital.elder_service",    "点长辈就医",       "text"),

    # ===== 添加就诊人 016 =====
    ("016", "添加就诊人", "hospital.add_patient",      "点添加就诊人",     "text"),

    # ===== 门诊缴费 017 =====
    # 「待支付 / 已支付」是同一屏的两个 tab，属于不同目标，不该串成一条链
    ("017", "待支付",     "hospital.pay_pending",      "选待支付",         "text"),
    ("017", "已支付",     "hospital.pay_done",         "选已支付",         "text"),

    # ===== 粤省事 018 =====
    ("018", "粤省事码",   "gov.health_code",           "点粤省事码",       "grid"),
    ("018", "数字空间",   "gov.digital_space",         "点数字空间",       "grid"),
    ("018", "即接即办",   "gov.quick_service",         "点即接即办",       "grid"),
    ("018", "关怀版",     "gov.elder_mode",            "点关怀版",         "grid"),
    ("018", "社保卡信息查询", "gov.social_card_query", "点社保卡信息查询", "grid"),
    ("018", "医保",       "gov.insurance",             "点医保",           "grid"),
    ("018", "公积金",     "gov.housing_fund",          "点公积金",         "grid"),

    # ===== 医保大厅 019 / 030 =====
    ("019", "医保电子凭证", "gov.insurance_code",      "点医保电子凭证",   "grid"),
    ("019", "医保账户余额", "gov.insurance_balance",    "查医保账户余额",   "text"),
    ("030", "医保电子凭证", "gov.insurance_code",      "点医保电子凭证",   "grid"),
    ("030", "医保支付",   "gov.insurance_pay",         "点医保支付",       "grid"),
    ("030", "异地就医信息", "gov.cross_region",        "点异地就医信息",   "grid"),

    # ===== 人社 026 =====
    ("026", "个人参保信息", "gov.social_security",     "点个人参保信息",   "text"),
    ("026", "社保凭证",   "gov.social_cert",           "点社保凭证",       "text"),
    ("026", "失业登记",   "gov.unemployment",          "点失业登记",       "text"),

    # ===== 广州服务 023 =====
    ("023", "医保",       "gov.insurance",             "点医保",           "grid"),
    ("023", "公积金",     "gov.housing_fund",          "点公积金",         "grid"),

    # ===== 法人服务 025 =====
    ("025", "行驶驾驶",   "gov.driving",               "点行驶驾驶",       "grid"),
    ("025", "出入境",     "gov.exit_entry",            "点出入境",         "grid"),
    ("025", "市场监管",   "gov.market_supervise",      "点市场监管",       "grid"),

    # ===== 微信 =====
    ("041", "A.儿子",     "wechat.video_call_son",     "点儿子的微信",     "row"),
    ("041", "肿菌莲",     "wechat.open_chat",          "点第一个聊天",     "row"),
    ("042", "A.儿子",     "wechat.video_call_son",     "确认当前聊天",     "text"),
    ("043", "视频通话",   "wechat.video_call_son",     "点视频通话",       "grid"),
    ("043", "相册",       "wechat.send_photo",         "点相册",           "grid"),
    ("043", "拍摄",       "wechat.take_photo",         "点拍摄",           "grid"),
    ("043", "转账",       "wechat.transfer",           "点转账",           "grid"),
    ("043", "红包",       "wechat.red_packet",         "点红包",           "grid"),
    ("043", "位置",       "wechat.share_location",     "点位置",           "grid"),
    ("043", "语音输入",   "wechat.voice_input",        "点语音输入",       "grid"),
    ("044", "视频通话",   "wechat.video_call_son",     "选视频通话",       "text"),
    ("044", "语音通话",   "wechat.voice_call_son",     "选语音通话",       "text"),
    ("045", "共享实时位置", "wechat.live_location",     "选共享实时位置",   "text"),
    ("045", "发送位置",   "wechat.share_location",     "选发送位置",       "text"),
    ("050", "微信(78)",   "wechat.open_wechat",        "确认微信界面",     "text"),

    # ===== 地图 / 出行 040 =====
    ("040", "打车",       "map.taxi",                  "点打车",           "grid"),
    ("040", "公交地铁",   "map.transit",               "点公交地铁",       "grid"),
    ("040", "顺风车",     "map.carpool",               "点顺风车",         "grid"),
    ("040", "地铁图",     "map.subway_map",            "点地铁图",         "grid"),
    ("040", "实时公交",   "map.bus_live",              "点实时公交",       "grid"),

    # ===== 地铁 049 =====
    ("049", "广州地铁乘车码", "map.subway_qr",         "点广州地铁乘车码", "text"),
    ("049", "刷码乘车",   "map.subway_qr",             "点刷码乘车",       "text"),

    # ==========================================================
    # 第二批：006-009 科室列表 / 015 建档绑卡
    # ==========================================================
    ("006", "肝病内科",   "hospital.pick_dept",        "选肝病内科",       "text"),
    ("006", "泌尿外科",   "hospital.pick_dept",        "选泌尿外科",       "text"),
    ("006", "胸外科",     "hospital.pick_dept",        "选胸外科",         "text"),
    ("006", "老年病科",   "hospital.pick_dept",        "选老年病科",       "text"),
    ("006", "儿科门诊",   "hospital.pick_dept",        "选儿科门诊",       "text"),

    ("007", "口腔科",     "hospital.pick_dept",        "选口腔科",         "text"),
    ("007", "眼科",       "hospital.pick_dept",        "选眼科",           "text"),
    ("007", "耳鼻咽喉科", "hospital.pick_dept",        "选耳鼻咽喉科",     "text"),
    ("007", "精神心理科", "hospital.pick_dept",        "选精神心理科",     "text"),
    ("007", "营养科",     "hospital.pick_dept",        "选营养科",         "text"),

    ("008", "发热门诊",   "hospital.pick_dept",        "选发热门诊",       "text"),
    ("008", "肠道门诊",   "hospital.pick_dept",        "选肠道门诊",       "text"),
    ("008", "药学服务门诊", "hospital.pick_dept",      "选药学服务门诊",   "text"),

    ("009", "展开",       "hospital.dept_detail",      "展开科室介绍",     "text"),

    ("015", "无诊疗卡",   "hospital.no_card",          "点无诊疗卡",       "text"),
    ("015", "绑定",       "hospital.bind_card",        "点绑定",           "text"),
    ("015", "居民身份证", "hospital.pick_id_type",     "选居民身份证",     "text"),

    # ==========================================================
    # 第二批：020-029 政务
    # ==========================================================
    ("020", "我是城乡居民", "gov.insurance_resident",   "选我是城乡居民",   "text"),
    ("020", "我是灵活就业人员", "gov.insurance_flex",   "选我是灵活就业人员", "text"),
    ("020", "城乡居民医保费清缴", "gov.insurance_pay_fee", "点城乡居民医保费清缴", "text"),
    ("020", "本地就医",   "gov.local_medical",         "点本地就医",       "text"),
    ("020", "异地就医",   "gov.cross_region",          "点异地就医",       "text"),
    ("020", "待遇报销",   "gov.reimbursement",         "点待遇报销",       "text"),
    ("020", "定点医院查询", "gov.hospital_query",       "点定点医院查询",   "text"),

    ("021", "人脸识别认证", "gov.login_face",          "选人脸识别认证",   "text"),
    ("021", "微警认证",   "gov.login_wechat_police",   "选微警认证",       "text"),
    ("021", "下一步",     "gov.login_next",            "点下一步",         "text"),

    ("022", "开始人脸识别验证", "gov.face_verify",     "点开始人脸识别验证", "text"),

    ("024", "助残",       "gov.disability",            "点助残",           "grid"),
    ("024", "法律服务",   "gov.legal_service",         "点法律服务",       "grid"),
    ("024", "社会救助",   "gov.social_relief",         "点社会救助",       "grid"),
    ("024", "生活缴费",   "gov.utility_pay",           "点生活缴费",       "grid"),
    ("024", "就医",       "gov.medical_service",       "点就医",           "grid"),

    ("027", "城乡居民基本养老保险个人信息查询", "gov.pension_query", "点城乡居民养老保险查询", "text"),
    ("027", "工伤保险个人基本信息变更", "gov.work_injury", "点工伤保险信息变更", "text"),
    ("027", "失业保险个人基本信息变更", "gov.unemployment_change", "点失业保险信息变更", "text"),

    ("028", "开始办理",   "gov.start_service",         "点开始办理",       "text"),

    ("029", "养老待遇收入证明", "gov.pension_proof",    "点养老待遇收入证明", "text"),
    ("029", "社会保险参保证明查询", "gov.social_cert_query", "点社会保险参保证明查询", "text"),
    ("029", "个人权益单", "gov.rights_record",         "点个人权益单",     "text"),

    # ==========================================================
    # 第二批：031-039 患者服务平台 / 社保小程序
    # ==========================================================
    ("031", "专家号预约", "hospital.book_appointment", "点专家号预约",     "text"),
    ("031", "处方在线支付", "hospital.rx_pay",         "点处方在线支付",   "text"),
    ("031", "就诊卡管理", "hospital.card_manage",      "点就诊卡管理",     "text"),
    ("031", "押金预交",   "hospital.deposit",          "点押金预交",       "text"),
    ("031", "住院日清单", "hospital.daily_bill",       "点住院日清单",     "text"),

    ("032", "急诊科",     "hospital.pick_dept",        "选急诊科",         "text"),
    ("032", "挂号须知",   "hospital.book_notice",      "点挂号须知",       "text"),
    ("032", "内科",       "hospital.pick_dept",        "选内科",           "text"),
    ("032", "外科",       "hospital.pick_dept",        "选外科",           "text"),
    ("032", "儿科",       "hospital.pick_dept",        "选儿科",           "text"),

    ("033", "挂号指引",   "hospital.book_guide",       "点挂号指引",       "text"),
    ("033", "展开",       "hospital.expand_doctor",    "展开医生列表",     "text"),
    ("033", "上午",       "hospital.pick_morning",     "选上午",           "text"),

    ("036", "扫一扫",     "gov.social_scan",           "点扫一扫",         "text"),
    ("036", "亲情服务",   "gov.family_service",        "点亲情服务",       "text"),
    ("036", "长辈版",     "gov.elder_mode",            "点长辈版",         "text"),
    ("036", "个人社保权益单查询", "gov.rights_query",  "点个人社保权益单查询", "text"),
    ("036", "养老保险",   "gov.pension",               "点养老保险",       "text"),
    ("036", "社保卡服务", "gov.social_card_service",   "点社保卡服务",     "text"),

    ("037", "我的社保卡", "gov.my_social_card",        "点我的社保卡",     "text"),
    ("037", "粤税通",     "gov.tax",                   "点粤税通",         "text"),
    ("037", "穗好办",     "gov.suihaoban",             "点穗好办",         "text"),

    ("038", "广州市白云区人民医院", "hospital.enter_app", "进广州市白云区人民医院", "text"),
    ("038", "陆丰市人民医院", "hospital.enter_other",   "进陆丰市人民医院", "text"),

    ("039", "粤省事",     "gov.enter_yueshengshi",     "进粤省事",         "text"),
    ("039", "乘车码",     "map.subway_qr",             "进乘车码",         "text"),

    # ==========================================================
    # 第二批：046 相机 / 048 粤省事首页
    # ==========================================================
    ("048", "关怀版",     "gov.elder_mode",            "点关怀版",         "grid"),
    ("048", "粤省事码",   "gov.health_code",           "点粤省事码",       "grid"),
    ("048", "数字空间",   "gov.digital_space",         "点数字空间",       "grid"),
    ("048", "马上看看",   "gov.promo",                 "点马上看看",       "text"),

    # ===== 034 排班日期（用具体日期，避免「有号」重名）=====
    ("034", "挂号指引",   "hospital.book_guide",       "点挂号指引",       "text"),
    ("034", "周六",       "hospital.pick_date",        "选周六",           "text"),

    # ===== 035 就诊人管理（低分辨率，仅作补充）=====
]


# --------------------------------------------------------------------------
# 执行
# --------------------------------------------------------------------------

def load_ocr(stem: str) -> List[dict]:
    f = OCR_DIR / f"{stem}.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8"))


def pick(cands: List[dict], pattern: str) -> Optional[dict]:
    """在 OCR 候选里找 pattern。

    优先「完全相等」，其次「以 pattern 开头」，最后才「包含」。
    否则「待支付」会误匹配到「暂无待支付项目」，
    「医保」会误匹配到「医保电子凭证」。
    """
    exact = [c for c in cands if c["text"].strip() == pattern]
    if exact:
        return max(exact, key=lambda c: c["score"])

    starts = [c for c in cands if c["text"].strip().startswith(pattern)]
    if starts:
        # 长度越接近越好，避免「医保」抢到「医保电子凭证」
        starts.sort(key=lambda c: (len(c["text"]) - len(pattern), -c["score"]))
        return starts[0]

    contains = [c for c in cands if pattern in c["text"]]
    if contains:
        contains.sort(key=lambda c: (len(c["text"]) - len(pattern), -c["score"]))
        return contains[0]
    return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="自动标注")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    parser.add_argument("--preview", action="store_true", help="生成预览图")
    parser.add_argument("--append", action="store_true", help="追加到现有 gt.jsonl")
    args = parser.parse_args(argv)

    if args.preview:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    results: List[dict] = []
    missing: List[str] = []
    im_cache: Dict[str, Image.Image] = {}

    for img_stem, pattern, task_id, goal, mode in MAPPING:
        cands = load_ocr(img_stem)
        if not cands:
            missing.append(f"{img_stem}: OCR 结果缺失")
            continue
        hit = pick(cands, pattern)
        if hit is None:
            missing.append(f"{img_stem}: 找不到文字「{pattern}」")
            continue

        if img_stem not in im_cache:
            im_cache[img_stem] = Image.open(IMAGES / f"{img_stem}.jpg").convert("RGB")
        im = im_cache[img_stem]

        box = MODES[mode](im, hit["bbox"])
        results.append({
            "file": f"images/{img_stem}.jpg",
            "task_id": task_id,
            "step": 1,
            "goal": goal,
            "bbox": box,
            "_text": hit["text"],
            "_mode": mode,
        })

    # 按链分组重新排 step：同一条链里，按「图片在 MAPPING 中出现的顺序」编号
    chain_step: Dict[str, int] = {}
    for r in results:
        tid = r["task_id"]
        chain_step[tid] = chain_step.get(tid, 0) + 1
        r["step"] = chain_step[tid]

    print(f"匹配成功 {len(results)} 条，失败 {len(missing)} 条\n")
    if missing:
        print("未匹配：")
        for m in missing:
            print(f"  - {m}")
        print()

    # 打印结果
    by_chain: Dict[str, List[dict]] = {}
    for r in results:
        by_chain.setdefault(r["task_id"], []).append(r)
    for tid in sorted(by_chain):
        print(f"[{tid}]")
        for r in by_chain[tid]:
            print(f"  step{r['step']}  {r['file']:<22} {r['goal']:<14} "
                  f"bbox={r['bbox']}  ({r['_text']}/{r['_mode']})")
        print()

    # 预览图（先出，方便检查；dry-run 也出）
    if args.preview:
        from PIL import ImageDraw, ImageFont
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 26)
        except Exception:
            font = ImageFont.load_default()
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        for stem, im in im_cache.items():
            canvas = im.copy()
            d = ImageDraw.Draw(canvas)
            for r in results:
                if r["file"] != f"images/{stem}.jpg":
                    continue
                x0, y0, x1, y1 = r["bbox"]
                d.rectangle([x0, y0, x1, y1], outline=(0, 220, 255), width=5)
                d.text((x0 + 4, max(0, y0 - 30)), r["goal"], fill=(0, 160, 200), font=font)
            canvas.save(PREVIEW_DIR / f"{stem}.png")
        print(f"预览图已存到 {PREVIEW_DIR}（{len(im_cache)} 张）")

    if args.dry_run:
        print("--dry-run，未写入文件")
        return 0

    # 写文件
    rows = []
    for r in results:
        rows.append({
            "file": r["file"],
            "task_id": r["task_id"],
            "step": r["step"],
            "goal": r["goal"],
            "bbox": r["bbox"],
        })

    if args.append and GT.exists():
        old = [json.loads(l) for l in GT.read_text(encoding="utf-8-sig").splitlines() if l.strip()]
        seen = {(o["file"], o.get("task_id", ""), o.get("step", 1), o["goal"]) for o in old}
        for r in rows:
            key = (r["file"], r["task_id"], r["step"], r["goal"])
            if key not in seen:
                old.append(r)
        rows = old

    GT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                  encoding="utf-8")
    print(f"已写入 {len(rows)} 条到 {GT}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
