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
├─ tools/                  # 评测脚本
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

## 当前进度

- [x] 设计框架、技术选型、项目定稿
- [x] 定位管线验证脚本（`tools/`）
- [x] 后端骨架：上传 / 定位 / 健康检查
- [] 定位准确率 gate（目标 hit@1 ≥ 85%，50 张）
- [ ] 前端：拍照上传 → 微调 → 全屏展示
- [ ] 多步流程与 RAG 预热
