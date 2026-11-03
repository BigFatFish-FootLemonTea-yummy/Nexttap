# 指一下 · NextTap

> 给"正在帮老人的人"用的即时指路工具：拍下屏幕，30 秒生成一张"下一步点这里"的卡片。

**赛事**：第九届传智杯 · AI WEB 网页开发挑战赛
**团队**：2 人 ｜ **提交**：2026-11-18

---

## 原创性声明

> 本项目在作者此前开源的 devSage（面向研发团队的知识库 Agent）基础上演进，参考了其 Agent 编排思路与三阶混合检索设计（本项目未引入 LangGraph，Agent 层为自研显式状态机）。
> 赛期内新增：截图多模态解析与元素定位、全屏指路交互、操作路径/报错知识库、适老化任务评测集。
> 新增部分提交记录见 Git 历史。

---

## 目录结构

```
.
├─ server/                 # FastAPI 后端
│  ├─ app/
│  │  ├─ main.py           # 入口（启动时预热 OCR + 检索模型）
│  │  ├─ config.py         # 环境变量
│  │  ├─ schemas.py        # 请求/响应模型
│  │  ├─ vision/           # OCR 与候选构建
│  │  ├─ llm/              # 目标选择
│  │  ├─ guide.py          # 三阶检索 + 风险判断 + 卡片组装
│  │  ├─ mcp_server.py     # MCP 服务，4 个工具
│  │  └─ api/              # 路由
│  ├─ tests/               # 纯逻辑单测（不需要 OCR / LLM）
│  ├─ Dockerfile
│  └─ requirements.txt
├─ web/                    # Vue 3 前端（见 web/README.md）
│  ├─ nginx.conf           # 同域反代 + SPA 回落
│  └─ Dockerfile           # 两阶段构建
├─ tools/                  # 标注、评测与数据维护
│  ├─ annotate.py          # bbox 标注工具（tkinter，零依赖）
│  ├─ run_eval.py          # 批量跑定位，产出 pred.jsonl
│  ├─ eval.py              # 定位 hit@1 + 分阶段 + 聚类区间
│  ├─ eval_retrieval.py    # 检索 Recall@K / MRR / 消融
│  ├─ selfcheck.py         # 数据自检（改完先跑这个）
│  ├─ repair_eval_data.py  # 一致性审计
│  ├─ audit_goal_leak.py   # goal 泄漏审计
│  ├─ guide_ui.py          # Web 标注界面（拖框 + 写引导话术）
│  ├─ collect_failed.py    # 从 pred 里筛出失败用例
│  └─ rebuild_paths.py     # 重建任务链 name/aliases
├─ data/
│  ├─ eval/                # 定位评测：110 条真值 + 110 张脱敏截图
│  └─ rag/                 # 30 条任务链 + 三套检索评测集
├─ docs/                   # 交接文档 / 技术文档 8 章 / 部署指南 / 演示速查卡
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

## 评测流程（gate：hit@1 ≥ 85%，110 张图 / 110 用例）

```bash
# 1. 截图放在 data/eval/images/（110 张已脱敏，已入库）
# 2. 拖框标注真值
python tools/annotate.py

# 3. 数据自检（改完任何东西先跑）
python tools/selfcheck.py

# 4. 后端起来后，批量跑
python tools/run_eval.py --limit 5     # 先试 5 条
python tools/run_eval.py               # 跑全量，约 8–9 分钟

# 5. 出数字
python tools/eval.py --ci
```

> 改了 goal 就必须重跑模型，否则 `pred.jsonl` 全部失配。
> 完整流程见 `docs/交接文档.md` §十三。

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
- [x] 定位准确率 gate：**hit@1 98.2%（108/110）**，95% 聚类区间 [95.5%, 100.0%]；step=1 **100%**（30/30），step≥2 97.5%（78/80），整链 **93.3%**（28/30）；时延 p50 3.1s / p90 4.5s
  - 新评测集（2026-11-03）：30 条真实流程 / 110 用例 / 110 张图，覆盖微信 10 条 + 三家医院 20 条
  - 无文字图标（加号 / 搜索镜 / 快门 / 喇叭）由 `server/app/vision/icons.py` 检测后进入候选，解决了「模型看不见按钮」导致的 422
  - goal 泄漏已归零：step=1 共 30 条全部是用户口语，不含目标框文字（`tools/audit_goal_leak.py` 可复验）
- [x] 前端：拍照上传 → 叠圈微调 → 全屏指路（含示例一键体验）
- [x] MCP 服务：4 个工具（locate_element / search_guide / check_risk / compose_card）
- [x] **RAG 三阶检索**：词面 + 向量（bge-small-zh ONNX）+ RRF，含域外拒识
  - 数据重建（name/aliases）后 **Recall@5 84.6%**（留出集，净化口径，95%CI [75.0, 94.2]）
  - 详见 `docs/RAG实现记录.md`，其中记录了两次返工和一次过拟合回滚
- [x] 部署配置：Docker Compose 双服务 + Nginx 同域反代（**未上真机**）
- [x] 技术文档 8 章（`docs/技术文档/00_目录.md`）
- [ ] 5 人真实访谈（W2 硬交付）
- [ ] 演示视频
- [ ] 多步流程预热（后端能力已就绪，前端未接入）
