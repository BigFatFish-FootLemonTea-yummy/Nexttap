# 评测工具

评测集的标注、运行与打分。核心脚本 + 数据维护脚本。

---

## 快速开始

```bash
# 1. 数据自检（改完任何东西都先跑这个）
python tools/selfcheck.py

# 2. 跑评测（后端要先起来）
python tools/run_eval.py --gt data/eval/gt_first50.jsonl --out data/eval/pred50.jsonl

# 3. 出数字
python tools/eval.py --gt data/eval/gt_first50.jsonl --pred data/eval/pred50.jsonl --ci
```

## 数据维护（改 gt 前后都要跑）

```bash
# 补缺失的 task_id / step + 一致性审计（先 dry-run 看报告）
python tools/repair_eval_data.py --dry-run
python tools/repair_eval_data.py --write

# 查 goal 是否泄漏目标框文字（跑 47 张图约 2 分钟，需要 OCR 依赖）
python tools/audit_goal_leak.py
```

**这两个检查为什么必须做**（2026-11-01 踩过的坑）：

- `gt.jsonl` 里有 5 行漏填 `task_id`/`step`，而 `apply_goal_map.py` 只改
  「有 task_id 且 step=1」的行，于是整行被跳过 → step 缺省成 1，混进最关键的
  step=1 指标，同时把两条链的步数算错、让 `gt_first50` 匹配不上。
- `step=1` 的 119 条里曾有 82 条 goal 直接写着目标按钮的文字（goal `点公积金`
  而框里就是"公积金"），模型做字符串匹配就能命中。审计显示：泄漏组命中
  95.1%，未泄漏组 81.1%，差 14 个百分点。

`selfcheck.py` 现在会自动检查「缺 task_id/step」和「同 bbox 多身份」两项。

输出示例：

```
真值用例总数      : 50   （分布在 29 张图上）
已评测            : 50   （缺预测 0 个）
命中              : 44
hit@1             : 88.0%   (44/50)
95% Wilson 区间   : [76.2%, 94.4%]   （假设 50 个用例相互独立）
95% 聚类区间      : [75.1%, 95.2%]   （按 N 张图重采样，推荐引用这个）
时延 p50/p90      : 2400ms / 3100ms   (F2 要求 ≤5000ms)

-- 整链成功率（一条链所有步全对才算成功）--
  PASS  wechat.video_call_son    ✓1 ✓2 ✓3
  FAIL  hospital.book_appointment ✓1 ✓2 ✗3 ✓4
  整链成功率：1/2 = 50.0%
```

---

## 脚本清单

| 脚本 | 作用 |
|---|---|
| **`eval.py`** | 算 hit@1、Wilson 区间 + 聚类区间、时延、整链成功率 |
| **`run_eval.py`** | 批量调后端，产出 `pred.jsonl` |
| **`annotate.py`** | 手工标注 GUI（拖框、缩放、任务链下拉） |
| **`repair_eval_data.py`** | 补缺失的 task_id/step、重建 gt_first50、一致性审计 |
| **`audit_goal_leak.py`** | OCR 逐框检查 goal 是否泄漏答案 |
| `redact.py` | 隐私打码：扫描 PII 并用纯色块遮盖 |
| `selfcheck.py` | 数据完整性自检 |

> 数据准备阶段的脚本（OCR 提取、自动标注、链修正等）已完成使命，不入库，本地保留。

---

## 数据格式

### `data/eval/gt.jsonl` — 真值

每行一个用例：

```json
{"file": "images/001.jpg", "task_id": "hospital.book_appointment",
 "step": 1, "goal": "点预约挂号", "bbox": [847, 1566, 1098, 1874],
 "quality": "ok"}
```

| 字段 | 说明 |
|---|---|
| `file` | 图片相对路径 |
| `bbox` | `[x0, y0, x1, y1]`，**原图像素坐标** |
| `goal` | 这一步该点哪，动作指令（不是任务名） |
| `task_id` | 所属任务链，可省略（= 孤立单步） |
| `step` | 链内第几步 |
| `quality` | `ok` / `low` / `bad`，bad 评测时自动剔除 |

**同一 (file, task_id, step, goal) 出现多行 = 多个可接受入口**，命中任意一个都算对。

### `data/eval/pred.jsonl` — 预测

`run_eval.py` 生成，**必须带 `task_id` 和 `step`**，否则 `eval.py` 匹配不上。

```json
{"file": "images/001.jpg", "goal": "点预约挂号", "task_id": "hospital.book_appointment",
 "step": 1, "bbox": [850, 1570, 1095, 1870], "elapsed_ms": 2400}
```

### `data/rag/paths.jsonl` — 任务链

```json
{"task_id": "wechat.video_call_son", "name": "给儿子打视频电话", "app": "微信",
 "aliases": ["怎么给儿子打视频电话", "我想看看孙子"],
 "steps": [{"step": 1, "screen": "聊天列表", "target": "点儿子的微信", "hint": "点「A.儿子」"}]}
```

`aliases` 是老人会说的话，进 RAG 索引；`steps[].target` 是动作指令。

---

## 评测口径

### hit@1

**预测框中心点落在真值框内 = 命中。** 对应《项目定稿》D9。

### 整链成功率

一条链的所有步都命中，这条链才算成功。

**以下链不计入整链统计**（`eval.py` 里的 `CHAIN_EXCLUDE`）：

| 类型 | 例子 | 原因 |
|---|---|---|
| 候选集合 | `hospital.pick_dept`（21 个科室） | 同一屏多个答案，不是 21 步操作 |
| 跨 App 对照 | `gov.insurance`、`gov.health_code` 等 | 同一功能在不同 App 的入口，不是路径 |

加 `--all-chains` 可查看全部。

### 低质量用例

`quality = "bad"` 的用例（分辨率过低、界面被遮挡）**默认剔除**，`--include-bad` 可查看。

---

## 当前数据规模

```
用例       164 条   (ok=162  low=2  bad=0)
图片        47 张   （不入库）
任务链     120 条
首批 50     50 条   （高频场景优先，29 张图）

已验证的真实链：
  wechat.video_call_son        3 步  聊天列表 → 功能面板 → 视频通话弹窗
  hospital.book_appointment    4 步  医院首页 → 科室 → 医生 → 挂号详情
```

---

## 图片说明

**评测图片不纳入仓库**（含真实界面截图，涉及隐私）。

- 图片已做 PII 打码（姓名、手机号共 4 处）
- 需要复现评测时，请联系作者获取 `data/eval/images/` 打包文件
- 格式：`001.jpg` ~ `050.jpg`，与原图同名

---

## 已知的坑（已在代码里处理）

| 坑 | 处理 |
|---|---|
| 长截图只显示左上角 | `annotate.py` 用 PIL resize 到画布尺寸 |
| 从右下往左上拖成空框 | `on_release` + `screen_to_image` 双重 min/max |
| gt.jsonl 有坏行被静默丢掉 | `load_gt` 遇坏行直接退出并备份 |
| 写到一半断电文件截断 | `save_gt` 先写 tmp 再 `os.replace` |
| pred 缺 task_id 导致全 0% | `eval.py` 检测「一条都没匹配上」并报警 |
| 上传 MIME 写死 image/jpeg | 按真实后缀给 MIME |
| 科室 21 个候选被当成 21 步链 | `CHAIN_EXCLUDE` 排除出整链统计 |
