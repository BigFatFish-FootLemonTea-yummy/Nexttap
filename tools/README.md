# 评测工具

评测集的标注、运行与打分。核心脚本 + 数据维护脚本。

---

## 快速开始

```bash
# 1. 数据自检（改完任何东西都先跑这个）
python tools/selfcheck.py

# 2. 跑评测（后端要先起来）
python tools/run_eval.py

# 3. 出数字
python tools/eval.py --ci
```

> 2026-11 换集：旧的 `gt.jsonl`(164 条) + `images/`(47 张扁平图) +
> `gt_first50.jsonl` + `pred.jsonl` + `goal_map.tsv` 已全部删除，
> 现在是新集 `gt.jsonl`(118 条) + `images/`(113 张分层图)。
> 原来 `--gt data/eval/gt_first50.jsonl` 那套「先跑 50 条」的用法已不可用，
> 需要抽样就用 `run_eval.py --limit N`。

## 数据维护

```bash
# 一致性审计（先 dry-run 看报告）
python tools/repair_eval_data.py --dry-run

# 查 goal 是否泄漏目标框文字（需要 OCR 依赖）
python tools/audit_goal_leak.py
```

**这两个检查为什么必须做**（2026-11-01 踩过的坑，教训仍然有效）：

- `gt.jsonl` 里曾有 5 行漏填 `task_id`/`step`，旧版 `apply_goal_map.py` 只改
  「有 task_id 且 step=1」的行，于是整行被跳过 → step 缺省成 1，混进最关键的
  step=1 指标，同时把两条链的步数算错、让 `gt_first50` 匹配不上。
  （`apply_goal_map.py` 已删除，但 `selfcheck.py` 仍会检查「缺 task_id/step」。）
- `step=1` 的 119 条里曾有 82 条 goal 直接写着目标按钮的文字（goal `点公积金`
  而框里就是"公积金"），模型做字符串匹配就能命中。审计显示：泄漏组命中
  95.1%，未泄漏组 81.1%，差 14 个百分点。新集的 goal 是按规范写的
  （step=1 用户口语 / step>=2 动作指令），审计应保持 0 泄漏。

`selfcheck.py` 会自动检查「缺 task_id/step」和「同 bbox 多身份」两项。

输出示例：

```
真值用例总数      : 118   （分布在 112 张图上）
已评测            : 118   （缺预测 0 个）
命中              : 103
hit@1             : 87.3%   (103/118)
95% Wilson 区间   : [80.1%, 92.2%]   （假设 118 个用例相互独立）
95% 聚类区间      : [79.4%, 93.1%]   （按 112 张图重采样，推荐引用这个）
时延 p50/p90      : 2400ms / 3100ms   (F2 要求 ≤5000ms)

-- 整链成功率（一条链所有步全对才算成功）--
  PASS  wechat.call    ✓1 ✓2 ✓3
  FAIL  baiyun.book    ✓1 ✓2 ✓3 ✗4 ✓5 ✓6 ✓7 ✓8
  整链成功率：1/2 = 50.0%
```

---

## 脚本清单

| 脚本 | 作用 |
|---|---|
| **`eval.py`** | 算 hit@1、Wilson 区间 + 聚类区间、时延、整链成功率 |
| **`run_eval.py`** | 批量调后端，产出 `pred.jsonl` |
| **`annotate.py`** | 手工标注 GUI（拖框、缩放、任务链下拉），递归遍历分层 `images/` |
| **`repair_eval_data.py`** | 一致性审计（first50 逻辑已随新集删除） |
| **`audit_goal_leak.py`** | OCR 逐框检查 goal 是否泄漏答案 |
| `redact.py` | 隐私打码：扫描 PII 并用纯色块遮盖 |
| `selfcheck.py` | 数据完整性自检 |

> 数据准备阶段的脚本（OCR 提取、自动标注、链修正等）已完成使命，不入库，本地保留。
> `build_retrieval_eval.py` / `eval_retrieval.py` 服务于 `data/rag/paths.jsonl` 的
> 检索评测，与定位评测集解耦，换集不影响它们。

---

## 数据格式

### `data/eval/gt.jsonl` — 真值

每行一个用例：

```json
{"file": "images/Chat on WeChat/Call [Name]/Screenshot_20261102_091532.jpg",
 "task_id": "wechat.call", "step": 1, "goal": "想给这个人打个电话",
 "bbox": [455, 1880, 790, 2010], "quality": "ok"}
```

| 字段 | 说明 |
|---|---|
| `file` | 相对 `data/eval/` 的图片路径，新集是 `images/<App>/<医院>/<流程>/xxx.jpg`，可含空格和方括号 |
| `bbox` | `[x0, y0, x1, y1]`，**原图像素坐标** |
| `goal` | 这一步该点哪。`step=1` 是用户口语，`step>=2` 是动作指令 |
| `task_id` | 所属任务链 |
| `step` | 链内第几步，从 1 开始连续 |
| `quality` | `ok` / `low` / `bad`，bad 评测时自动剔除 |

**同一 (file, task_id, step, goal) 出现多行 = 多个可接受入口**，命中任意一个都算对。
新集里有 6 组这样的多框用例（例如 `baiyun.book` 的 step=1 在首页有 3 个挂号入口）。

### `data/eval/pred.jsonl` — 预测

`run_eval.py` 生成，**必须带 `task_id` 和 `step`**，否则 `eval.py` 匹配不上。

```json
{"file": "images/Chat on WeChat/Call [Name]/Screenshot_20261102_091532.jpg",
 "goal": "想给这个人打个电话", "task_id": "wechat.call",
 "step": 1, "bbox": [455, 1885, 788, 2008], "elapsed_ms": 2400}
```

### `data/rag/paths.jsonl` — 任务链（RAG 检索用）

```json
{"task_id": "wechat.video_call_son", "name": "给儿子打视频电话", "app": "微信",
 "aliases": ["怎么给儿子打视频电话", "我想看看孙子"],
 "steps": [{"step": 1, "screen": "聊天列表", "target": "点儿子的微信", "hint": "点「A.儿子」"}]}
```

`paths.jsonl` 已与新集对齐：30 条链，task_id 与 `gt.jsonl` 完全一致
（`baiyun.*` / `lufeng.*` / `sysu.*` / `wechat.*`）。

> 换集初期两边命名体系一度脱节（旧 `paths.jsonl` 是 `hospital.*` / `gov.*`），
> 现在已同步。`selfcheck.py` 仍保留降级逻辑：万一以后再次脱节，
> 「部分找不到」报 FAIL、「全部找不到」只报 WARN 并说明评测不依赖它。

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

这些 task_id 都来自**旧集**，保留只为用 `--gt` 指旧文件复算历史数字。
新集 30 条链全部是真实多步路径，无需排除。加 `--all-chains` 可查看全部。

### 低质量用例

`quality = "bad"` 的用例（分辨率过低、界面被遮挡）**默认剔除**，`--include-bad` 可查看。

---

## 当前数据规模

```
用例       118 条   (ok=118  low=0  bad=0)
图片       113 张   （其中 112 张被 gt 引用，1 张备用）
任务链      30 条
step=1      34 条
step>=2     84 条
```

已覆盖的场景：

```
Medical Interpreting  3 家医院（白云区人民医院 / 陆丰市人民医院 / 中山三院）
  挂号、加就诊人、门诊缴费、医保码（2 条路径）、就诊记录、打开小程序、登录
Chat on WeChat
  打电话、聊天、扫码付/付款码、手机号加好友、二维码加好友（展示/扫描）、
  语音消息、共享位置、拍照发送
```

---

## 图片说明

`data/eval/images/` **入库**（已脱敏，保证任何人都能复现评测）。

- 目录结构：`images/<App>/<医院>/<流程>/xxx.jpg`
- 有 12 个文件名在多个目录下重名（例如 `Screenshot_20261102_090114.jpg` 在
  3 家医院的「医保码」目录下各有一份，且内容相同），所以**任何按文件名做的
  匹配/缓存都必须用相对路径**，不能只用 basename
- 图片已做 PII 打码（姓名、手机号）

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
| **分层目录下文件名重名**（新集） | `annotate.py` / `selfcheck.py` / `redact.py` 一律用相对路径；PNG 缓存键也换成相对路径 |
| gt 的 task_id 与 paths.jsonl 对不上 | `selfcheck.py` 区分「部分找不到=FAIL」/「全部找不到=WARN」；`repair_eval_data.py` 无可比对链时跳过缺步检查 |
