# 标注与评测工具使用说明

## 一条命令自检

改完任何东西，先跑这个。它会把「会让人白干」的坑全查一遍：

```bash
python tools/selfcheck.py
```

输出 `✅ 无错误` 才能继续。

---

## 完整流程

### 1. 准备任务链（`data/rag/paths.jsonl`）

每行一条链：

```json
{"task_id": "wechat.video_call_son",
 "name": "给儿子打视频电话",
 "app": "微信",
 "aliases": ["怎么给儿子打视频电话", "我想看看孙子", "怎么视频聊天"],
 "steps": [
   {"step": 1, "screen": "聊天列表", "target": "点儿子的微信", "hint": "点最上面「A.儿子」"},
   {"step": 2, "screen": "聊天页",   "target": "打开加号菜单",  "hint": "点输入框右边的「+」"},
   {"step": 3, "screen": "功能面板", "target": "点视频通话",    "hint": "点「视频通话」"}
 ]}
```

**三个层级别混**：

| 字段 | 是什么 | 例子 |
|---|---|---|
| `task_id` | 稳定标识，英文 | `wechat.video_call_son` |
| `aliases` | 老人开口说的话，进 RAG 索引 | `怎么给儿子打视频电话` |
| `steps[].target` | 这一步点哪，动作指令 | `点视频通话` |

### 2. 放图

截图放进 `data/eval/images/`，格式只认 **png / jpg / jpeg / webp**。

### 3. 标注

```bash
python tools/annotate.py
```

**界面**：

```
┌─────────────────────────────────────────────────────────┐
│ 所属任务链：[wechat.video_call_son ▼]  第几步：[2]      │
│            已有 第1步:点儿子的微信，下一个填 2           │
├─────────────────────────────────────────────────────────┤
│ 这一步点哪：[点视频通话        ] [保存(S)] [改文案(U)]  │
├──────────────┬──────────────────────────────────────────┤
│ 这张图的用例  │                                          │
│              │        ┌──────────┐                      │
│ 1.[链 第1步] │        │  截图     │  ← 完整显示          │
│   点儿子的微信│        │  ┌────┐  │                      │
│              │        │  └────┘  │                      │
├──────────────┴──────────────────────────────────────────┤
│ 001.jpg | 1260x2644 | 已标 2 个 | 合计 5 个用例          │
└─────────────────────────────────────────────────────────┘
```

**操作**：

| 操作 | 效果 |
|---|---|
| 左键拖 | 画框（青色 = 待保存） |
| 填「这一步点哪」 | 动作指令，不是任务名 |
| `S` | 保存，**不翻页**，step 自动 +1 |
| `U` | 改选中用例的文案 |
| `Del` | 删除选中用例 |
| `R` | 清空当前框 |
| 滚轮 | 缩放（0.4× – 6×） |
| 中键拖 | 平移 |
| `F` | 适应窗口 |
| `N` / `P` | 下一张 / 上一张 |

**颜色**：🟩 绿 = 已保存 · 🟨 黄 = 选中 · 🟦 青 = 正在拖

### 4. 跑评测

```bash
# 后端要先起来：uvicorn app.main:app --reload --port 8000
python tools/run_eval.py --limit 5     # 先试 5 个
python tools/run_eval.py               # 跑全量
python tools/eval.py --ci              # 出数字
```

**输出两个指标**：

```
hit@1             : 87.0%   (43/50)     ← 单步准确率
整链成功率：8/12 = 66.7%                  ← 用户真实感受
```

---

## 命名规则

### `goal`（这一步点哪）

```
点 + 元素上的字      点视频通话 / 点门诊缴费
选 + 元素上的字      选视频通话
拖 + 元素上的字      拖字体滑块
输 + 要输什么        输手机号
```

| ✅ 对 | ❌ 错 | 为什么 |
|---|---|---|
| 点视频通话 | 给儿子打视频电话 | 那是任务名，不是这一步 |
| 点相册 | 发照片 | 同上 |
| 点门诊缴费 | 交看病的钱 | 同上 |

**判断方法**：把这句话念给一个没用过这个 App 的人，他能毫不犹豫指出一个地方。

### 同一功能多个入口

图上如果有两个入口都能到同一个功能（顶部搜索框 + 右上角放大镜），标成**同一个 goal 两个框**，命中任意一个都算对。

```json
{"file": "images/001.jpg", "goal": "点搜索", "bbox": [60, 40, 900, 120]}
{"file": "images/001.jpg", "goal": "点搜索", "bbox": [1100, 40, 1200, 120]}
```

⚠️ **只有真的同一个功能才能这么做**。给不同功能标同名 goal = 给模型放水，hit@1 虚高。

---

## 数据格式

### `data/eval/gt.jsonl`

每行一个用例：

```json
{"file": "images/001.jpg", "task_id": "hospital.pay_bill", "step": 1,
 "goal": "点门诊缴费", "bbox": [500, 1146, 642, 1309]}
```

- `bbox` = `[x0, y0, x1, y1]`，**原图像素坐标**
- `task_id` / `step` 可省略（= 孤立单步用例，只参与单步统计）
- 同一图可有多行；同一 (图, task_id, step, goal) 多行 = 多个可接受框

### `data/eval/pred.jsonl`

`run_eval.py` 自动生成，**必须带上 task_id 和 step**，否则 `eval.py` 匹配不上。

### `quality` 字段（可选）

每条可带 `"quality": "ok" | "low" | "bad"`：

| 值 | 含义 | 评测行为 |
|---|---|---|
| `ok` | 正常 | 参与 |
| `low` | 可用但价值低 | 参与，但不计入整链 |
| `bad` | 分辨率过低 / 界面被遮挡 | **默认剔除**（`--include-bad` 可查看） |

用 `python tools/quality_tag.py` 自动打标。

---

## 工具清单

| 脚本 | 作用 |
|---|---|
| `annotate.py` | 手工拖框标注（GUI） |
| `ocr_dump.py` | 批量 OCR，输出每张图的文字坐标 |
| `auto_annotate.py` | OCR + 像素分析自动标注 |
| `sync_paths.py` | 从 gt.jsonl 反推 paths.jsonl |
| `quality_tag.py` | 给用例打 ok/low/bad 标记 |
| `priority.py` | 按优先级挑首批要跑的用例 |
| `run_eval.py` | 批量跑后端，产出 pred.jsonl |
| `eval.py` | 算 hit@1 + 整链成功率 |
| `selfcheck.py` | 数据完整性自检 |

---

## 已知的坑（都已在代码里处理）

| 坑 | 处理方式 |
|---|---|
| 长截图只显示左上角 | `redraw_canvas` 用 PIL resize 到画布尺寸 |
| 窗口刚开时量到 1px | `canvas_size` 先 `update_idletasks` |
| 从右下往左上拖成空框 | `on_release` + `screen_to_image` 双重 min/max |
| gt.jsonl 有坏行被静默丢掉 | `load_gt` 遇坏行直接退出并备份 |
| 写到一半断电文件截断 | `save_gt` 先写 tmp 再 `os.replace` |
| pred 缺 task_id 导致全 0% | `eval.py` 检测「一条都没匹配上」并报警 |
| JPG 打不开（tkinter 只认 PNG） | Pillow / Windows System.Drawing 三级兜底 |
| 上传 MIME 写死 image/jpeg | 按真实后缀给 MIME |
| 「待支付」误匹配「暂无待支付项目」 | `pick` 按「相等 → 前缀 → 包含」排序 |
| 彩色背景上找图标失控 | `is_gradient_background` 检测后退回纯文字框 |
| 科室 22 个候选被当成 22 步链 | `CHAIN_EXCLUDE` 排除出整链统计 |

---

## 测试脚本

| 脚本 | 测什么 |
|---|---|
| `tools/selfcheck.py` | 数据完整性、函数一致性、路径存在性 |
| `tools/_test_layout.py` | 缩放/居中/坐标往返（纯几何，不开窗口） |
| `tools/_test_gui.py` | 打开/拖框/保存/删除/缩放（无头，弹窗被替换） |

三个全绿再开始批量标注。

---

## 当前数据规模

```
全量用例   172 条  (ok=162  low=2  bad=8)
评测用     164 条  （bad 自动剔除）
首批 50    50 条   （优先高频场景，29 张图，10 条多步链）
任务链     127 条
覆盖图片   51 张
```

### 首批 50 条怎么跑

```bash
python tools/run_eval.py --gt data/eval/gt_first50.jsonl --out data/eval/pred50.jsonl
python tools/eval.py --gt data/eval/gt_first50.jsonl --pred data/eval/pred50.jsonl --ci
```

### 全量 164 条怎么跑

```bash
python tools/run_eval.py
python tools/eval.py --ci
```
