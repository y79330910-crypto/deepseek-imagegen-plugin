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
  - 测试后端连通性、页面内试生成并预览图片（支持文生图 / 图生图）
  - 一键运行 doctor 诊断，结果以卡片形式展示
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
PLUGIN_ROOT = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PLUGIN_ROOT, "assets")

HTML_PAGE = r"""<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek ImageGen 设置</title>
<style>
  :root {
    --bg0:#090b13; --bg1:#0d1020; --card:#131729; --card2:#181d33; --line:#262c47;
    --fg:#e9ecf8; --dim:#9aa4c7; --dim2:#68719a;
    --acc:#38bdf8; --acc2:#22d3ee; --acc-dim:rgba(56,189,248,.16);
    --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
    --radius:14px;
  }
  * { box-sizing:border-box; }
  html { scroll-behavior:smooth; }
  body {
    margin:0; color:var(--fg);
    font-family:"Segoe UI","Microsoft YaHei",system-ui,-apple-system,sans-serif;
    font-size:13.5px; line-height:1.55;
    background:
      radial-gradient(1000px 420px at 85% -10%, rgba(56,189,248,.14), transparent 60%),
      radial-gradient(800px 380px at -10% 20%, rgba(34,211,238,.10), transparent 55%),
      linear-gradient(165deg, var(--bg0), var(--bg1) 55%, #0b0e1a);
    min-height:100vh;
  }
  ::selection { background:rgba(56,189,248,.35); }

  .topbar {
    position:sticky; top:0; z-index:50;
    display:flex; align-items:center; gap:12px;
    padding:10px 22px;
    background:rgba(10,12,22,.78);
    backdrop-filter:blur(14px);
    border-bottom:1px solid rgba(38,44,71,.65);
  }
  .brand { display:flex; align-items:center; gap:10px; min-width:0; }
  .brand-mark {
    width:32px; height:32px; border-radius:9px; flex:0 0 auto;
    display:flex; align-items:center; justify-content:center;
    background:linear-gradient(135deg,#0ea5e9,#22d3ee);
    box-shadow:0 4px 16px rgba(56,189,248,.45);
    color:#fff; font-size:16px;
  }
  .brand-mark img { width:100%; height:100%; border-radius:9px; object-fit:cover; }
  .brand-name { font-size:15px; font-weight:650; letter-spacing:.2px; }
  .brand-sub { font-size:11px; color:var(--dim); margin-top:1px; }
  .topbar-right { margin-left:auto; display:flex; align-items:center; gap:10px; min-width:0; }
  .cfg-path {
    font-size:11.5px; color:var(--dim2); white-space:nowrap; overflow:hidden;
    text-overflow:ellipsis; max-width:46vw; direction:rtl; text-align:left;
  }
  .chip {
    display:inline-flex; align-items:center; gap:6px; flex:0 0 auto;
    padding:4px 11px; border-radius:999px; font-size:11.5px;
    background:var(--card2); border:1px solid var(--line); color:var(--dim);
  }
  .chip .dot { width:7px; height:7px; border-radius:50%; background:var(--dim2); }
  .chip.ok { color:var(--ok); border-color:rgba(52,211,153,.35); }
  .chip.ok .dot { background:var(--ok); box-shadow:0 0 8px rgba(52,211,153,.8); }
  .chip.bad { color:var(--bad); border-color:rgba(248,113,113,.35); }
  .chip.bad .dot { background:var(--bad); box-shadow:0 0 8px rgba(248,113,113,.8); }
  .chip.warn { color:var(--warn); border-color:rgba(251,191,36,.35); }
  .chip.warn .dot { background:var(--warn); }
  .chip.purple { color:#a5f3fc; border-color:rgba(103,232,249,.35); }
  .chip.purple .dot { background:#67e8f9; }

  .wrap { max-width:1060px; margin:0 auto; padding:22px 22px 70px; }

  .hero {
    position:relative; overflow:hidden; border-radius:18px;
    border:1px solid var(--line); min-height:168px;
    display:flex; align-items:flex-end;
    background:url('/assets/banner.jpg') center/cover no-repeat;
  }
  .hero::after {
    content:""; position:absolute; inset:0;
    background:
      linear-gradient(90deg, rgba(9,11,19,.92) 0%, rgba(9,11,19,.72) 42%, rgba(9,11,19,.18) 100%),
      linear-gradient(0deg, rgba(9,11,19,.55), transparent 60%);
  }
  .hero-inner { position:relative; z-index:2; padding:28px 30px; max-width:72%; }
  .hero h1 { margin:0 0 6px; font-size:26px; font-weight:700; letter-spacing:.3px; }
  .hero p { margin:0 0 12px; color:var(--dim); font-size:13px; max-width:560px; }
  .hero .chips { display:flex; gap:8px; flex-wrap:wrap; }

  .nav {
    display:flex; gap:8px; flex-wrap:wrap; margin:18px 0 16px;
    position:sticky; top:53px; z-index:40; padding:6px;
    background:rgba(13,16,32,.72); border:1px solid var(--line);
    border-radius:12px; backdrop-filter:blur(12px);
  }
  .nav a {
    text-decoration:none; color:var(--dim); font-size:12.5px;
    padding:7px 14px; border-radius:9px; transition:.15s;
  }
  .nav a:hover { color:var(--fg); background:rgba(56,189,248,.10); }

  .card {
    background:linear-gradient(180deg, rgba(23,27,46,.86), rgba(19,23,41,.90));
    border:1px solid var(--line); border-radius:var(--radius);
    margin-bottom:16px; overflow:hidden;
  }
  .card-head {
    display:flex; align-items:center; gap:12px;
    padding:15px 20px 0;
  }
  .step {
    flex:0 0 auto; width:26px; height:26px; border-radius:8px;
    display:flex; align-items:center; justify-content:center;
    background:var(--acc-dim); color:#a5f3fc; border:1px solid rgba(103,232,249,.35);
    font-size:13px; font-weight:700;
  }
  .card-head h2 { margin:0; font-size:15.5px; font-weight:650; }
  .card-head .desc { margin:1px 0 0; color:var(--dim2); font-size:11.5px; }
  .card-body { padding:14px 20px 18px; }

  label { display:block; font-size:11.5px; color:var(--dim); margin:0 0 5px; }
  input, select, textarea {
    width:100%; background:#0c0f1d; border:1px solid var(--line);
    color:var(--fg); border-radius:9px; padding:8px 11px; font-size:13px;
    font-family:inherit; transition:border-color .15s, box-shadow .15s;
  }
  input:focus, select:focus, textarea:focus {
    outline:none; border-color:var(--acc);
    box-shadow:0 0 0 3px rgba(56,189,248,.18);
  }
  input::placeholder { color:#4c5478; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(190px, 1fr)); gap:12px; }
  .grid3 { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:12px; }

  .backend-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:12px; }
  .backend-card {
    border:1px solid var(--line); border-radius:12px; padding:12px 14px;
    background:rgba(12,15,29,.5);
  }
  .b-head { display:flex; align-items:center; gap:9px; margin-bottom:10px; }
  .b-icon {
    width:30px; height:30px; border-radius:8px; flex:0 0 auto;
    display:flex; align-items:center; justify-content:center; font-size:15px;
    background:var(--acc-dim); border:1px solid rgba(103,232,249,.3);
  }
  .b-head b { font-size:13.5px; }
  .b-head .tag { margin-left:auto; }

  .row2 { display:grid; grid-template-columns:3fr 1fr; gap:10px; align-items:end; }
  .actions { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  button {
    font-family:inherit; cursor:pointer; color:var(--fg);
    background:var(--card2); border:1px solid var(--line);
    border-radius:9px; padding:8px 15px; font-size:13px; transition:.15s;
  }
  button:hover { border-color:var(--acc); background:#1d2340; }
  .btn-primary {
    background:linear-gradient(135deg,#0ea5e9,#22d3ee); border:none; color:#04121a;
    box-shadow:0 4px 16px rgba(56,189,248,.35);
  }
  .btn-primary:hover { filter:brightness(1.1); background:linear-gradient(135deg,#0ea5e9,#22d3ee); }
  .btn-danger { background:#3a1a24; border-color:#5b2534; color:#fda4af; }
  .btn-danger:hover { border-color:#f87171; background:#461f2c; }
  .btn-sm { padding:6px 11px; font-size:12px; border-radius:8px; }

  .test-area {
    display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px;
    align-items:end;
  }
  .test-area .wide { grid-column:1 / -1; }
  #preview {
    margin-top:16px; display:none;
    border:1px solid var(--line); border-radius:12px; overflow:hidden;
    background:#0c0f1d;
  }
  #preview.show { display:block; }
  #preview img { display:block; width:100%; max-height:460px; object-fit:contain; background:#0a0c16; }
  .preview-meta {
    display:flex; gap:8px; flex-wrap:wrap; align-items:center;
    padding:10px 14px; border-top:1px solid var(--line); font-size:12px; color:var(--dim);
  }
  .preview-meta code { color:#a5f3fc; font-size:11.5px; word-break:break-all; }
  #errorBox {
    margin-top:14px; display:none; padding:12px 14px;
    border:1px solid rgba(248,113,113,.35); border-radius:10px;
    background:rgba(248,113,113,.08); color:#fca5a5; font-size:12.5px; white-space:pre-wrap;
  }
  #errorBox.show { display:block; }

  .check-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(230px, 1fr)); gap:10px; }
  .check-card {
    border:1px solid var(--line); border-radius:11px; padding:12px 14px;
    background:rgba(12,15,29,.5); display:flex; flex-direction:column; gap:8px;
  }
  .check-card .row { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .check-card .name { font-weight:600; font-size:13px; }
  .check-card .msg { color:var(--dim); font-size:12px; word-break:break-all; }

  .hint { color:var(--dim2); font-size:11.5px; margin:8px 0 0; }
  .hr { border:none; border-top:1px dashed var(--line); margin:16px 0; }

  #toasts { position:fixed; top:16px; right:16px; z-index:100; display:flex; flex-direction:column; gap:8px; max-width:min(380px, 90vw); }
  .toast {
    padding:11px 14px; border-radius:11px; font-size:12.5px; line-height:1.5;
    background:rgba(24,29,51,.96); border:1px solid var(--line); border-left:3px solid var(--acc);
    box-shadow:0 10px 30px rgba(0,0,0,.45); white-space:pre-wrap; word-break:break-word;
    animation:toastIn .2s ease;
  }
  .toast.ok { border-left-color:var(--ok); }
  .toast.bad { border-left-color:var(--bad); }
  @keyframes toastIn { from { opacity:0; transform:translateX(12px); } to { opacity:1; transform:none; } }

  @media (max-width:760px) {
    .hero-inner { max-width:100%; padding:22px; }
    .hero h1 { font-size:21px; }
    .cfg-path { display:none; }
    .row2 { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<div class="topbar">
  <div class="brand">
    <div class="brand-mark"><img src="/assets/avatar.png" alt="洛天依"></div>
    <div>
      <div class="brand-name">DeepSeek ImageGen</div>
      <div class="brand-sub">图像生成桥接 · 本地设置</div>
    </div>
  </div>
  <div class="topbar-right">
    <span class="chip purple"><span class="dot"></span>洛天依主题 · 仅本机访问</span>
    <span id="saveChip" class="chip"><span class="dot"></span>加载中…</span>
    <span class="cfg-path" id="cfgPath">配置加载中…</span>
  </div>
</div>

<div class="wrap">
  <div class="hero">
    <div class="hero-inner">
      <h1>🎨 图像生成工作台</h1>
      <p>配置后端点一下就能出图：文生图、图生图、模型选择、连通诊断都在这里。</p>
      <div class="chips">
        <span class="chip">vertex 本地代理</span>
        <span class="chip">5 种后端</span>
        <span class="chip">洛天依主题 ✦</span>
      </div>
    </div>
  </div>

  <nav class="nav">
    <a href="#s-import">一键导入</a>
    <a href="#s-global">全局设置</a>
    <a href="#s-backends">后端参数</a>
    <a href="#s-test">试生成</a>
    <a href="#s-doctor">诊断</a>
  </nav>

  <section class="card" id="s-import">
    <div class="card-head">
      <span class="step">1</span>
      <div><h2>一键导入 Vertex Proxy</h2><p class="desc">自动读取代理的端口、密钥与模型列表，选中最佳图像模型</p></div>
    </div>
    <div class="card-body">
      <div class="row2">
        <input id="vpDir" placeholder="Vertex Proxy 目录（含 config\config.json、api_keys.txt、models.json）">
        <button class="btn-primary" onclick="loadVertex()">读取并导入</button>
      </div>
      <div id="vpResult"></div>
      <p class="hint">留空使用默认目录：C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist</p>
    </div>
  </section>

  <section class="card" id="s-global">
    <div class="card-head">
      <span class="step">2</span>
      <div><h2>默认后端与全局设置</h2><p class="desc">不指定后端时默认使用这里的选择</p></div>
    </div>
    <div class="card-body">
      <div class="grid">
        <div>
          <label>默认后端</label>
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
    </div>
  </section>

  <section class="card" id="s-backends">
    <div class="card-head">
      <span class="step">3</span>
      <div><h2>后端参数</h2><p class="desc">每个后端独立配置，密钥只保存在本机</p></div>
    </div>
    <div class="card-body">
      <div class="backend-grid">
        <div class="backend-card">
          <div class="b-head"><div class="b-icon">🟣</div><b>Vertex Proxy</b><span class="chip purple"><span class="dot"></span>默认</span></div>
          <div class="grid3">
            <div style="grid-column:1/-1"><label>代理目录</label><input id="vertex_dir" placeholder="代理目录"></div>
            <div style="grid-column:1/-1"><label>API 地址（留空=按端口自动）</label><input id="vertex_base" placeholder="http://127.0.0.1:2156/v1"></div>
            <div style="grid-column:1/-1"><label>API Key（留空=自动读取）</label><input id="vertex_key" type="password" placeholder="sk-..."></div>
            <div style="grid-column:1/-1"><label>图像模型（留空=自动最佳）</label><input id="vertex_model" placeholder="gemini-3-pro-image"></div>
          </div>
        </div>
        <div class="backend-card">
          <div class="b-head"><div class="b-icon">🌐</div><b>Pollinations</b><span class="chip"><span class="dot"></span>免费免密钥</span></div>
          <div class="grid3">
            <div style="grid-column:1/-1"><label>模型（留空=默认）</label><input id="pollinations_model" placeholder="flux"></div>
          </div>
        </div>
        <div class="backend-card">
          <div class="b-head"><div class="b-icon">⚡</div><b>SiliconFlow</b><span class="chip"><span class="dot"></span>FLUX</span></div>
          <div class="grid3">
            <div style="grid-column:1/-1"><label>API 地址</label><input id="sf_base" placeholder="https://api.siliconflow.cn/v1"></div>
            <div style="grid-column:1/-1"><label>API Key</label><input id="sf_key" type="password" placeholder="sk-..."></div>
            <div style="grid-column:1/-1"><label>模型</label><input id="sf_model" placeholder="black-forest-labs/FLUX.1-schnell"></div>
          </div>
        </div>
        <div class="backend-card">
          <div class="b-head"><div class="b-icon">🖥</div><b>SD WebUI / Forge</b><span class="chip"><span class="dot"></span>本地</span></div>
          <div class="grid3">
            <div style="grid-column:1/-1"><label>地址</label><input id="sd_base" placeholder="http://127.0.0.1:7860"></div>
            <div><label>采样器</label><input id="sd_sampler" placeholder="Euler a"></div>
            <div><label>步数</label><input id="sd_steps" type="number" placeholder="28"></div>
            <div><label>CFG</label><input id="sd_cfg" type="number" step="0.5" placeholder="7"></div>
            <div style="grid-column:1/-1"><label>图生图去噪强度</label><input id="sd_denoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
          </div>
        </div>
        <div class="backend-card">
          <div class="b-head"><div class="b-icon">🔗</div><b>ComfyUI</b><span class="chip"><span class="dot"></span>本地</span></div>
          <div class="grid3">
            <div style="grid-column:1/-1"><label>地址</label><input id="cf_base" placeholder="http://127.0.0.1:8188"></div>
            <div style="grid-column:1/-1"><label>Checkpoint（留空=自动）</label><input id="cf_checkpoint" placeholder="sd_xl_base_1.0.safetensors"></div>
            <div><label>步数</label><input id="cf_steps" type="number" placeholder="28"></div>
            <div><label>CFG</label><input id="cf_cfg" type="number" step="0.5" placeholder="7"></div>
            <div style="grid-column:1/-1"><label>图生图去噪强度</label><input id="cf_denoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
          </div>
        </div>
      </div>
    </div>
  </section>

  <section class="card" id="s-test">
    <div class="card-head">
      <span class="step">4</span>
      <div><h2>试生成</h2><p class="desc">不保存配置也能先试试效果；图片预览不会离开你的电脑</p></div>
    </div>
    <div class="card-body">
      <div class="test-area">
        <div class="wide"><label>提示词</label><input id="testPrompt" placeholder="一只戴宇航员头盔的柴犬，写实风格"></div>
        <div><label>后端</label><select id="testBackend"></select></div>
        <div><label>尺寸</label><input id="testSize" placeholder="1024x1024（图生图可留空）"></div>
        <div><label>参考图（图生图，可选）</label><input id="testImage" placeholder="图片路径或 http(s) 链接"></div>
        <div><label>去噪强度（图生图）</label><input id="testDenoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
        <div><label>种子（留空=随机）</label><input id="testSeed" type="number" placeholder="例如 42"></div>
      </div>
      <div class="actions" style="margin-top:14px">
        <button class="btn-primary" id="genBtn" onclick="testGenerate()">✨ 开始生成</button>
        <button onclick="testBackend()">🔌 测试后端连通性</button>
      </div>
      <div id="errorBox"></div>
      <div id="preview">
        <img id="previewImg" alt="生成结果预览">
        <div class="preview-meta" id="previewMeta"></div>
      </div>
    </div>
  </section>

  <section class="card" id="s-doctor">
    <div class="card-head">
      <span class="step">5</span>
      <div><h2>诊断</h2><p class="desc">检查各后端连通性、配置是否完整</p></div>
    </div>
    <div class="card-body">
      <div class="actions">
        <button onclick="runDoctor()">🔍 运行诊断（doctor）</button>
      </div>
      <div id="doctorResult" style="margin-top:14px"></div>
    </div>
  </section>

  <section class="card">
    <div class="card-body" style="display:flex; justify-content:flex-end">
      <button class="btn-primary" onclick="save()">💾 保存配置</button>
    </div>
  </section>
</div>

<div id="toasts"></div>

<script>
let state = { config: null, busy: false };
const $ = id => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch (e) { j = {}; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

function toast(text, type) {
  const box = $("toasts");
  const t = document.createElement("div");
  t.className = "toast " + (type || "");
  t.textContent = text;
  box.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; t.style.transition = "opacity .3s"; setTimeout(() => t.remove(), 320); }, 4200);
}

function setSaveChip(text, cls) {
  const chip = $("saveChip");
  chip.className = "chip " + (cls || "");
  chip.innerHTML = '<span class="dot"></span>' + esc(text);
}

async function loadConfig() {
  const j = await api("/api/config");
  state.config = j.config;
  $("cfgPath").textContent = j.path;
  render();
  setSaveChip("配置已加载", "ok");
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
  $("sd_denoise").value = c.sd_webui && c.sd_webui.denoising_strength != null ? c.sd_webui.denoising_strength : 0.6;
  $("cf_base").value = c.comfyui && c.comfyui.base_url || "";
  $("cf_checkpoint").value = c.comfyui && c.comfyui.checkpoint || "";
  $("cf_steps").value = c.comfyui && c.comfyui.steps || "";
  $("cf_cfg").value = c.comfyui && c.comfyui.cfg || "";
  $("cf_denoise").value = c.comfyui && c.comfyui.denoise != null ? c.comfyui.denoise : 0.6;
  fillTestBackend();
}

function fillTestBackend() {
  const sel = $("testBackend");
  const cur = state.config.default_backend || "vertex";
  [["vertex","vertex（本地代理）"],["pollinations","pollinations（免费）"],["siliconflow","siliconflow（FLUX）"],["sd-webui","sd-webui（本地）"],["comfyui","comfyui（本地）"]].forEach(([v,label]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = label;
    if (v === cur) o.selected = true;
    sel.appendChild(o);
  });
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
    denoising_strength: parseFloat($("sd_denoise").value) || 0.6,
  });
  c.comfyui = Object.assign({}, c.comfyui, {
    base_url: $("cf_base").value.trim(),
    checkpoint: $("cf_checkpoint").value.trim(),
    steps: parseInt($("cf_steps").value) || 28,
    cfg: parseFloat($("cf_cfg").value) || 7,
    denoise: parseFloat($("cf_denoise").value) || 0.6,
  });
  return c;
}

async function save() {
  const r = await api("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collect()) });
  state.config = collect();
  setSaveChip("已保存", "ok");
  toast("✅ " + r.message, "ok");
}

async function loadVertex() {
  const dir = $("vpDir").value.trim() || undefined;
  const r = await api("/api/vertex" + (dir ? "?dir=" + encodeURIComponent(dir) : ""));
  if (!r.found) { toast("未找到 Vertex Proxy 配置：" + r.reason, "bad"); return; }
  const keys = r.keys.map(k => `<option value="${esc(k)}">${esc(k.slice(0,10) + "…" + k.slice(-4))}</option>`).join("");
  const models = r.models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
  $("vpResult").innerHTML =
    `<div class="row2" style="margin-top:12px">` +
    `<div><label>API 地址</label><input id="vpBase" value="${esc(r.base_url)}"></div>` +
    `<div><label>图像模型（已自动选最佳）</label><select id="vpModel">${models}</select></div>` +
    `<div><label>API Key</label><select id="vpKey">${keys}</select></div>` +
    `<button class="btn-primary" onclick="applyVertex()">应用到后端参数</button></div>` +
    `<p class="hint">端口 ${r.port}，共 ${r.models.length} 个模型，图像模型 ${r.image_models.length} 个，最佳：<b style="color:#c4b5fd">${esc(r.best_model || "")}</b></p>`;
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
  toast("已应用到后端参数（地址留空=按端口自动）。记得点「保存配置」。", "ok");
}

async function testBackend() {
  const backend = $("default_backend").value;
  const r = await api("/api/test", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backend }) });
  toast("测试 " + backend + "：" + r.message, r.ok ? "ok" : "bad");
}

async function testGenerate() {
  if (state.busy) return;
  state.busy = true;
  $("genBtn").disabled = true;
  $("genBtn").textContent = "⏳ 生成中…";
  $("errorBox").className = "";
  $("preview").className = "";
  const prompt = $("testPrompt").value.trim() || "a cute astronaut dog";
  const body = {
    prompt,
    backend: $("testBackend").value,
    size: $("testSize").value.trim() || "1024x1024",
    image: $("testImage").value.trim(),
    denoise: $("testDenoise").value.trim() ? parseFloat($("testDenoise").value) : undefined,
    seed: $("testSeed").value.trim() ? parseInt($("testSeed").value) : undefined,
  };
  try {
    const r = await api("/api/test-generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const img = $("previewImg");
    img.src = "/api/image?path=" + encodeURIComponent(r.path);
    img.onerror = () => { img.style.display = "none"; };
    $("preview").className = "show";
    let meta = `后端 ${esc(r.backend)} · 尺寸 ${esc(r.size)} · 种子 ${r.seed}`;
    if (r.init_image) meta += ` · 图生图`;
    if (r.denoise != null) meta += ` · 去噪 ${r.denoise}`;
    $("previewMeta").innerHTML = `<span>${meta}</span><code>${esc(r.path)}</code>`;
    toast("✅ 生成成功", "ok");
  } catch (e) {
    const box = $("errorBox");
    box.textContent = "❌ 生成失败：" + e.message;
    box.className = "show";
  } finally {
    state.busy = false;
    $("genBtn").disabled = false;
    $("genBtn").textContent = "✨ 开始生成";
  }
}

async function runDoctor() {
  const r = await api("/api/doctor");
  const box = $("doctorResult");
  if (!r.checks || !r.checks.length) { box.innerHTML = `<p class="hint">${esc(r.error || "无诊断结果")}</p>`; return; }
  const cards = r.checks.map(c =>
    `<div class="check-card">
      <div class="row">
        <span class="name">${esc(c.backend)}</span>
        <span class="chip ${c.ok ? "ok" : "bad"}"><span class="dot"></span>${c.ok ? "正常" : "失败"}</span>
      </div>
      <div class="msg">${esc(c.message)}</div>
      ${c.best_model ? `<div class="msg">最佳模型：<b style="color:#c4b5fd">${esc(c.best_model)}</b></div>` : ""}
    </div>`
  ).join("");
  box.innerHTML = `<div class="check-grid">${cards}</div>` +
    `<p class="hint" style="margin-top:12px">默认后端：${esc(r.default_backend)} · 配置文件：${esc(r.config_file)}（${r.config_exists ? "存在" : "不存在，使用默认配置"}）</p>`;
}

loadConfig().catch(e => { setSaveChip("加载失败", "bad"); toast("加载失败：" + e.message, "bad"); });
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


def test_generate(
    prompt: str, backend: str, size: str = "1024x1024", image: str = "", denoise: float | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    preview_dir = os.path.join(tempfile.gettempdir(), "deepseek-imagegen-preview")
    return image_gen.generate_image(
        prompt,
        backend=backend or "auto",
        size=size or "1024x1024",
        out=preview_dir,
        init_image=image or None,
        denoise=denoise,
        seed=seed,
    )


def serve_preview_image(path: str) -> bytes | None:
    """仅允许读取预览目录内的图片（试生成结果），防止任意文件读取。"""
    try:
        target = os.path.realpath(path)
        preview_dir = os.path.realpath(os.path.join(tempfile.gettempdir(), "deepseek-imagegen-preview"))
        if target != preview_dir and not target.startswith(preview_dir + os.sep):
            return None
        if not os.path.isfile(target):
            return None
        with open(target, "rb") as handle:
            return handle.read()
    except Exception:  # noqa: BLE001
        return None


def serve_asset(name: str) -> bytes | None:
    allowed = {"banner.jpg", "icon.png", "avatar.png"}
    if name not in allowed:
        return None
    target = os.path.join(ASSETS_DIR, name)
    if not os.path.isfile(target):
        return None
    with open(target, "rb") as handle:
        return handle.read()


def run_doctor_json() -> dict[str, Any]:
    try:
        report = image_gen.cmd_doctor(type("A", (), {"json": True})())
        return {"ok": report.get("ok", False), **report}
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

    def _send_bytes(self, body: bytes, content_type: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
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
        if path == "/api/image":
            query = urllib.parse.parse_qs(self.path.split("?")[1]) if "?" in self.path else {}
            body = serve_preview_image(query.get("path", [""])[0])
            if body is None:
                return self._send_json({"error": "图片不存在或路径不允许"}, 404)
            return self._send_bytes(body, "image/png")
        if path.startswith("/assets/"):
            body = serve_asset(os.path.basename(path))
            if body is None:
                return self._send_json({"error": "not found"}, 404)
            ctype = "image/jpeg" if path.endswith(".jpg") else "image/png"
            return self._send_bytes(body, ctype)
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
                result = test_generate(
                    str(body.get("prompt") or "a cute astronaut dog"),
                    str(body.get("backend") or "auto"),
                    size=str(body.get("size") or ""),
                    image=str(body.get("image") or ""),
                    denoise=body.get("denoise"),
                    seed=body.get("seed"),
                )
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
