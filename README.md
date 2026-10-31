# 指一下 · NextTap

> 给"正在帮老人的人"用的即时指路工具：拍下屏幕，30 秒生成一张"下一步点这里"的卡片。

**赛事**：第九届传智杯 · AI WEB 网页开发挑战赛
**团队**：2 人 ｜ **提交**：2026-11-18

---

## 原创性声明

> 本项目在作者此前开源的 devSage（面向研发团队的知识库 Agent）基础上演进，复用了其基于 LangGraph 的 Agent 编排、三阶混合检索与 HITL 机制。
> 赛期内新增：截图多模态解析与元素定位、全屏指路交互、操作路径/报错知识库、适老化任务评测集。
> 新增部分提交记录见 Git 历史。

---

## 目录结构

```
.
├─ server/                 # FastAPI 后端
│  ├─ app/
│  │  ├─ main.py           # 入口
│  │  ├─ config.py         # 环境变量
│  │  ├─ schemas.py        # 请求/响应模型
│  │  ├─ vision/           # OCR 与候选构建
│  │  ├─ llm/              # 目标选择
│  │  └─ api/              # 路由
│  ├─ tests/               # 纯逻辑单测（不需要 OCR / LLM）
│  └─ requirements.txt
├─ web/                    # Vue 3 前端（见 web/README.md）
├─ tools/                  # 标注与评测脚本
│  ├─ annotate.py          # bbox 标注工具（tkinter，零依赖）
│  ├─ run_eval.py          # 批量跑定位，产出 pred.jsonl
│  └─ eval.py              # 算 hit@1，带 Wilson 置信区间
├─ data/eval/              # 评测集（图片不入库，只提交 gt.jsonl / pred.jsonl）
├─ docs/                   # 设计文档与技术文档
├─ .env.example
└─ docker-compose.yml
```

---

## 本地启动

```bash
# 1. 后端
cd server
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 配置
copy ..\.env.example ..\.env
# 编辑 .env，填入 LLM_API_KEY

# 3. 起服务
uvicorn app.main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/docs> 看接口。

```bash
# 4. 纯逻辑自测（不需要装 OCR）
python tests/test_candidates.py
```

---

## 核心接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/api/screenshot` | 上传截图，返回 `image_id` 与尺寸 |
| POST | `/api/locate` | `{image_id, goal}` → 目标元素、归一化坐标、一句话指引 |

坐标统一用 **0–1000 归一化**，前端按容器尺寸缩放，避开分辨率问题。

---

## 评测流程（gate：hit@1 ≥ 85%，50 张）

```bash
# 1. 把 50 张截图放进 data/eval/images/
# 2. 拖框标注真值
python tools/annotate.py

# 3. 后端起来后，批量跑
python tools/run_eval.py --limit 5     # 先试 5 张
python tools/run_eval.py               # 跑全量

# 4. 出数字
python tools/eval.py --ci
```

**命中判定**（对应定稿 D9）：预测框中心点落在真值框内即算 hit。

真值 `data/eval/gt.jsonl`，每行一张：

```json
{"file": "images/001.png", "goal": "给儿子打视频电话", "bbox": [100, 200, 300, 260]}
```

`bbox` 是**原图像素坐标** `[x0, y0, x1, y1]`。

---

## 当前进度

- [x] 设计框架、技术选型、项目定稿
- [x] 后端骨架：上传 / 定位 / 健康检查
- [x] 定位管线验证脚本（`tools/`）
- [x] 定位准确率 gate：**hit@1 91.5%（150/164）**，95% 区间 [86.2%, 94.8%]；step=1 91.1%，整链 91.4%；时延 p50 3.8s / p90 4.8s
- [ ] 前端：拍照上传 → 微调 → 全屏展示
- [ ] 多步流程与 RAG 预热
