# RAG 模块替换说明

> 写给 A（或接手 RAG 的同学）。
> 目的：如果你们那边有现成的检索模块，**可以整块换掉**，不影响其他部分。

---

## 一、为什么留这个文件

当前 `server/app/guide.py` 里的三阶检索（词面 + 向量 + RRF）是 B 临时补的，
目的是让检索这项不至于空着。**但它不是最优解**，而且：

- 评测集是 B 自己写的，有自评嫌疑（详见 `docs/RAG实现记录.md`）
- aliases 数据（306 条模板产物）没修
- 距离 90% 目标还差 9–19 个百分点

**如果 A 那边有现成的、经过验证的检索方案，应该直接换掉。**
本文档说明怎么换、换的时候必须保住什么。

---

## 二、唯一的硬约束：返回结构不能变

```python
def search(query: str, top_k: int = 3, min_score: float = 0.42) -> List[Dict[str, Any]]:
    """
    返回：
    [
      {
        "task_id": str,      # 任务链编号，如 "wechat.video_call_son"
        "name":    str,      # 任务名，如 "儿子的微信"
        "app":     str,      # 所属 App，如 "wechat"
        "score":   float,    # 相关度，0–1（RRF 模式下是融合分，量纲不同但单调）
        "steps":   list,     # 完整步骤，来自 paths.jsonl
      },
      ...
    ]
    """
```

**为什么不能变**：MCP 的 `search_guide` 直接依赖它：

```python
# server/app/mcp_server.py
@mcp.tool()
def search_guide(query: str, top_k: int = 3) -> Dict[str, Any]:
    hits = guide.search(query, top_k=top_k)
    return {
        "query": query,
        "count": len(hits),
        "results": hits,
        "note": "" if hits else "没有匹配到标准路径，可以只用 locate_element 逐步定位",
    }
```

如果结构变了，MCP 工具、以及后续可能加的 HTTP 接口都要跟着改。

---

## 三、替换方式

### 方式一：只换 `search()` 的实现（推荐）

`guide.py` 里这三个函数是检索的全部：

| 函数 | 职责 |
|---|---|
| `search()` | 入口，返回上述结构 |
| `_score()` | 词面打分（可删） |
| `_VectorIndex` | 向量索引（可删） |
| `_rrf_fuse()` | RRF 融合（可删） |
| `_shares_token()` | 域外拒识辅助（可删） |

**保留**：`load_paths()`、`check_risk()`、`compose_card()` —— 这三个 MCP 也在用。

替换时把上面几个函数换成你们的实现，保持 `search()` 签名与返回结构即可。

### 方式二：整个文件换掉

如果你们的模块自包含，直接替换 `guide.py`，但要保证同样导出：

```python
load_paths() -> List[dict]
search(query, top_k=3, min_score=0.42) -> List[dict]
check_risk(text) -> dict
compose_card(text, instruction, task_id="", step=1) -> dict
```

`server/tests/test_guide.py` 的 14 项断言可以直接当验收测试跑：

```powershell
python server/tests/test_guide.py
```

---

## 四、如果沿用当前实现，需要知道的设计约束

### 4.1 环境变量开关

| 变量 | 取值 | 说明 |
|---|---|---|
| `NEXTTAP_RETRIEVAL` | `lexical` / `hybrid` / `hybrid_rerank` | 默认 `hybrid` |
| `NEXTTAP_EMBED_MODEL` | 默认 `BAAI/bge-small-zh-v1.5` | 换模型只改这里 |
| `NEXTTAP_VECTOR_MIN` | 默认 `0.55` | 域外拒识的绝对阈值 |
| `NEXTTAP_VECTOR_GAP` | 默认 `0.02` | 域外拒识的相对领先阈值 |

### 4.2 降级是硬要求

**fastembed 没装或模型加载失败时，必须自动退回纯词面，不能抛异常。**

已验证：模拟 `ImportError` 后 `search()` 仍返回正确结果。

这条要求的原因是：部署环境可能装不上 onnxruntime，但产品不能因此挂掉。

### 4.3 域外拒识必须保留

当前实现在 hybrid 模式下有个额外判断：

```python
top_sim >= 0.55 and (top1 - top5) >= 0.02
```

**作用**：域外查询（"我想买火车票"）返回空列表，而不是硬凑 3 条。

如果不保留这个，MCP 的 `note: "没有匹配到标准路径"` 永远拿不到，
客户端会拿着无关结果继续执行。

**回归测试**（当前 8/8 通过）：

```python
OUT_OF_DOMAIN = ["我想买火车票", "今天天气怎么样", "我要打游戏", "abcxyz",
                 "帮我订个外卖", "明天会下雨吗", "给我讲个笑话", "你是谁"]
# 全部应返回 []
```

---

## 五、数据层面的待办（不论用谁的算法都要做）

这些和算法无关，**换实现也得做**：

| # | 待办 | 为什么 |
|---|---|---|
| 1 | **修 `paths.jsonl` 的 306 条模板 aliases** | 实测对检索零贡献，全是"怎么X/我要X/帮我X" |
| 2 | **改两处命名错** | `hospital.pick_dept` 的 name 应为「选择科室」（它是 21 个科室的选择页）；`hospital.pick_slot` 待确认 |
| 3 | **换真人评测集** | 当前 68 条是 B 手写的，有自评污染 |
| 4 | **补 53 条未覆盖任务链** | 44% 的链从没测过，且恰好是同屏混淆的难例 |

第 1、3 条都依赖**真人访谈**，和 W2 的 5 人访谈是同一批人，可以顺带做。

---

## 六、当前实现的实测数字（供对照）

### 线上默认配置（hybrid，不跑 rerank）

| 集合 | 条数 | Recall@1 | Recall@3 | Recall@5 | MRR |
|---|---|---|---|---|---|
| `retrieval_eval.jsonl` | 68 | 47.1% | 72.1% | 79.4% | 0.608 |
| `retrieval_heldout.jsonl` | 52 | 48.1% | 65.4% | 71.2% | 0.581 |

**对外报留出集 71.2%**，原集只作回归基线（有自评污染）。

### 消融

| 配置 | 原集 R@5 | 留出集 R@5 | 单次耗时 |
|---|---|---|---|
| 纯词面 | 47.1% | 46.2% | ~10ms |
| + 向量 + RRF | 79.4% | 71.2% | ~100ms |
| + rerank（仅离线） | 80.9% | 80.8% | 3.8s |

**换实现后建议用同样的两套评测集对比**，至少别比这个差。

---

## 七、替换后的检查清单

```powershell
# 1. 契约不变
python server/tests/test_guide.py          # 14 项全过

# 2. 检索指标不退步
python tools/eval_retrieval.py
python tools/eval_retrieval.py --data data/rag/retrieval_heldout.jsonl

# 3. 域外拒识仍有效
#    （8 条域外查询应全部返回空，见 §4.3）

# 4. MCP 工具仍正常
python -c "import sys; sys.path.insert(0,'server'); from app import guide; print(guide.search('退休金那个', 3))"

# 5. 数据自检
python tools/selfcheck.py
```

---

## 八、一句话总结

> **`search()` 的返回结构是唯一不能动的契约，其他随便换。**
> 换完跑一遍 `test_guide.py` 和两套检索评测，别比现在差就行。

如果换完发现指标下降，先别急着调算法——**先看是不是数据问题**
（aliases 模板、评测集污染），这两块的收益比调算法大。
