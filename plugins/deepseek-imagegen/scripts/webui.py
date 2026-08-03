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
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DeepSeek ImageGen · 设置</title>
<style>
  :root {
    --cyan:#38bdf8; --cyan-deep:#22d3ee; --cyan-soft:rgba(56,189,248,.18);
    --teal:#2dd4bf; --ok:#34d399; --warn:#fbbf24; --bad:#f87171;
    --text:#eef4fb; --text-dim:#b8cfe3;
    --glass:rgba(14,22,36,.82); --stroke:rgba(160,205,240,.16); --hi:rgba(255,255,255,.10);
    --shadow:0 18px 46px rgba(4,12,24,.45);
    --radius:18px;
    --ease:cubic-bezier(.22,.8,.3,1);
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  html, body { height:100%; }
  body {
    font:14px/1.55 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
    color:var(--text); overflow:hidden;
  }
  ::selection { background:rgba(56,189,248,.3); }
  a { color:var(--cyan-deep); text-decoration:none; }
  button { font:inherit; cursor:pointer; color:inherit; }
  input, select, textarea {
    font:inherit; color:var(--text); background:rgba(8,14,26,.55);
    border:1px solid var(--stroke); border-radius:11px; padding:10px 13px; width:100%;
    transition:border-color .2s var(--ease), background .2s var(--ease), box-shadow .2s;
  }
  input::placeholder, textarea::placeholder { color:rgba(186,205,224,.62); }
  input:focus, select:focus, textarea:focus {
    outline:none; border-color:var(--cyan); background:rgba(10,17,31,.78);
    box-shadow:0 0 0 3px var(--cyan-soft);
  }
  select {
    -webkit-appearance:none; appearance:none; cursor:pointer; padding-right:36px;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%230ea5e9' stroke-width='1.6' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
    background-repeat:no-repeat; background-position:right 14px center;
  }
  option { background:#101a2c; color:var(--text); }
  .hidden { display:none !important; }
  .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0; }

  .bg { position:fixed; inset:-4%; z-index:-2; background:url('/assets/background.jpg') center/cover no-repeat; }
  .bg-veil {
    position:fixed; inset:0; z-index:-1; pointer-events:none;
    background:
      radial-gradient(1100px 700px at 85% -8%, rgba(56,189,248,.10), transparent 55%),
      linear-gradient(90deg, rgba(10,18,32,.30), rgba(10,18,32,.14) 45%, rgba(10,18,32,.04)),
      linear-gradient(180deg, rgba(10,18,32,.16), rgba(10,18,32,.30));
  }

  .glass {
    background:var(--glass);
    -webkit-backdrop-filter:blur(22px); backdrop-filter:blur(22px);
    border:1px solid var(--stroke); border-radius:var(--radius);
    box-shadow:var(--shadow), inset 0 1px 0 var(--hi);
  }

  .btn {
    background:linear-gradient(180deg, var(--cyan), var(--cyan-deep)); color:#fff; border:none;
    border-radius:11px; padding:10px 18px; font-weight:600; letter-spacing:.01em;
    box-shadow:0 6px 18px rgba(14,165,233,.28);
    transition:transform .18s var(--ease), filter .18s, box-shadow .18s;
  }
  .btn:hover { filter:brightness(1.06); transform:translateY(-1.5px); box-shadow:0 9px 24px rgba(14,165,233,.36); }
  .btn:active { transform:translateY(0) scale(.99); }
  .btn.ghost { background:rgba(255,255,255,.07); color:var(--text-dim); box-shadow:none; border:1px solid var(--stroke); }
  .btn.ghost:hover { color:var(--text); border-color:var(--cyan); background:rgba(255,255,255,.14); transform:translateY(-1.5px); }
  .btn.danger { background:transparent; color:var(--bad); border:1px solid rgba(225,29,72,.3); box-shadow:none; padding:6px 13px; }
  .btn.danger:hover { background:rgba(225,29,72,.08); transform:none; }
  .btn-sm { padding:6px 12px; font-size:12.5px; border-radius:9px; }

  #app {
    position:fixed; inset:0; display:grid; grid-template-columns:236px 1fr;
    padding:18px; gap:18px;
  }
  aside { padding:24px 16px; display:flex; flex-direction:column; gap:4px; }
  .brand { display:flex; align-items:center; gap:11px; padding:4px 6px 22px; }
  .brand .mk {
    width:40px; height:40px; border-radius:12px; overflow:hidden; flex:none;
    box-shadow:0 6px 16px rgba(14,165,233,.35);
  }
  .brand .mk img { width:100%; height:100%; object-fit:cover; display:block; }
  .brand b { font-size:15.5px; letter-spacing:.01em; }
  .brand .sub { font-size:11px; color:var(--text-dim); margin-top:2px; }
  nav { display:flex; flex-direction:column; gap:3px; }
  nav button {
    display:flex; align-items:center; gap:12px; width:100%; text-align:left;
    background:transparent; border:none; color:var(--text-dim);
    padding:11px 14px; border-radius:12px; font-size:14px;
    transition:background .2s var(--ease), color .2s, transform .2s var(--ease), box-shadow .2s;
  }
  nav button .ic { font-size:16px; width:20px; text-align:center; }
  nav button:hover { background:rgba(255,255,255,.09); color:var(--text); transform:translateX(3px); }
  nav button.active { background:var(--cyan-soft); color:var(--cyan-deep); font-weight:600; box-shadow:inset 0 0 0 1px rgba(14,165,233,.28), 0 4px 14px rgba(14,165,233,.14); }
  aside .spacer { flex:1; }
  .side-foot { padding:12px 8px 2px; border-top:1px dashed var(--stroke); }
  .side-foot .cfg { font-size:11px; color:var(--text-dim); line-height:1.5; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .side-foot .chip {
    display:inline-flex; align-items:center; gap:6px; margin-top:8px;
    padding:4px 10px; border-radius:999px; font-size:11px;
    background:rgba(56,189,248,.14); color:#7dd3fc; border:1px solid rgba(56,189,248,.35);
  }

  main { position:relative; overflow:hidden; border-radius:var(--radius); }
  .main-glass-bg {
    position:absolute; inset:-1px; z-index:0; pointer-events:none;
    background:rgba(14,22,36,.78); border:1px solid var(--stroke); border-radius:var(--radius);
    box-shadow:var(--shadow), inset 0 1px 0 var(--hi);
  }
  .main-content { position:relative; z-index:1; overflow:auto; padding:26px 30px 96px; height:100%; }
  .main-content::-webkit-scrollbar { width:9px; }
  .main-content::-webkit-scrollbar-thumb { background:rgba(160,205,240,.24); border-radius:9px; }
  .page-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:22px; flex-wrap:wrap; }
  .page-head h2 { font-size:23px; font-weight:700; letter-spacing:.01em; color:#f2f8ff; }
  .page-head .hint { color:var(--text-dim); font-size:12.5px; margin-top:4px; }
  section { transition:opacity .16s var(--ease), transform .16s var(--ease); }
  section.leaving { opacity:0; }
  section.entering { opacity:0; transform:translateY(12px); }

  .grid { display:grid; gap:14px; }
  .grid-2 { grid-template-columns:1fr 1fr; }
  .backend-grid { grid-template-columns:repeat(auto-fill, minmax(300px, 1fr)); }
  .card { padding:18px 20px; transition:transform .22s var(--ease), box-shadow .22s, border-color .22s; }
  .card h3 { font-size:14px; margin-bottom:2px; display:flex; align-items:center; gap:8px; }
  .card h3 .b-icon {
    width:26px; height:26px; border-radius:8px; display:inline-flex; align-items:center; justify-content:center;
    background:var(--cyan-soft); color:var(--cyan-deep); font-size:14px; flex:none;
  }
  .card .desc { color:var(--text-dim); font-size:11.5px; margin-bottom:12px; }

  .field { margin-bottom:14px; }
  .field label { display:block; color:var(--text-dim); font-size:12.5px; margin-bottom:6px; }
  .field .hint { color:var(--text-dim); font-size:11px; margin-top:5px; }
  .toolbar { display:flex; gap:10px; margin-bottom:14px; align-items:flex-end; }
  .toolbar input, .toolbar select { flex:1; }
  .toolbar .btn { flex:none; }

  .divider { border-top:1px dashed var(--stroke); margin:16px 0; }
  .stat-cards { display:grid; gap:14px; grid-template-columns:repeat(auto-fill, minmax(190px,1fr)); }
  .stat { padding:16px 18px; }
  .stat .label { color:var(--text-dim); font-size:12px; letter-spacing:.03em; }
  .stat .value { font-size:26px; font-weight:700; margin-top:6px; line-height:1.1; color:var(--cyan-deep); }
  .stat .sub { color:var(--text-dim); font-size:11.5px; margin-top:5px; }

  .chip {
    display:inline-flex; align-items:center; gap:6px;
    padding:4px 11px; border-radius:999px; font-size:11.5px; flex:none;
    background:rgba(10,16,28,.55); border:1px solid var(--stroke); color:var(--text-dim);
  }
  .chip .dot { width:7px; height:7px; border-radius:50%; background:var(--text-dim); }
  .chip.ok { color:var(--ok); border-color:rgba(22,163,74,.3); }
  .chip.ok .dot { background:var(--ok); }
  .chip.bad { color:var(--bad); border-color:rgba(225,29,72,.3); }
  .chip.bad .dot { background:var(--bad); }
  .chip.warn { color:var(--warn); border-color:rgba(217,119,6,.3); }
  .chip.warn .dot { background:var(--warn); }
  .chip.cyan { color:#7dd3fc; border-color:rgba(56,189,248,.4); background:rgba(56,189,248,.14); }
  .chip.cyan .dot { background:var(--cyan); }

  #preview { margin-top:16px; display:none; border:1px solid var(--stroke); border-radius:12px; overflow:hidden; background:#0c1524; }
  #preview.show { display:block; }
  #preview img { display:block; width:100%; max-height:430px; object-fit:contain; background:#0a1320; }
  .preview-meta { display:flex; gap:8px; flex-wrap:wrap; align-items:center; padding:10px 14px; border-top:1px solid var(--stroke); font-size:12px; color:var(--text-dim); }
  .preview-meta code { color:#7dd3fc; font-size:11.5px; word-break:break-all; }
  #errorBox { margin-top:14px; display:none; padding:12px 14px; border:1px solid rgba(248,113,113,.35); border-radius:10px; background:rgba(248,113,113,.10); color:#fda4af; font-size:12.5px; white-space:pre-wrap; }
  #errorBox.show { display:block; }

  .check-grid { display:grid; gap:10px; grid-template-columns:repeat(auto-fill, minmax(230px,1fr)); }
  .check-card { padding:13px 15px; display:flex; flex-direction:column; gap:8px; }
  .check-card .row { display:flex; align-items:center; justify-content:space-between; gap:8px; }
  .check-card .name { font-weight:600; font-size:13px; }
  .check-card .msg { color:var(--text-dim); font-size:12px; word-break:break-all; }

  .save-bar {
    position:fixed; right:34px; bottom:34px; z-index:40;
    display:flex; align-items:center; gap:10px;
    padding:10px 12px 10px 16px;
    background:rgba(16,24,38,.82); border:1px solid var(--stroke); border-radius:14px;
    box-shadow:0 14px 40px rgba(4,12,24,.45);
    -webkit-backdrop-filter:blur(12px); backdrop-filter:blur(12px);
  }

  .toast {
    position:fixed; bottom:28px; left:50%; transform:translateX(-50%) translateY(22px);
    padding:13px 24px; font-size:13px; opacity:0; pointer-events:none; z-index:60;
    transition:all .34s var(--ease); max-width:min(560px,92vw);
    background:rgba(16,24,38,.94); color:var(--text);
    border:1px solid var(--stroke); border-radius:13px;
    box-shadow:0 16px 44px rgba(4,12,24,.5); white-space:pre-wrap; text-align:center;
  }
  .toast.show { opacity:1; transform:translateX(-50%) translateY(0); }
  .toast.ok { border-left:4px solid var(--ok); }
  .toast.bad { border-left:4px solid var(--bad); }

  .menu-btn {
    display:none; position:fixed; top:14px; left:14px; z-index:99;
    width:40px; height:40px; border:1px solid var(--stroke);
    background:rgba(16,24,38,.8); border-radius:11px; align-items:center; justify-content:center;
    font-size:20px; color:var(--text);
    -webkit-backdrop-filter:blur(10px); backdrop-filter:blur(10px);
  }
  .menu-overlay { position:fixed; inset:0; z-index:90; background:rgba(20,50,70,.35); opacity:0; pointer-events:none; transition:opacity .3s var(--ease); }
  .menu-overlay.open { opacity:1; pointer-events:auto; }

  @media (max-width:768px) {
    .menu-btn { display:flex; }
    #app { grid-template-columns:1fr; padding:0; gap:0; }
    aside {
      position:fixed; top:0; left:0; bottom:0; z-index:100; width:260px; border-radius:0;
      transform:translateX(-100%); transition:transform .3s var(--ease); padding:20px 14px;
    }
    aside.open { transform:translateX(0); box-shadow:0 0 60px rgba(20,50,70,.4); }
    .main-content { padding:16px; padding-top:68px; }
    main { border-radius:0; }
    .page-head h2 { font-size:19px; }
    .card { padding:15px; }
    .grid-2 { grid-template-columns:1fr; }
    .save-bar { right:14px; bottom:14px; }
    .toolbar { flex-wrap:wrap; }
    .toolbar input { min-width:130px; }
  }
</style>
</head>
<body>

<div class="bg"></div>
<div class="bg-veil"></div>

<div id="app">
  <button class="menu-btn" id="menuBtn" onclick="toggleMenu()">☰</button>
  <aside class="glass" id="sideBar">
    <div class="brand">
      <div class="mk"><img src="/assets/avatar.png" alt="洛天依"></div>
      <div>
        <b>DeepSeek ImageGen</b>
        <div class="sub">图像生成桥接 · 洛天依主题</div>
      </div>
    </div>
    <nav id="sideNav">
      <button data-page="import" class="active"><span class="ic">📥</span> 一键导入</button>
      <button data-page="global"><span class="ic">⚙️</span> 全局设置</button>
      <button data-page="backends"><span class="ic">🧩</span> 后端参数</button>
      <button data-page="test"><span class="ic">✨</span> 试生成</button>
      <button data-page="doctor"><span class="ic">🔍</span> 诊断</button>
    </nav>
    <div class="spacer"></div>
    <div class="side-foot">
      <div class="cfg" id="cfgPath">配置加载中…</div>
      <span class="chip" id="saveChip"><span class="dot"></span>加载中…</span>
    </div>
  </aside>
  <div class="menu-overlay" id="menuOverlay" onclick="closeMenu()"></div>

  <main>
    <div class="main-glass-bg"></div>
    <div class="main-content">

      <section id="page-import">
        <div class="page-head">
          <div><h2>一键导入 Vertex Proxy</h2><div class="hint">自动读取代理的端口、密钥与模型列表，选中最佳图像模型</div></div>
        </div>
        <div class="card glass">
          <div class="toolbar">
            <input id="vpDir" placeholder="Vertex Proxy 目录（含 config\config.json、api_keys.txt、models.json）">
            <button class="btn" onclick="loadVertex()">读取并导入</button>
          </div>
          <div id="vpResult"></div>
          <div class="field"><div class="hint">留空使用默认目录：C:\Users\yjq\Documents\Codex\2026-07-31\new-chat\outputs\vertex-proxy\dist</div></div>
        </div>
      </section>

      <section id="page-global" class="hidden">
        <div class="page-head">
          <div><h2>全局设置</h2><div class="hint">不指定后端时默认使用这里的选择</div></div>
          <button class="btn ghost" onclick="save()">💾 保存</button>
        </div>
        <div class="card glass">
          <div class="grid grid-2">
            <div class="field">
              <label for="default_backend">默认后端</label>
              <select id="default_backend">
                <option value="vertex">vertex（本地代理，自动选最佳图像模型）</option>
                <option value="pollinations">pollinations（免费免密钥）</option>
                <option value="siliconflow">siliconflow（FLUX）</option>
                <option value="sd-webui">sd-webui（本地）</option>
                <option value="comfyui">comfyui（本地）</option>
              </select>
            </div>
            <div class="field"><label for="default_size">默认尺寸</label><input id="default_size" placeholder="1024x1024"></div>
            <div class="field"><label for="save_dir">默认保存目录（留空=当前目录）</label><input id="save_dir" placeholder="例如 D:\images"></div>
            <div class="field"><label for="mirror_dir">自动副本目录（留空=不复制）</label><input id="mirror_dir" placeholder="C:\Users\yjq\Pictures\codex"></div>
            <div class="field"><label for="default_negative">默认负面提示词</label><input id="default_negative" placeholder="文字, 水印, 低质量"></div>
          </div>
        </div>
        <div class="card glass" style="margin-top:14px">
          <div class="field">
            <div class="hint">提示词翻译官：把你的中文需求改写成图像模型更喜欢的结构化描述。默认 DeepSeek；
            若 DeepSeek 通道异常（例如返回问号），会自动改用本地 Gemini，出图不会中断。</div>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label for="tr_engine">翻译官引擎</label>
              <select id="tr_engine">
                <option value="deepseek">DeepSeek（默认）</option>
                <option value="gemini">Gemini（本地代理，稳定支持中文）</option>
                <option value="off">关闭（原文直传）</option>
              </select>
            </div>
            <div class="field">
              <label for="tr_lang">提示词输出语言</label>
              <select id="tr_lang">
                <option value="zh">中文描述</option>
                <option value="en">英文描述</option>
              </select>
            </div>
            <div class="field">
              <label for="tr_autofix">自动看图改图</label>
              <select id="tr_autofix">
                <option value="1">开启（推荐）</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div class="field"><label for="tr_maxfix">最多改图轮数</label><input id="tr_maxfix" type="number" min="0" max="3" placeholder="1"></div>
            <div class="field">
              <label for="tr_fixmode">自动改图方式</label>
              <select id="tr_fixmode">
                <option value="edit">局部小修（在原图上改，推荐）</option>
                <option value="redraw">整图重画（老方式）</option>
              </select>
            </div>
            <div class="field">
              <label for="tr_keepbest">保留最佳（改坏了自动退回）</label>
              <select id="tr_keepbest">
                <option value="1">开启（推荐）</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div class="field"><label for="tr_ds_base">DeepSeek 地址（留空=自动读 Codex 配置）</label><input id="tr_ds_base" placeholder="https://api.deepseek.com"></div>
            <div class="field"><label for="tr_ds_key">DeepSeek 密钥（留空=自动读 Codex 配置）</label><input id="tr_ds_key" type="password" placeholder="sk-..."></div>
            <div class="field"><label for="tr_ds_model">DeepSeek 模型</label><input id="tr_ds_model" placeholder="deepseek-v4-flash"></div>
            <div class="field"><label for="tr_gm_model">Gemini 翻译模型（留空=自动最佳）</label><input id="tr_gm_model" placeholder="gemini-3.6-flash"></div>
            <div class="field m-b-0"><label for="tr_vision">视觉检查脚本路径（留空=自动查找）</label><input id="tr_vision" placeholder="vision_bridge.py 的完整路径"></div>
          </div>
        </div>
        <div class="card glass">
          <div class="page-head">
            <div><h2>提示词词库（v0.6）</h2><div class="hint">把收集的提示词存进 MySQL，生成时用向量检索最相近的几条例子喂给翻译官，让第一版更稳。密钥只保存在本机</div></div>
          </div>
          <div class="grid grid-2">
            <div class="field">
              <label for="pl_enabled">启用词库</label>
              <select id="pl_enabled">
                <option value="1">开启</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div class="field">
              <label for="pl_feed">喂给翻译官</label>
              <select id="pl_feed">
                <option value="1">开启（推荐）</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div class="field"><label for="pl_topk">初选数量</label><input id="pl_topk" type="number" min="1" max="200" placeholder="50"></div>
            <div class="field"><label for="pl_finalk">最终参考条数</label><input id="pl_finalk" type="number" min="1" max="20" placeholder="8"></div>
            <div class="field"><label for="pl_emb_base">Embedding 地址</label><input id="pl_emb_base" placeholder="https://api.siliconflow.com/v1/embeddings"></div>
            <div class="field"><label for="pl_emb_key">Embedding 密钥</label><input id="pl_emb_key" type="password" placeholder="sk-..."></div>
            <div class="field"><label for="pl_emb_model">Embedding 模型</label><input id="pl_emb_model" placeholder="Qwen/Qwen3-Embedding-8B"></div>
            <div class="field">
              <label for="pl_rr">Rerank 精排</label>
              <select id="pl_rr">
                <option value="1">开启（推荐）</option>
                <option value="0">关闭</option>
              </select>
            </div>
            <div class="field"><label for="pl_rr_base">Rerank 地址</label><input id="pl_rr_base" placeholder="https://api.siliconflow.com/v1/rerank"></div>
            <div class="field"><label for="pl_rr_key">Rerank 密钥（留空=用 Embedding 密钥）</label><input id="pl_rr_key" type="password" placeholder="sk-..."></div>
            <div class="field"><label for="pl_rr_model">Rerank 模型</label><input id="pl_rr_model" placeholder="Qwen/Qwen3-Reranker-8B"></div>
            <div class="field"><label for="pl_mysql_host">MySQL 主机</label><input id="pl_mysql_host" placeholder="127.0.0.1"></div>
            <div class="field"><label for="pl_mysql_port">MySQL 端口</label><input id="pl_mysql_port" type="number" placeholder="3306"></div>
            <div class="field"><label for="pl_mysql_user">MySQL 账号</label><input id="pl_mysql_user" placeholder="root"></div>
            <div class="field"><label for="pl_mysql_pass">MySQL 密码</label><input id="pl_mysql_pass" type="password" placeholder=""></div>
            <div class="field m-b-0"><label for="pl_mysql_db">数据库名</label><input id="pl_mysql_db" placeholder="prompt_library"></div>
          </div>
        </div>
      </section>

      <section id="page-backends" class="hidden">
        <div class="page-head">
          <div><h2>后端参数</h2><div class="hint">每个后端独立配置，密钥只保存在本机</div></div>
          <button class="btn ghost" onclick="save()">💾 保存</button>
        </div>
        <div class="grid backend-grid">
          <div class="card glass">
            <h3><span class="b-icon">🟣</span> Vertex Proxy <span class="chip cyan" style="margin-left:auto"><span class="dot"></span>默认</span></h3>
            <div class="desc">本地代理，自动发现端口 / 密钥 / 模型</div>
            <div class="field"><label for="vertex_dir">代理目录</label><input id="vertex_dir" placeholder="代理目录"></div>
            <div class="field"><label for="vertex_base">API 地址（留空=按端口自动）</label><input id="vertex_base" placeholder="http://127.0.0.1:2156/v1"></div>
            <div class="field"><label for="vertex_key">API Key（留空=自动读取）</label><input id="vertex_key" type="password" placeholder="sk-..."></div>
            <div class="field m-b-0"><label for="vertex_model">图像模型（留空=自动最佳）</label><input id="vertex_model" placeholder="gemini-3-pro-image"></div>
          </div>
          <div class="card glass">
            <h3><span class="b-icon">🌐</span> Pollinations <span class="chip" style="margin-left:auto"><span class="dot"></span>免费免密钥</span></h3>
            <div class="desc">公共免费接口，无需任何密钥</div>
            <div class="field"><label for="pollinations_model">模型（留空=默认）</label><input id="pollinations_model" placeholder="flux"></div>
          </div>
          <div class="card glass">
            <h3><span class="b-icon">⚡</span> SiliconFlow <span class="chip" style="margin-left:auto"><span class="dot"></span>FLUX</span></h3>
            <div class="desc">OpenAI 兼容图像接口，国内可直连</div>
            <div class="field"><label for="sf_base">API 地址</label><input id="sf_base" placeholder="https://api.siliconflow.cn/v1"></div>
            <div class="field"><label for="sf_key">API Key</label><input id="sf_key" type="password" placeholder="sk-..."></div>
            <div class="field m-b-0"><label for="sf_model">模型</label><input id="sf_model" placeholder="black-forest-labs/FLUX.1-schnell"></div>
          </div>
          <div class="card glass">
            <h3><span class="b-icon">🖥</span> SD WebUI / Forge <span class="chip" style="margin-left:auto"><span class="dot"></span>本地</span></h3>
            <div class="desc">本地 Stable Diffusion，支持文生图与图生图</div>
            <div class="field"><label for="sd_base">地址</label><input id="sd_base" placeholder="http://127.0.0.1:7860"></div>
            <div class="grid grid-2">
              <div class="field"><label for="sd_sampler">采样器</label><input id="sd_sampler" placeholder="Euler a"></div>
              <div class="field"><label for="sd_steps">步数</label><input id="sd_steps" type="number" placeholder="28"></div>
              <div class="field"><label for="sd_cfg">CFG</label><input id="sd_cfg" type="number" step="0.5" placeholder="7"></div>
              <div class="field"><label for="sd_denoise">图生图去噪强度</label><input id="sd_denoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
            </div>
          </div>
          <div class="card glass">
            <h3><span class="b-icon">🔗</span> ComfyUI <span class="chip" style="margin-left:auto"><span class="dot"></span>本地</span></h3>
            <div class="desc">本地节点式工作流，自动上传图片图生图</div>
            <div class="field"><label for="cf_base">地址</label><input id="cf_base" placeholder="http://127.0.0.1:8188"></div>
            <div class="field"><label for="cf_checkpoint">Checkpoint（留空=自动）</label><input id="cf_checkpoint" placeholder="sd_xl_base_1.0.safetensors"></div>
            <div class="grid grid-2">
              <div class="field"><label for="cf_steps">步数</label><input id="cf_steps" type="number" placeholder="28"></div>
              <div class="field"><label for="cf_cfg">CFG</label><input id="cf_cfg" type="number" step="0.5" placeholder="7"></div>
              <div class="field"><label for="cf_denoise">图生图去噪强度</label><input id="cf_denoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
            </div>
          </div>
        </div>
      </section>

      <section id="page-test" class="hidden">
        <div class="page-head">
          <div><h2>试生成</h2><div class="hint">不保存配置也能先试试效果；图片预览不会离开你的电脑</div></div>
        </div>
        <div class="card glass">
          <div class="field"><label for="testPrompt">提示词</label><input id="testPrompt" placeholder="一只戴宇航员头盔的柴犬，写实风格"></div>
          <div class="field" style="display:flex;align-items:center;gap:10px">
            <button class="btn ghost" id="trBtn" onclick="translateOnly()">🔁 先翻译</button>
            <span class="hint" id="trHint">点击后用翻译官改写并填入上方输入框，可再手动修改</span>
          </div>
          <div class="grid grid-2">
            <div class="field"><label for="testBackend">后端</label><select id="testBackend"></select></div>
            <div class="field"><label for="testSize">尺寸</label><input id="testSize" placeholder="1024x1024（图生图可留空）"></div>
            <div class="field"><label for="testImage">参考图（图生图，可选）</label><input id="testImage" placeholder="图片路径或 http(s) 链接"></div>
            <div class="field"><label for="testDenoise">去噪强度（图生图）</label><input id="testDenoise" type="number" step="0.05" min="0" max="1" placeholder="0.6"></div>
            <div class="field"><label for="testSeed">种子（留空=随机）</label><input id="testSeed" type="number" placeholder="例如 42"></div>
            <div class="field"><label for="testTranslator">翻译官</label><select id="testTranslator"></select></div>
            <div class="field"><label><input type="checkbox" id="testAutoFix"> 自动看图改图</label></div>
            <div class="field" style="display:flex;align-items:flex-end"><button class="btn" id="genBtn" style="width:100%" onclick="testGenerate()">✨ 开始生成</button></div>
          </div>
          <div class="toolbar" style="margin-top:4px">
            <button class="btn ghost" onclick="testBackend()">🔌 测试后端连通性</button>
          </div>
          <div id="errorBox"></div>
          <div id="preview">
            <img id="previewImg" alt="生成结果预览">
            <div class="preview-meta" id="previewMeta"></div>
            <div class="toolbar" style="margin-top:8px">
              <button class="btn ghost" onclick="autoFixCurrent()">🔍 看图修正当前图（手动触发）</button>
            </div>
          </div>
        </div>
      </section>

      <section id="page-doctor" class="hidden">
        <div class="page-head">
          <div><h2>诊断</h2><div class="hint">检查各后端连通性、配置是否完整</div></div>
          <button class="btn ghost" onclick="runDoctor()">🔍 运行诊断（doctor）</button>
        </div>
        <div id="doctorResult"></div>
      </section>

    </div>
  </main>
</div>

<div class="save-bar">
<span class="chip cyan" style="background:rgba(10,16,28,.6)"><span class="dot"></span>仅本机访问</span>
  <button class="btn" onclick="save()">💾 保存配置</button>
</div>
<div class="toast" id="toast"></div>

<script>
let state = { config: null, busy: false, page: "import" };
const $ = id => document.getElementById(id);

async function api(path, opts) {
  const r = await fetch(path, opts);
  let j = null;
  try { j = await r.json(); } catch (e) { j = {}; }
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }

let toastTimer = null;
function toast(text, type) {
  const t = $("toast");
  t.textContent = text;
  t.className = "toast " + (type || "");
  requestAnimationFrame(() => t.classList.add("show"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.classList.remove("show"); }, 4200);
}

function setSaveChip(text, cls) {
  const chip = $("saveChip");
  chip.className = "chip " + (cls || "");
  chip.innerHTML = '<span class="dot"></span>' + esc(text);
}

function showPage(name) {
  document.querySelectorAll(".main-content > section").forEach(s => s.classList.add("hidden"));
  const sec = $("page-" + name);
  if (sec) sec.classList.remove("hidden");
  document.querySelectorAll("#sideNav button").forEach(b => b.classList.toggle("active", b.dataset.page === name));
  state.page = name;
  try { history.replaceState(null, "", "#page-" + name); } catch (e) {}
  closeMenu();
}

function toggleMenu() {
  const sb = $("sideBar"), ov = $("menuOverlay");
  const open = sb.classList.toggle("open");
  ov.classList.toggle("open", open);
}
function closeMenu() { $("sideBar").classList.remove("open"); $("menuOverlay").classList.remove("open"); }
document.querySelectorAll("#sideNav button").forEach(b => b.onclick = () => showPage(b.dataset.page));

async function loadConfig() {
  const j = await api("/api/config");
  state.config = j.config;
  $("cfgPath").textContent = j.path;
  $("cfgPath").title = j.path;
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
  const tr = c.translator || {};
  $("tr_engine").value = tr.engine || "deepseek";
  $("tr_lang").value = tr.output_lang || "zh";
  $("tr_autofix").value = tr.auto_fix === false ? "0" : "1";
  $("tr_maxfix").value = tr.max_fix_rounds != null ? tr.max_fix_rounds : 1;
  $("tr_fixmode").value = tr.fix_mode === "redraw" ? "redraw" : "edit";
  $("tr_keepbest").value = tr.fix_keep_best === false ? "0" : "1";
  $("tr_ds_base").value = tr.deepseek && tr.deepseek.base_url || "";
  $("tr_ds_key").value = tr.deepseek && tr.deepseek.api_key || "";
  $("tr_ds_model").value = tr.deepseek && tr.deepseek.model || "deepseek-v4-flash";
  $("tr_gm_model").value = tr.gemini && tr.gemini.model || "";
  $("tr_vision").value = tr.vision_bridge || "";
  const pl = c.prompt_library || {};
  const ple = pl.embedding || {}, plr = pl.rerank || {}, plm = pl.mysql || {};
  $("pl_enabled").value = pl.enabled === false ? "0" : "1";
  $("pl_feed").value = pl.use_in_translator === false ? "0" : "1";
  $("pl_topk").value = pl.top_k != null ? pl.top_k : 50;
  $("pl_finalk").value = pl.final_k != null ? pl.final_k : 8;
  $("pl_emb_base").value = ple.base_url || "";
  $("pl_emb_key").value = ple.api_key || "";
  $("pl_emb_model").value = ple.model || "Qwen/Qwen3-Embedding-8B";
  $("pl_rr").value = plr.enabled === false ? "0" : "1";
  $("pl_rr_base").value = plr.base_url || "";
  $("pl_rr_key").value = plr.api_key || "";
  $("pl_rr_model").value = plr.model || "Qwen/Qwen3-Reranker-8B";
  $("pl_mysql_host").value = plm.host || "127.0.0.1";
  $("pl_mysql_port").value = plm.port || 3306;
  $("pl_mysql_user").value = plm.user || "";
  $("pl_mysql_pass").value = plm.password || "";
  $("pl_mysql_db").value = plm.db || "prompt_library";
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
  fillTestTranslator();
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

function fillTestTranslator() {
  const sel = $("testTranslator");
  const cur = (state.config.translator && state.config.translator.engine) || "deepseek";
  [["auto","跟随配置（当前 " + (cur === "off" ? "关闭" : cur) + "）"],["deepseek","DeepSeek"],["gemini","Gemini"],["off","关闭（直传）"]].forEach(([v,label]) => {
    const o = document.createElement("option");
    o.value = v; o.textContent = label;
    if (v === "auto") o.selected = true;
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
  c.translator = Object.assign({}, c.translator, {
    enabled: $("tr_engine").value !== "off",
    engine: $("tr_engine").value,
    output_lang: $("tr_lang").value,
    auto_fix: $("tr_autofix").value === "1",
    max_fix_rounds: parseInt($("tr_maxfix").value) || 1,
    fix_mode: $("tr_fixmode").value,
    fix_keep_best: $("tr_keepbest").value === "1",
    deepseek: {
      base_url: $("tr_ds_base").value.trim(),
      api_key: $("tr_ds_key").value.trim(),
      model: $("tr_ds_model").value.trim() || "deepseek-v4-flash",
    },
    gemini: {
      model: $("tr_gm_model").value.trim(),
    },
    vision_bridge: $("tr_vision").value.trim(),
  });
  const embKey = $("pl_emb_key").value.trim();
  c.prompt_library = {
    enabled: $("pl_enabled").value === "1",
    use_in_translator: $("pl_feed").value === "1",
    top_k: parseInt($("pl_topk").value) || 50,
    final_k: parseInt($("pl_finalk").value) || 8,
    embedding: {
      base_url: $("pl_emb_base").value.trim() || "https://api.siliconflow.com/v1/embeddings",
      api_key: embKey,
      model: $("pl_emb_model").value.trim() || "Qwen/Qwen3-Embedding-8B",
    },
    rerank: {
      enabled: $("pl_rr").value === "1",
      base_url: $("pl_rr_base").value.trim() || "https://api.siliconflow.com/v1/rerank",
      api_key: $("pl_rr_key").value.trim() || embKey,
      model: $("pl_rr_model").value.trim() || "Qwen/Qwen3-Reranker-8B",
    },
    mysql: {
      host: $("pl_mysql_host").value.trim() || "127.0.0.1",
      port: parseInt($("pl_mysql_port").value) || 3306,
      user: $("pl_mysql_user").value.trim(),
      password: $("pl_mysql_pass").value,
      db: $("pl_mysql_db").value.trim() || "prompt_library",
    },
  };
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
    `<div class="toolbar" style="margin-top:4px">` +
    `<div style="flex:1.2"><label class="sr-only" for="vpBase">API 地址</label><input id="vpBase" value="${esc(r.base_url)}"></div>` +
    `<div style="flex:1.4"><label class="sr-only" for="vpModel">图像模型</label><select id="vpModel">${models}</select></div>` +
    `<div style="flex:1"><label class="sr-only" for="vpKey">API Key</label><select id="vpKey">${keys}</select></div>` +
    `<button class="btn" onclick="applyVertex()">应用</button></div>` +
    `<div class="field"><div class="hint">端口 ${r.port} · 共 ${r.models.length} 个模型 · 图像模型 ${r.image_models.length} 个 · 最佳：<b style="color:var(--cyan-deep)">${esc(r.best_model || "")}</b></div></div>`;
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

async function translateOnly() {
  const text = $("testPrompt").value.trim();
  if (!text) { toast("请先输入需求", "bad"); return; }
  const btn = $("trBtn");
  btn.disabled = true;
  btn.textContent = "⏳ 翻译中…";
  try {
    const r = await api("/api/translate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, engine: $("testTranslator").value }) });
    $("testPrompt").value = r.rewritten || text;
    const engine = r.engine_used === "off" ? "直传" : r.engine_used;
    const extra = r.fallback ? "（DeepSeek 通道异常，已自动改用 " + r.model + "）" : "";
    $("trHint").textContent = "已由 " + engine + " 翻译" + extra + "，可再手动修改";
    toast("✅ 翻译完成：" + engine, "ok");
  } catch (e) {
    toast("翻译失败：" + e.message, "bad");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔁 先翻译";
  }
}

async function testGenerate() {
  if (state.busy) return;
  state.busy = true;
  $("genBtn").disabled = true;
  $("genBtn").textContent = "⏳ 生成中…";
  $("errorBox").className = "";
  $("preview").className = "";
  const body = {
    prompt: $("testPrompt").value.trim() || "a cute astronaut dog",
    backend: $("testBackend").value,
    size: $("testSize").value.trim() || "1024x1024",
    image: $("testImage").value.trim(),
    denoise: $("testDenoise").value.trim() ? parseFloat($("testDenoise").value) : undefined,
    seed: $("testSeed").value.trim() ? parseInt($("testSeed").value) : undefined,
    translator: $("testTranslator").value,
    auto_fix: $("testAutoFix").checked,
  };
  try {
    const r = await api("/api/test-generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    state.lastPath = r.path;
    state.lastPrompt = body.prompt;
    const img = $("previewImg");
    img.src = "/api/image?path=" + encodeURIComponent(r.path);
    img.onerror = () => { img.style.display = "none"; };
    $("preview").className = "show";
    let meta = `后端 ${esc(r.backend)} · 尺寸 ${esc(r.size)} · 种子 ${r.seed}`;
    if (r.init_image) meta += ` · 图生图`;
    if (r.denoise != null) meta += ` · 去噪 ${r.denoise}`;
    if (r.translator) {
      const t = r.translator;
      const used = t.engine_used === "off" ? "直传" : t.engine_used;
      meta += ` · 翻译官 ${used}`;
      if (t.fallback) meta += `(自动切换)`;
    }
    if (r.auto_fix) {
      meta += ` · 自动改图 ${r.auto_fix.rounds} 轮`;
      if (r.auto_fix.reverted) meta += ` · 修正更差，已退回原图`;
    }
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

async function autoFixCurrent() {
  if (state.busy) return;
  if (!state.lastPath) { toast("请先生成一张图片，再点看图修正", "warn"); return; }
  state.busy = true;
  try {
    const r = await api("/api/auto-fix", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: state.lastPath, prompt: state.lastPrompt || "" }) });
    if (r.error) throw new Error(r.error);
    const img = $("previewImg");
    img.src = "/api/image?path=" + encodeURIComponent(r.path);
    let msg = r.fixed ? `已修正（${r.verdict === "kept" ? "保留修正版" : "修正更差，已退回原图"}）` : "检查通过，无需修正";
    if (r.issues) msg += " · " + String(r.issues).slice(0, 150);
    $("previewMeta").innerHTML = `<span>${msg}</span><code>${esc(r.path)}</code>`;
    toast(msg, r.fixed ? "ok" : "info");
  } catch (e) {
    const box = $("errorBox");
    box.textContent = "❌ 看图修正失败：" + e.message;
    box.className = "show";
  } finally {
    state.busy = false;
  }
}

async function runDoctor() {
  const r = await api("/api/doctor");
  const box = $("doctorResult");
  if (!r.checks || !r.checks.length) { box.innerHTML = `<p class="hint">${esc(r.error || "无诊断结果")}</p>`; return; }
  const cards = r.checks.map(c =>
    `<div class="card glass check-card">
      <div class="row">
        <span class="name">${esc(c.backend)}</span>
        <span class="chip ${c.ok ? "ok" : "bad"}"><span class="dot"></span>${c.ok ? "正常" : "失败"}</span>
      </div>
      <div class="msg">${esc(c.message)}</div>
      ${c.best_model ? `<div class="msg">最佳模型：<b style="color:var(--cyan-deep)">${esc(c.best_model)}</b></div>` : ""}
    </div>`
  ).join("");
  box.innerHTML = `<div class="check-grid">${cards}</div>` +
    `<p class="hint" style="margin-top:12px">默认后端：${esc(r.default_backend)} · 配置文件：${esc(r.config_file)}（${r.config_exists ? "存在" : "不存在，使用默认配置"}）</p>`;
}

loadConfig().catch(e => { setSaveChip("加载失败", "bad"); toast("加载失败：" + e.message, "bad"); });
const hashPage = (location.hash || "").match(/^#page-(.+)/);
if (hashPage) showPage(hashPage[1]);
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
    seed: int | None = None, translator: str = "auto", auto_fix: bool | None = None,
) -> dict[str, Any]:
    preview_dir = os.path.join(tempfile.gettempdir(), "deepseek-imagegen-preview")
    return image_gen.generate_with_translator(
        prompt,
        backend=backend or "auto",
        size=size or "1024x1024",
        out=preview_dir,
        init_image=image or None,
        denoise=denoise,
        seed=seed,
        translator=translator or "auto",
        auto_fix=auto_fix,
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
    allowed = {"banner.jpg", "icon.png", "avatar.png", "background.jpg"}
    if name not in allowed:
        return None
    target = os.path.join(ASSETS_DIR, name)
    if not os.path.isfile(target):
        return None
    with open(target, "rb") as handle:
        return handle.read()


def auto_fix_image(path: str, prompt: str) -> dict[str, Any]:
    """手动触发看图修正：视觉检查 → 局部小修 → 保留最佳。"""
    import time as _time

    cfg_all = image_gen.load_config()
    if not path or not os.path.isfile(path):
        raise image_gen.GenError("图片不存在：" + str(path))
    check = image_gen.run_vision_check(path, prompt, cfg_all, tiered=True)
    if not check.get("ok"):
        raise image_gen.GenError("视觉检查未完成：" + str(check.get("reason") or ""))
    if not check.get("has_issues"):
        return {
            "ok": True,
            "fixed": False,
            "issues": check.get("issues") or "",
            "path": path,
        }
    instruction = image_gen.build_fix_instruction(image_gen._fix_issues_text(check))
    data, mime, name = image_gen.load_init_image(path)
    width, height = image_gen.probe_image_size(data, mime) or (1024, 1024)
    out_path = os.path.splitext(path)[0] + "-fix.png"
    result_bytes = image_gen.gen_vertex_img2img(
        cfg_all, instruction, width, height, "", data, mime, name
    )
    with open(out_path, "wb") as handle:
        handle.write(result_bytes)
    _time.sleep(3)
    recheck = image_gen.run_vision_check(out_path, prompt, cfg_all, tiered=True)
    accepted, why = image_gen._fix_accepted(check, recheck)
    if not accepted:
        return {
            "ok": True,
            "fixed": True,
            "verdict": "reverted",
            "reason": why,
            "path": path,
            "issues": recheck.get("issues") or "",
        }
    return {
        "ok": True,
        "fixed": True,
        "verdict": "kept",
        "path": out_path,
        "issues": recheck.get("issues") or "",
    }


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
            if path == "/api/translate":
                body = self._read_json()
                result = image_gen.translate_prompt(
                    str(body.get("text") or ""),
                    engine=str(body.get("engine") or "auto"),
                )
                return self._send_json(result)
            if path == "/api/test-generate":
                body = self._read_json()
                result = test_generate(
                    str(body.get("prompt") or "a cute astronaut dog"),
                    str(body.get("backend") or "auto"),
                    size=str(body.get("size") or ""),
                    image=str(body.get("image") or ""),
                    denoise=body.get("denoise"),
                    seed=body.get("seed"),
                    translator=str(body.get("translator") or "auto"),
                    auto_fix=body.get("auto_fix"),
                )
                return self._send_json(result)
            if path == "/api/auto-fix":
                body = self._read_json()
                return self._send_json(
                    auto_fix_image(str(body.get("path") or ""), str(body.get("prompt") or ""))
                )
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
