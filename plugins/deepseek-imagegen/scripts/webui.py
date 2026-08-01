#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DeepSeek ImageGen 本地设置页面（可视化配置工具）。

用法：
  python scripts/webui.py                 # 默认 http://127.0.0.1:8766
  python scripts/webui.py --port 9000
  或：python scripts/image_gen.py webui

功能：
  - 一键从本地 Vertex Proxy 导入端口、密钥和模型列表，自动选中最佳图像模型
  - 可视化编辑默认后端与各后端参数（pollinations / siliconflow / vertex / sd-webui / comfyui）
  - 测试后端连通性、一键试生成小图、运行 doctor 诊断
  - 保存到 ~/.deepseek-imagegen/config.json（与 image_gen.py 共用）

安全说明：只监听 127.0.0.1，请勿修改为 0.0.0.0 暴露到局域网。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import image_gen  # noqa: E402


DEFAULT_PORT = 8766

HTML_PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek ImageGen 设置</title>
<style>
  :root { --bg:#0f1220; --card:#181c30; --line:#2a3050; --fg:#e8eaf6; --dim:#9aa3c0; --acc:#7c3aed; --ok:#2ecc71; --bad:#ff5c5c; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font-family:"Microsoft YaHei","Segoe UI",sans-serif; }
  header { padding:18px 26px; border-bottom:1px solid var(--line); background:linear-gradient(90deg,#241a3f,#1a1433); }
  header h1 { margin:0; font-size:20px; }
  header p { margin:4px 0 0; color:var(--dim); font-size:12px; word-break:break-all; }
  main { max-width:960px; margin:0 auto; padding:18px 26px 60px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin-bottom:16px; }
  .card h2 { margin:0 0 12px; font-size:15px; }
  label { display:block; font-size:12px; color:var(--dim); margin:8px 0 4px; }
  input, select { width:100%; background:#0e1122; border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:8px 10px; font-size:13px; }
  input:focus, select:focus { outline:none; border-color:var(--acc); }
  .row2 { display:grid; grid-template-columns:3fr 1fr; gap:8px; align-items:end; }
  .grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; }
  .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
  button { background:#232a4d; color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:8px 14px; font-size:13px; cursor:pointer; }
  button:hover { border-color:var(--acc); }
  button.primary { background:var(--acc); border-color:var(--acc); }
  button.small { padding:6px 10px; font-size:12px; }
  .actions { display:flex; gap:10px; flex-wrap:wrap; }
  #msg { margin-top:10px; white-space:pre-wrap; font-size:13px; }
  .ok { color:var(--ok); } .bad { color:var(--bad); }
  .hint { color:var(--dim); font-size:12px; }
  #vpResult { margin-top:10px; }
  .tag { display:inline-block; background:#241a3f; border:1px solid var(--line); border-radius:20px; padding:2px 10px; font-size:11px; color:var(--acc); margin:2px 4px 2px 0; }
</style>
</head>
<body>
<header>
  <h1>🎨 DeepSeek ImageGen 设置</h1>
  <p id="cfgPath">配置加载中…</p>
</header>
<main>
  <section class="card">
    <h2>① 一键导入 Vertex Proxy（本地代理）</h2>
    <div class="row2">
      <input id="vpDir" placeholder="Vertex Proxy 目录（含 config\config.json、api_keys.txt、models.json）">
      <button onclick="loadVertex()">读取并导入</button>
    </div>
    <div id="vpResult"></div>
  </section>

  <section class="card">
    <h2>② 默认后端与全局设置</h2>
    <div class="grid">
      <div><label>默认后端</label>
        <select id="default_backend">
          <option value="vertex">vertex（本地代理，自动选最佳图像模型）</option>
          <option value="pollinations">pollinations（免费免密钥）</option>
          <option value="siliconflow">siliconflow（FLUX）</option>
          <option value="sd-webui">sd-webui（本地）</option>
          <option value="comfyui">comfyui（本地）</option>
        </select>
      </div>
      <div><label>默认尺寸</label><input id="default_size" placeholder="1024x1024"></div>
      <div><label>默认保存目录（留空=当前目录）</label><input id="save_dir" placeholder="例如 D:\images"></div>
      <div><label>自动副本目录（留空=不复制）</label><input id="mirror_dir" placeholder="C:\Users\yjq\Pictures\codex"></div>
      <div><label>默认负面提示词</label><input id="default_negative" placeholder="文字, 水印, 低质量"></div>
    </div>
  </section>

  <section class="card">
    <h2>③ 后端参数</h2>
    <div class="grid">
      <div><label>Vertex 目录</label><input id="vertex_dir" placeholder="代理目录"></div>
      <div><label>Vertex API 地址（留空=自动）</label><input id="vertex_base" placeholder="http://127.0.0.1:2156/v1"></div>
      <div><label>Vertex API Key（留空=自动读取）</label><input id="vertex_key" type="password" placeholder="sk-..."></div>
      <div><label>Vertex 图像模型（留空=自动最佳）</label><input id="vertex_model" placeholder="gemini-3-pro-image"></div>
      <div><label>Pollinations 模型（留空=默认）</label><input id="pollinations_model" placeholder="flux"></div>
      <div><label>SiliconFlow 地址</label><input id="sf_base" placeholder="https://api.siliconflow.cn/v1"></div>
      <div><label>SiliconFlow API Key</label><input id="sf_key" type="password" placeholder="sk-..."></div>
      <div><label>SiliconFlow 模型</label><input id="sf_model" placeholder="black-forest-labs/FLUX.1-schnell"></div>
      <div><label>SD WebUI 地址</label><input id="sd_base" placeholder="http://127.0.0.1:7860"></div>
      <div><label>SD WebUI 采样器</label><input id="sd_sampler" placeholder="Euler a"></div>
      <div><label>SD WebUI 步数</label><input id="sd_steps" type="number" placeholder="28"></div>
      <div><label>SD WebUI CFG</label><input id="sd_cfg" type="number" step="0.5" placeholder="7"></div>
      <div><label>ComfyUI 地址</label><input id="cf_base" placeholder="http://127.0.0.1:8188"></div>
      <div><label>ComfyUI Checkpoint（留空=自动）</label><input id="cf_checkpoint" placeholder="sd_xl_base_1.0.safetensors"></div>
      <div><label>ComfyUI 步数</label><input id="cf_steps" type="number" placeholder="28"></div>
      <div><label>ComfyUI CFG</label><input id="cf_cfg" type="number" step="0.5" placeholder="7"></div>
    </div>
  </section>

  <section class="card">
    <h2>④ 保存与测试</h2>
    <div class="actions">
      <button class="primary" onclick="save()">💾 保存配置</button>
      <button onclick="testBackend()">🔌 测试默认后端连通性</button>
      <button onclick="testGenerate()">🖼 试生成一张测试图（1024x1024）</button>
      <button onclick="runDoctor()">🔍 运行诊断（doctor）</button>
    </div>
    <div>
      <label>试生成提示词</label>
      <input id="testPrompt" placeholder="一只戴宇航员头盔的柴犬，写实风格">
    </div>
    <div id="msg"></div>
  </section>
</main>
<script>
let state = { config: null };
const $ = id => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

async function loadConfig() {
  const j = await api("/api/config");
  state.config = j.config;
  $("cfgPath").textContent = "配置文件：" + j.path;
  render();
}

function render() {
  const c = state.config;
  $("default_backend").value = c.default_backend || "vertex";
  $("default_size").value = c.default_size || "1024x1024";
  $("save_dir").value = c.save_dir || "";
  $("mirror_dir").value = c.mirror_dir || "";
  $("default_negative").value = c.default_negative || "";
  $("vertex_dir").value = c.vertex && c.vertex.dir || "";
  $("vertex_base").value = c.vertex && c.vertex.base_url || "";
  $("vertex_key").value = c.vertex && c.vertex.api_key || "";
  $("vertex_model").value = c.vertex && c.vertex.model || "";
  $("pollinations_model").value = c.pollinations && c.pollinations.model || "";
  $("sf_base").value = c.siliconflow && c.siliconflow.base_url || "";
  $("sf_key").value = c.siliconflow && c.siliconflow.api_key || "";
  $("sf_model").value = c.siliconflow && c.siliconflow.model || "";
  $("sd_base").value = c.sd_webui && c.sd_webui.base_url || "";
  $("sd_sampler").value = c.sd_webui && c.sd_webui.sampler_name || "";
  $("sd_steps").value = c.sd_webui && c.sd_webui.steps || "";
  $("sd_cfg").value = c.sd_webui && c.sd_webui.cfg_scale || "";
  $("cf_base").value = c.comfyui && c.comfyui.base_url || "";
  $("cf_checkpoint").value = c.comfyui && c.comfyui.checkpoint || "";
  $("cf_steps").value = c.comfyui && c.comfyui.steps || "";
  $("cf_cfg").value = c.comfyui && c.comfyui.cfg || "";
}

function collect() {
  const c = JSON.parse(JSON.stringify(state.config || {}));
  c.default_backend = $("default_backend").value;
  c.default_size = $("default_size").value || "1024x1024";
  c.save_dir = $("save_dir").value.trim();
  c.mirror_dir = $("mirror_dir").value.trim();
  c.default_negative = $("default_negative").value;
  c.vertex = {
    dir: $("vertex_dir").value.trim(),
    base_url: $("vertex_base").value.trim(),
    api_key: $("vertex_key").value.trim(),
    model: $("vertex_model").value.trim(),
  };
  c.pollinations = Object.assign({}, c.pollinations, { model: $("pollinations_model").value.trim() });
  c.siliconflow = Object.assign({}, c.siliconflow, {
    base_url: $("sf_base").value.trim(),
    api_key: $("sf_key").value.trim(),
    model: $("sf_model").value.trim(),
  });
  c.sd_webui = Object.assign({}, c.sd_webui, {
    base_url: $("sd_base").value.trim(),
    sampler_name: $("sd_sampler").value.trim(),
    steps: parseInt($("sd_steps").value) || 28,
    cfg_scale: parseFloat($("sd_cfg").value) || 7,
  });
  c.comfyui = Object.assign({}, c.comfyui, {
    base_url: $("cf_base").value.trim(),
    checkpoint: $("cf_checkpoint").value.trim(),
    steps: parseInt($("cf_steps").value) || 28,
    cfg: parseFloat($("cf_cfg").value) || 7,
  });
  return c;
}

function showMsg(text, ok) {
  const m = $("msg");
  m.className = ok ? "ok" : "bad";
  m.textContent = text;
}

async function save() {
  const r = await api("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collect()),
  });
  showMsg("✅ " + r.message, true);
  state.config = collect();
}

async function loadVertex() {
  const dir = $("vpDir").value.trim() || undefined;
  const r = await api("/api/vertex" + (dir ? "?dir=" + encodeURIComponent(dir) : ""));
  if (!r.found) { showMsg("未找到 Vertex Proxy 配置：" + r.reason, false); return; }
  const keys = r.keys.map(k => `<option value="${esc(k)}">${esc(k.slice(0, 10) + "…" + k.slice(-4))}</option>`).join("");
  const models = r.models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  const best = esc(r.best_model || "");
  $("vpResult").innerHTML =
    `<div class="row2" style="margin-top:10px">` +
    `<div><label>API 地址</label><input id="vpBase" value="${esc(r.base_url)}"></div>` +
    `<div><label>图像模型（已自动选最佳）</label><select id="vpModel">${models}</select></div>` +
    `<div><label>API Key</label><select id="vpKey">${keys}</select></div>` +
    `<button class="primary" onclick="applyVertex()">应用到后端参数</button></div>` +
    `<p class="hint">端口 ${r.port}，共 ${r.models.length} 个模型，图像模型 ${r.image_models.length} 个，最佳：<span class="tag">${best}</span></p>`;
  $("vpResult").querySelector("#vpModel").value = r.best_model || "";
}

function applyVertex() {
  const base = $("vpResult").querySelector("#vpBase").value;
  const model = $("vpResult").querySelector("#vpModel").value;
  const key = $("vpResult").querySelector("#vpKey").value;
  $("vertex_dir").value = $("vpDir").value.trim() || state.config.vertex.dir || "";
  $("vertex_base").value = "";
  $("vertex_key").value = key;
  $("vertex_model").value = model;
  $("default_backend").value = "vertex";
  showMsg("已应用到后端参数（地址留空=按端口自动）。记得点「保存配置」。", true);
}

async function testBackend() {
  const backend = $("default_backend").value;
  const r = await api("/api/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ backend }),
  });
  showMsg("测试 " + backend + "：" + r.message, r.ok);
}

async function testGenerate() {
  const prompt = $("testPrompt").value.trim() || "a cute astronaut dog";
  const r = await api("/api/test-generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, backend: $("default_backend").value }),
  });
  if (r.ok) {
    showMsg("✅ 生成成功：" + r.path + "\n尺寸 " + r.size + "，种子 " + r.seed, true);
  } else {
    showMsg("❌ 生成失败：" + r.error, false);
  }
}

async function runDoctor() {
  const r = await api("/api/doctor");
  showMsg(r.report || r.error || "诊断完成", r.ok);
}

loadConfig().catch(e => showMsg("加载失败：" + e.message, false));
</script>
</body>
</html>
"""


def config_path() -> str:
    return str(image_gen.CONFIG_FILE)


def load_raw_config() -> dict[str, Any]:
    path = config_path()
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return image_gen.load_config()


def save_config(obj: Any) -> str:
    if not isinstance(obj, dict):
        raise ValueError("配置必须是 JSON 对象")
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def read_vertex(dirpath: str | None) -> dict[str, Any]:
    candidates = []
    if dirpath and dirpath.strip():
        candidates.append(dirpath.strip().strip('"'))
    env_dir = os.environ.get("VERTEX_PROXY_DIR")
    if env_dir:
        candidates.append(env_dir)
    candidates.append(image_gen.VERTEX_DEFAULT_DIR)
    for cand in candidates:
        if cand and os.path.isfile(os.path.join(cand, "config", "config.json")):
            try:
                info = image_gen.discover_vertex(
                    {"vertex": {"dir": cand, "base_url": "", "api_key": "", "model": ""}}
                )
            except image_gen.GenError as exc:
                return {"found": False, "reason": str(exc)}
            keys: list[str] = []
            keys_file = os.path.join(cand, "config", "api_keys.txt")
            if os.path.isfile(keys_file):
                with open(keys_file, "r", encoding="utf-8") as handle:
                    keys = [
                        k
                        for k in (
                            image_gen.read_first_api_key(handle.read()),
                        )
                        if k
                    ]
                # 读取全部有效 Key 供选择
                with open(keys_file, "r", encoding="utf-8") as handle:
                    keys = []
                    for line in handle.read().splitlines():
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("sk-"):
                            keys.append(line)
                        elif ":" in line:
                            parts = line.split(":", 1)
                            if parts[1].strip().startswith("sk-"):
                                keys.append(parts[1].strip())
            return {
                "found": True,
                "dir": cand,
                "port": info["port"],
                "base_url": info["base_url"],
                "keys": keys,
                "models": info["models"],
                "image_models": info["image_models"],
                "best_model": info["model"],
            }
    return {"found": False, "reason": "未找到 config\\config.json，请填写完整目录路径"}


def test_backend(backend: str) -> dict[str, Any]:
    backend = image_gen.resolve_backend(backend, image_gen.load_config())
    cfg = image_gen.load_config()
    if backend == "vertex":
        info = image_gen.discover_vertex(cfg)
        image_gen._http(
            f"{info['base_url'].rstrip('/')}/models",
            headers={"Authorization": f"Bearer {info['api_key']}", "User-Agent": image_gen.BROWSER_UA},
            timeout=image_gen.HEALTH_TIMEOUT,
        )
        return {"ok": True, "message": f"连通正常（{info['base_url']}），模型 {info['model']}"}
    if backend == "pollinations":
        base = cfg["pollinations"]["base_url"].rstrip("/")
        image_gen._http(base, timeout=image_gen.HEALTH_TIMEOUT, headers={"User-Agent": image_gen.BROWSER_UA})
        return {"ok": True, "message": "Pollinations 可达（免费免密钥）"}
    if backend == "siliconflow":
        key = (cfg.get("siliconflow", {}).get("api_key") or "").strip()
        if not key:
            raise image_gen.GenError("未配置 siliconflow.api_key")
        return {"ok": True, "message": "已配置 API Key，可尝试试生成"}
    if backend == "sd-webui":
        base = cfg["sd_webui"]["base_url"].rstrip("/")
        image_gen._http(f"{base}/sdapi/v1/sd-models", timeout=image_gen.HEALTH_TIMEOUT)
        return {"ok": True, "message": "SD WebUI 连通正常"}
    if backend == "comfyui":
        base = cfg["comfyui"]["base_url"].rstrip("/")
        image_gen._http(f"{base}/system_stats", timeout=image_gen.HEALTH_TIMEOUT)
        return {"ok": True, "message": "ComfyUI 连通正常"}
    return {"ok": False, "message": f"未知后端 {backend}"}


def test_generate(prompt: str, backend: str) -> dict[str, Any]:
    preview_dir = os.path.join(tempfile.gettempdir(), "deepseek-imagegen-preview")
    result = image_gen.generate_image(
        prompt,
        backend=backend or "auto",
        size="1024x1024",
        out=preview_dir,
    )
    return result


def run_doctor_json() -> dict[str, Any]:
    try:
        report = image_gen.cmd_doctor(type("A", (), {"json": True})())
        return {"ok": report.get("ok", False), "report": json.dumps(report, ensure_ascii=False, indent=2)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A003
        pass

    def _send_json(self, obj: Any, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send_html(HTML_PAGE)
        if path == "/api/config":
            return self._send_json({"path": config_path(), "config": load_raw_config()})
        if path == "/api/vertex":
            query = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            return self._send_json(read_vertex(query.get("dir", [None])[0]))
        if path == "/api/doctor":
            return self._send_json(run_doctor_json())
        return self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/api/config":
                saved = save_config(self._read_json())
                return self._send_json({"ok": True, "message": "已保存到 " + saved, "path": saved})
            if path == "/api/test":
                body = self._read_json()
                return self._send_json(test_backend(str(body.get("backend") or "auto")))
            if path == "/api/test-generate":
                body = self._read_json()
                result = test_generate(str(body.get("prompt") or "a cute astronaut dog"), str(body.get("backend") or "auto"))
                return self._send_json(result)
        except image_gen.GenError as exc:
            return self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"ok": False, "error": str(exc)}, 400)
        return self._send_json({"error": "not found"}, 404)


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> int:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"DeepSeek ImageGen 设置页面已启动：http://{host}:{port}")
    print("按 Ctrl+C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="DeepSeek ImageGen 设置页面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    sys.exit(serve(args.host, args.port))
