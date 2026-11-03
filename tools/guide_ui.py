# -*- coding: utf-8 -*-
"""引导路径标注 Web 界面。

一个页面搞定三件事：
    1. 看截图（带当前真值红框、模型答案蓝框）
    2. 在图上拖框，标注「这一步该点哪里」
    3. 写这一步的引导话术（screen / target / hint）

用法：
    cd D:\\Nexttap
    python tools/guide_ui.py
    浏览器打开 http://127.0.0.1:8770

标注结果写回：
    data/eval/gt.jsonl           bbox 与 goal
    data/rag/paths.jsonl         steps 的 target / hint
"""
from __future__ import annotations

import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

REPO = Path(__file__).resolve().parent.parent
IMAGES = REPO / "data" / "eval" / "images"
GT = REPO / "data" / "eval" / "gt.jsonl"
PRED = REPO / "data" / "eval" / "pred.jsonl"
PATHS = REPO / "data" / "rag" / "paths.jsonl"
STATE = REPO / "data" / "eval" / "_guide_review.json"

PORT = int(os.getenv("GUIDE_UI_PORT", "8770"))


# ---------------------------------------------------------------------------
# 数据装载
# ---------------------------------------------------------------------------

def load_gt() -> dict:
    out = {}
    for line in GT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = f"{r['file']}|{r['task_id']}|{r['step']}|{r['goal']}"
        out.setdefault(k, []).append(r["bbox"])
    return out


def load_pred() -> dict:
    out = {}
    for line in PRED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        k = f"{r['file']}|{r['task_id']}|{r['step']}|{r['goal']}"
        out[k] = r
    return out


def load_paths() -> dict:
    out = {}
    for line in PATHS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[r["task_id"]] = r
    return out


def build_items() -> list:
    """构造待核验清单：所有失败用例 + 其所属链的全部步骤。"""
    gt = load_gt()
    pred = load_pred()
    paths = load_paths()

    def center(b):
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)

    def inside(box, pt):
        x0, x1 = sorted([box[0], box[2]])
        y0, y1 = sorted([box[1], box[3]])
        return x0 <= pt[0] <= x1 and y0 <= pt[1] <= y1

    items = []
    for k, boxes in gt.items():
        file, tid, step, goal = k.split("|")
        step = int(step)
        p = pred.get(k, {})
        err = p.get("error")
        ok = (not err) and p.get("bbox") and any(inside(b, center(p["bbox"])) for b in boxes)
        if ok:
            continue
        chain = paths.get(tid, {})
        step_def = {}
        for s in chain.get("steps") or []:
            if int(s.get("step", 0)) == step:
                step_def = s
                break
        items.append({
            "key": k,
            "file": file,
            "task_id": tid,
            "step": step,
            "goal": goal,
            "gt_boxes": boxes,
            "model_bbox": p.get("bbox"),
            "model_text": p.get("text"),
            "model_error": err,
            "chain_name": chain.get("name", ""),
            "total_steps": len(chain.get("steps") or []),
            "screen": step_def.get("screen", ""),
            "target": step_def.get("target", ""),
            "hint": step_def.get("hint", ""),
        })
    items.sort(key=lambda r: (r["task_id"], r["step"]))
    return items


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # 安静点
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)

        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/api/items":
            self._json(build_items())
            return

        if path.startswith("/img/"):
            rel = path[len("/img/"):]
            fp = IMAGES / rel
            if not fp.exists():
                self._send(404, b"not found", "text/plain")
                return
            suffix = fp.suffix.lower()
            ctype = {".png": "image/png", ".jpg": "image/jpeg",
                     ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(suffix, "application/octet-stream")
            self._send(200, fp.read_bytes(), ctype)
            return

        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._json({"ok": False, "error": "bad json"}, 400)
            return

        if path == "/api/save":
            self._json(self._save(body))
            return
        if path == "/api/delete":
            self._json(self._delete(body))
            return

        self._json({"ok": False, "error": "unknown"}, 404)

    # ---------- 写回 ----------

    def _backup(self, fp: Path):
        bak = fp.with_suffix(fp.suffix + ".bak-guide")
        if not bak.exists():
            shutil.copy2(fp, bak)

    def _save(self, body):
        """保存一条用例的 bbox 列表 + 引导话术。"""
        file = body["file"]
        tid = body["task_id"]
        step = int(body["step"])
        goal = body["goal"]
        boxes = body.get("boxes") or []
        screen = (body.get("screen") or "").strip()
        target = (body.get("target") or "").strip()
        hint = (body.get("hint") or "").strip()

        if not boxes:
            return {"ok": False, "error": "至少需要一个框"}

        # --- gt.jsonl：替换该 case 的所有行 ---
        self._backup(GT)
        rows = [json.loads(l) for l in GT.read_text(encoding="utf-8").splitlines() if l.strip()]
        keep = [r for r in rows if not (
            r["file"] == file and r["task_id"] == tid
            and int(r["step"]) == step and r["goal"] == goal
        )]
        for b in boxes:
            keep.append({
                "file": file, "task_id": tid, "step": step,
                "goal": goal, "bbox": [int(v) for v in b], "quality": "ok",
            })
        GT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in keep) + "\n",
                      encoding="utf-8")

        # --- paths.jsonl：更新该步的 target / hint / screen ---
        if target or hint or screen:
            self._backup(PATHS)
            paths = [json.loads(l) for l in PATHS.read_text(encoding="utf-8").splitlines() if l.strip()]
            for p in paths:
                if p["task_id"] != tid:
                    continue
                for s in p.get("steps") or []:
                    if int(s.get("step", 0)) == step:
                        if screen:
                            s["screen"] = screen
                        if target:
                            s["target"] = target
                        if hint:
                            s["hint"] = hint
            PATHS.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in paths) + "\n",
                             encoding="utf-8")

        return {"ok": True, "boxes": len(boxes)}

    def _delete(self, body):
        """把这条用例从 gt 里删掉（标错/不该有的用例）。"""
        file = body["file"]
        tid = body["task_id"]
        step = int(body["step"])
        goal = body["goal"]

        self._backup(GT)
        rows = [json.loads(l) for l in GT.read_text(encoding="utf-8").splitlines() if l.strip()]
        keep = [r for r in rows if not (
            r["file"] == file and r["task_id"] == tid
            and int(r["step"]) == step and r["goal"] == goal
        )]
        removed = len(rows) - len(keep)
        GT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in keep) + "\n",
                      encoding="utf-8")
        return {"ok": True, "removed": removed}


# ---------------------------------------------------------------------------
# 前端页面
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>NextTap 引导路径标注</title>
<style>
* { box-sizing: border-box; }
body { margin:0; font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
       background:#1c1c1e; color:#e5e5e7; display:flex; height:100vh; overflow:hidden; }
#side { width:290px; flex-shrink:0; background:#2c2c2e; overflow-y:auto; border-right:1px solid #3a3a3c; }
#side h2 { font-size:13px; color:#8e8e93; margin:14px 12px 6px; font-weight:600; letter-spacing:.5px; }
.item { padding:9px 12px; cursor:pointer; border-left:3px solid transparent; font-size:13px; line-height:1.5; }
.item:hover { background:#3a3a3c; }
.item.active { background:#0a84ff22; border-left-color:#0a84ff; }
.item.done { opacity:.45; }
.item .tid { color:#8e8e93; font-size:11px; }
.item .go { color:#e5e5e7; margin-top:2px; }
#main { flex:1; display:flex; flex-direction:column; min-width:0; }
#bar { padding:10px 16px; background:#2c2c2e; border-bottom:1px solid #3a3a3c;
       display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
#bar .title { font-size:15px; font-weight:600; }
#bar .tag { font-size:12px; padding:2px 8px; border-radius:4px; background:#3a3a3c; color:#8e8e93; }
#stage { flex:1; display:flex; min-height:0; }
#canvasWrap { flex:1; display:flex; align-items:center; justify-content:center;
              overflow:auto; padding:12px; background:#000; position:relative; }
#canvasWrap img { max-height:100%; max-width:100%; cursor:crosshair; user-select:none;
                  display:block; }
#panel { width:340px; flex-shrink:0; background:#2c2c2e; padding:16px; overflow-y:auto;
         border-left:1px solid #3a3a3c; }
#panel label { display:block; font-size:12px; color:#8e8e93; margin:14px 0 5px; }
#panel input, #panel textarea { width:100%; background:#1c1c1e; border:1px solid #3a3a3c;
         color:#e5e5e7; padding:8px; border-radius:6px; font-size:13px; font-family:inherit; }
#panel textarea { resize:vertical; min-height:56px; }
#panel .hint { font-size:11px; color:#636366; margin-top:4px; line-height:1.5; }
button { border:none; border-radius:7px; padding:9px 14px; font-size:13px; cursor:pointer;
         font-family:inherit; font-weight:500; }
.primary { background:#0a84ff; color:#fff; width:100%; margin-top:16px; }
.primary:hover { background:#409cff; }
.ghost { background:#3a3a3c; color:#e5e5e7; }
.danger { background:#ff453a22; color:#ff453a; }
.row { display:flex; gap:8px; margin-top:10px; }
.row button { flex:1; }
#boxes { font-size:12px; color:#8e8e93; margin-top:8px; }
#msg { position:fixed; top:16px; left:50%; transform:translateX(-50%);
       padding:10px 20px; border-radius:8px; font-size:14px; display:none; z-index:99; }
#msg.ok { background:#30d158; color:#000; }
#msg.err { background:#ff453a; color:#fff; }
.legend { font-size:11px; color:#8e8e93; line-height:1.9; }
.legend b { color:#ff453a; }
.legend i { color:#0a84ff; font-style:normal; }
</style></head>
<body>
<div id="side">
  <h2>待核验（<span id="count">0</span>）</h2>
  <div id="list"></div>
</div>
<div id="main">
  <div id="bar">
    <span class="title" id="title">选择左侧条目</span>
    <span class="tag" id="tagStep"></span>
    <span class="tag" id="tagChain"></span>
  </div>
  <div id="stage">
    <div id="canvasWrap"><img id="shot" /></div>
    <div id="panel">
      <div class="legend">
        <b>红框</b> = 当前真值 &nbsp; <i>蓝框</i> = 模型答案<br>
        在图上拖框 = 添加新的可接受入口
      </div>
      <label>用户目标（goal）</label>
      <input id="fGoal" readonly />
      <label>当前框（点选可删除）</label>
      <div id="boxes">—</div>
      <div class="row">
        <button class="ghost" id="btnClear">清空重画</button>
        <button class="ghost" id="btnUndo">撤销上一个</button>
      </div>
      <label>这一步在哪个界面（screen）</label>
      <input id="fScreen" placeholder="例如：微信首页" />
      <label>这一步要点什么（target）</label>
      <input id="fTarget" placeholder="例如：点右上角的搜索放大镜" />
      <div class="hint">写动作，不是任务名。✗「给儿子打电话」 ✓「点右下角加号」</div>
      <label>给老人看的提示（hint）</label>
      <textarea id="fHint" placeholder="例如：点右上角那个放大镜"></textarea>
      <div class="hint">一句话，20 字以内，带方位</div>
      <button class="primary" id="btnSave">保存这一条</button>
      <div class="row">
        <button class="danger" id="btnDelete">删除这条用例</button>
      </div>
    </div>
  </div>
</div>
<div id="msg"></div>

<script>
let items = [], cur = -1, boxes = [], img = document.getElementById('shot');
let dragging = false, startPt = null, tmpBox = null;
const wrap = document.getElementById('canvasWrap');

function toast(text, ok) {
  const m = document.getElementById('msg');
  m.textContent = text; m.className = ok ? 'ok' : 'err'; m.style.display = 'block';
  setTimeout(() => { m.style.display = 'none'; }, 2200);
}

function fitScale() {
  const r = img.getBoundingClientRect();
  return { sx: img.naturalWidth / r.width, sy: img.naturalHeight / r.height, r };
}

function drawOverlay() {
  // 用绝对定位的 div 画框。坐标基准必须是「图片实际渲染区域」，
  // 不是 wrap 的可视区 —— 早先用 wrap.getBoundingClientRect() 导致
  // 框全跑到左上角。
  document.querySelectorAll('.ovl').forEach(e => e.remove());
  if (!img.naturalWidth) return;

  const ir = img.getBoundingClientRect();
  const wr = wrap.getBoundingClientRect();
  const sx = img.naturalWidth / ir.width;
  const sy = img.naturalHeight / ir.height;
  // 图片左上角相对 wrap 内容区的偏移（含滚动）
  const offX = ir.left - wr.left + wrap.scrollLeft;
  const offY = ir.top - wr.top + wrap.scrollTop;

  const mk = (b, color, label, dashed) => {
    const d = document.createElement('div');
    d.className = 'ovl';
    d.style.cssText = `position:absolute; border:2px ${dashed?'dashed':'solid'} ${color};
      left:${offX + b[0]/sx}px; top:${offY + b[1]/sy}px;
      width:${(b[2]-b[0])/sx}px; height:${(b[3]-b[1])/sy}px;
      pointer-events:none; box-shadow:0 0 0 1px #00000066;`;
    if (label) {
      const s = document.createElement('span');
      s.textContent = label;
      s.style.cssText = `position:absolute; top:-17px; left:-2px; font-size:11px;
        background:${color}; color:#fff; padding:1px 5px; border-radius:3px; white-space:nowrap;`;
      d.appendChild(s);
    }
    wrap.appendChild(d);
  };

  const it = items[cur];
  if (it && it.model_bbox) mk(it.model_bbox, '#0a84ff', '模型', true);
  boxes.forEach((b, i) => mk(b, '#ff453a', '真值' + (boxes.length > 1 ? (i+1) : ''), false));
  if (tmpBox) mk(tmpBox, '#30d158', '新框', false);
}

function renderList() {
  const list = document.getElementById('list');
  list.innerHTML = '';
  items.forEach((it, i) => {
    const d = document.createElement('div');
    d.className = 'item' + (i === cur ? ' active' : '') + (it._done ? ' done' : '');
    d.innerHTML = `<div class="tid">${it.task_id} · 第${it.step}步</div>
                   <div class="go">${it.goal}</div>`;
    d.onclick = () => select(i);
    list.appendChild(d);
  });
  document.getElementById('count').textContent = items.length;
}

function select(i) {
  cur = i;
  const it = items[i];
  boxes = JSON.parse(JSON.stringify(it.gt_boxes));
  document.getElementById('title').textContent = it.chain_name || it.task_id;
  document.getElementById('tagStep').textContent = `第 ${it.step} / ${it.total_steps} 步`;
  document.getElementById('tagChain').textContent = it.task_id;
  document.getElementById('fGoal').value = it.goal;
  document.getElementById('fScreen').value = it.screen || '';
  document.getElementById('fTarget').value = it.target || '';
  document.getElementById('fHint').value = it.hint || '';
  img.src = '/img/' + encodeURI(it.file.replace(/^images\//, ''));
  renderList();
  updateBoxes();
  setTimeout(drawOverlay, 120);
}

function updateBoxes() {
  document.getElementById('boxes').textContent =
    boxes.length ? boxes.map((b,i) => `#${i+1} [${b.join(', ')}]`).join('  ') : '—';
  drawOverlay();
}

img.onload = drawOverlay;
img.onerror = () => toast('图片加载失败', false);
window.addEventListener('resize', drawOverlay);
wrap.addEventListener('scroll', drawOverlay);

function toImage(ev) {
  const ir = img.getBoundingClientRect();
  const sx = img.naturalWidth / ir.width;
  const sy = img.naturalHeight / ir.height;
  return [Math.round((ev.clientX - ir.left) * sx),
          Math.round((ev.clientY - ir.top) * sy)];
}

img.addEventListener('mousedown', e => {
  e.preventDefault();
  dragging = true; startPt = toImage(e);
});
img.addEventListener('mousemove', e => {
  if (!dragging) return;
  const p = toImage(e);
  tmpBox = [Math.min(startPt[0], p[0]), Math.min(startPt[1], p[1]),
            Math.max(startPt[0], p[0]), Math.max(startPt[1], p[1])];
  drawOverlay();
});
window.addEventListener('mouseup', e => {
  if (!dragging) return;
  dragging = false;
  if (tmpBox && (tmpBox[2] - tmpBox[0] > 8) && (tmpBox[3] - tmpBox[1] > 8)) {
    boxes.push(tmpBox);
  }
  tmpBox = null;
  updateBoxes();
});

document.getElementById('btnUndo').onclick = () => { boxes.pop(); updateBoxes(); };
document.getElementById('btnClear').onclick = () => { boxes = []; updateBoxes(); };

document.getElementById('btnSave').onclick = async () => {
  const it = items[cur];
  if (!it) return;
  if (!boxes.length) { toast('至少画一个框', false); return; }
  const r = await fetch('/api/save', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      file: it.file, task_id: it.task_id, step: it.step, goal: it.goal,
      boxes, screen: document.getElementById('fScreen').value,
      target: document.getElementById('fTarget').value,
      hint: document.getElementById('fHint').value,
    })
  });
  const j = await r.json();
  if (j.ok) { toast('已保存 ' + j.boxes + ' 个框', true); it._done = true; renderList(); }
  else toast(j.error || '保存失败', false);
};

document.getElementById('btnDelete').onclick = async () => {
  const it = items[cur];
  if (!it) return;
  if (!confirm('确定删除这条用例？\n' + it.goal)) return;
  const r = await fetch('/api/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({file: it.file, task_id: it.task_id, step: it.step, goal: it.goal})
  });
  const j = await r.json();
  if (j.ok) {
    toast('已删除', true);
    items.splice(cur, 1); cur = Math.min(cur, items.length - 1);
    renderList();
    if (cur >= 0) select(cur);
  } else toast('删除失败', false);
};

// 键盘：J/K 上下切换
document.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === 'j' && cur < items.length - 1) select(cur + 1);
  if (e.key === 'k' && cur > 0) select(cur - 1);
});

(async () => {
  const r = await fetch('/api/items');
  items = await r.json();
  renderList();
  if (items.length) select(0);
  else document.getElementById('title').textContent = '没有待核验的用例 ✓';
})();
</script>
</body></html>"""


def main() -> None:
    items = build_items()
    print(f"待核验 {len(items)} 条")
    for it in items[:5]:
        print(f"  {it['task_id']} 第{it['step']}步  {it['goal']}")
    if len(items) > 5:
        print(f"  ... 共 {len(items)} 条")
    print()
    print(f"浏览器打开： http://127.0.0.1:{PORT}")
    print("  拖框 = 添加可接受入口 | J/K = 上下切换 | Ctrl+C 退出")
    print()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
