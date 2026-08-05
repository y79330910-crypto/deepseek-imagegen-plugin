#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
洛天依主题 DeepSeek ImageGen 网页界面（单文件，仅用 Python 标准库）。

启动：
    python scripts/webui.py               # 默认 http://127.0.0.1:8766
    python scripts/webui.py --port 9000   # 自定义端口
    python scripts/webui.py --no-browser  # 不自动打开浏览器

功能：
    - 生成页：提示词 + 参考图上传 + 尺寸/构图/参考图类型/去噪/种子/模型/翻译官/词库 -> 生图预览与下载
    - 设置页：可视化编辑 ~/.deepseek-imagegen/config.json（密钥打码，未改动不覆盖）
    - 历史画廊：最近 50 张生成记录，可回填参数重新生成

隐私说明：壁纸、历史记录、上传的参考图仅保存在本机（~/.deepseek-imagegen/），不进入代码仓库。
"""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
IMAGE_GEN = SCRIPT_DIR / "image_gen.py"
APP_DIR = Path.home() / ".deepseek-imagegen"
WEBUI_DIR = APP_DIR / "webui"
UPLOAD_DIR = APP_DIR / "uploads"
OUT_DIR_DEFAULT = WEBUI_DIR / "outputs"
HISTORY_FILE = APP_DIR / "history.json"
WALLPAPER_FILE = WEBUI_DIR / "wallpaper.png"
DEFAULT_WALLPAPER = Path(r"C:\Users\yjq\Downloads\【哲风壁纸】洛天依-虚拟歌姬.png")
HISTORY_LIMIT = 200
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 40 * 1024 * 1024
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def ensure_utf8() -> None:
    """Windows 控制台中文修复；pythonw 无控制台时静默降级。"""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if sys.stdout is None:
        try:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass
    if sys.stderr is None:
        try:
            sys.stderr = open(os.devnull, "w", encoding="utf-8")
        except Exception:
            pass


# ---------- 配置 ----------

def syspath() -> None:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))


def load_config() -> dict:
    syspath()
    from imagegen.config import load_config as lc
    return lc()


def raw_config() -> dict:
    cfg_path = APP_DIR / "config.json"
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def masked_config() -> dict:
    syspath()
    from imagegen.config import mask_config as mc
    return mc(load_config())


def save_raw(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = APP_DIR / "config.json.tmp"
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(APP_DIR / "config.json"))


def deep_merge(base: dict, override: dict) -> dict:
    """递归合并用户设置；打码占位符（含 * 或 “(未设置)”）保持不变。"""
    for key, val in override.items():
        if val is None:
            continue
        if isinstance(val, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], val)
            continue
        if isinstance(val, str) and ("*" in val or val == "(未设置)"):
            continue
        base[key] = val
    return base


def normalize_edits(edits: dict) -> dict:
    """把前端传来的字符串型设置转成配置需要的类型。"""
    pl = edits.get("prompt_library")
    if isinstance(pl, dict):
        if isinstance(pl.get("categories"), str):
            pl["categories"] = [c.strip() for c in pl["categories"].split(",") if c.strip()]
        for key in ("top_k", "final_k", "priority_count"):
            if key in pl and pl[key] not in ("", None):
                try:
                    pl[key] = int(pl[key])
                except (TypeError, ValueError):
                    pass
        for key in ("enabled", "use_in_translator"):
            if key in pl and isinstance(pl[key], str):
                pl[key] = pl[key] in ("true", "on", "1")
        rr = pl.get("rerank")
        if isinstance(rr, dict) and isinstance(rr.get("enabled"), str):
            rr["enabled"] = rr["enabled"] in ("true", "on", "1")
        mysql = pl.get("mysql")
        if isinstance(mysql, dict) and mysql.get("port") not in ("", None):
            try:
                mysql["port"] = int(mysql["port"])
            except (TypeError, ValueError):
                pass
    sp = edits.get("size_policy")
    if isinstance(sp, dict):
        if sp.get("retries") not in ("", None):
            try:
                sp["retries"] = int(sp["retries"])
            except (TypeError, ValueError):
                pass
        if sp.get("tolerance") not in ("", None):
            try:
                sp["tolerance"] = float(sp["tolerance"])
            except (TypeError, ValueError):
                pass
    ref = edits.get("reference")
    if isinstance(ref, dict) and isinstance(ref.get("auto_classify"), str):
        ref["auto_classify"] = ref["auto_classify"] in ("true", "on", "1")
    eb = edits.get("extra_backends")
    if isinstance(eb, dict):
        for name, info in eb.items():
            if not isinstance(info, dict):
                continue
            if isinstance(info.get("sizes"), str):
                info["sizes"] = [s.strip() for s in info["sizes"].split(",") if s.strip()]
            if info.get("quality") in ("", None):
                info.pop("quality", None)
    return edits


# ---------- 壁纸 / 历史 ----------

def ensure_wallpaper() -> None:
    if not WALLPAPER_FILE.is_file() and DEFAULT_WALLPAPER.is_file():
        WEBUI_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(str(DEFAULT_WALLPAPER), str(WALLPAPER_FILE))
        except Exception:
            pass


def save_wallpaper(b64: str) -> bool:
    try:
        data = base64.b64decode(b64)
    except Exception:
        return False
    if len(data) > MAX_UPLOAD_BYTES:
        return False
    WEBUI_DIR.mkdir(parents=True, exist_ok=True)
    WALLPAPER_FILE.write_bytes(data)
    return True


def load_history() -> list:
    if HISTORY_FILE.is_file():
        try:
            items = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(items, list):
                return items
        except Exception:
            pass
    return []


def save_history(items: list) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(HISTORY_FILE))


def add_history(entry: dict) -> None:
    items = load_history()
    items.insert(0, entry)
    save_history(items[:HISTORY_LIMIT])


# ---------- 生成 ----------

def run_generate(payload: dict) -> dict:
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "提示词不能为空。"}
    cfg = load_config()
    out_dir = str(cfg.get("save_dir") or "").strip() or str(OUT_DIR_DEFAULT)
    cmd = [sys.executable, str(IMAGE_GEN), "generate", prompt, "--out", out_dir, "--json"]

    size = str(payload.get("size") or "").strip()
    if size:
        cmd += ["--size", size]
    seed = str(payload.get("seed") or "").strip()
    if seed:
        cmd += ["--seed", seed]
    model = str(payload.get("model") or "").strip()
    if model:
        cmd += ["--model", model]
    comp = str(payload.get("composition") or "auto").strip()
    if comp and comp != "auto":
        cmd += ["--composition", comp]
    rtype = str(payload.get("ref_type") or "auto").strip()
    if rtype and rtype != "auto":
        cmd += ["--ref-type", rtype]
    denoise = str(payload.get("denoise") or "").strip()
    if denoise:
        cmd += ["--denoise", denoise]
    tr = str(payload.get("translator") or "auto").strip()
    if tr and tr != "auto":
        cmd += ["--translator", tr]
    lib = str(payload.get("library") or "auto").strip()
    if lib == "on":
        cmd += ["--library"]
    elif lib == "off":
        cmd += ["--no-library"]
    backend = str(payload.get("backend") or "").strip().lower()
    if backend and backend != "vertex":
        cmd += ["--backend", backend]
    quality = str(payload.get("quality") or "").strip()
    if quality and quality != "auto":
        cmd += ["--quality", quality]

    saved_refs: list[dict] = []
    images_payload = payload.get("images")
    if not isinstance(images_payload, list) or not images_payload:
        b64 = payload.get("image_base64") or ""
        if b64:
            images_payload = [{
                "name": str(payload.get("image_name") or "ref.png"),
                "base64": b64,
                "role": str(payload.get("ref_type") or "auto"),
            }]
    for idx, img in enumerate(images_payload or []):
        if idx >= 4:
            break
        if not isinstance(img, dict):
            continue
        b64 = img.get("base64") or ""
        if not b64:
            continue
        try:
            data = base64.b64decode(b64)
        except Exception:
            return {"ok": False, "error": "参考图数据无法解码，请重新上传。"}
        if len(data) > MAX_UPLOAD_BYTES:
            return {"ok": False, "error": "参考图超过 20MB 限制。"}
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(img.get("name") or "ref.png"))
        if not Path(name).suffix.lower() in IMG_EXTS:
            name += ".png"
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        img_path = str(UPLOAD_DIR / f"{int(time.time() * 1000)}_{idx}_{name}")
        try:
            Path(img_path).write_bytes(data)
        except Exception as exc:
            return {"ok": False, "error": f"参考图保存失败：{exc}"}
        cmd += ["--image", img_path]
        role = str(img.get("role") or "").strip()
        if role and role != "auto":
            cmd += ["--ref-role", role]
        saved_refs.append({"path": img_path, "role": role or "auto"})

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "生成超时（30 分钟），请稍后重试。"}
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or "生成失败，请查看日志。"
        try:
            err = json.loads(proc.stdout)
            msg = err.get("error") or msg
        except Exception:
            pass
        return {"ok": False, "error": msg[:800]}
    try:
        res = json.loads(proc.stdout)
    except Exception:
        return {"ok": False, "error": f"无法解析生成结果：{proc.stdout[:300]}"}
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "生成失败")[:800]}

    add_history({
        "id": f"{int(time.time() * 1000)}-{len(load_history())}",
        "path": str(res.get("path") or ""),
        "refs": saved_refs,
        "prompt": prompt,
        "prompt_used": str((res.get("translator") or {}).get("rewritten") or ""),
        "backend": str(res.get("backend") or backend or "vertex"),
        "seed": res.get("seed"),
        "size": str(res.get("size") or ""),
        "actual_size": str(res.get("actual_size") or ""),
        "composition": str(res.get("composition_preset") or comp or ""),
        "ref_type": str((res.get("reference") or {}).get("label") or rtype or ""),
        "model": str((res.get("translator") or {}).get("model") or ""),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    return {"ok": True, "result": res}
# ---------- 文件访问白名单 ----------

def allowed_roots() -> list:
    roots = [str(WEBUI_DIR), str(UPLOAD_DIR)]
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    for key in ("save_dir", "mirror_dir"):
        v = str(cfg.get(key) or "").strip()
        if v:
            roots.append(v)
    for it in load_history():
        p = str(it.get("path") or "")
        if p:
            d = os.path.dirname(p)
            if d:
                roots.append(d)
    out = []
    for root in roots:
        abs_root = os.path.abspath(os.path.normpath(root))
        if abs_root not in out:
            out.append(abs_root)
    return out


def safe_image_path(raw: str):
    if not raw:
        return None
    target = os.path.abspath(os.path.normpath(raw))
    for root in allowed_roots():
        if target == root or target.startswith(root + os.sep):
            if os.path.isfile(target):
                return target
            return None
    return None


def serve_file(handler: BaseHTTPRequestHandler, path: str) -> None:
    ctype, _ = mimetypes.guess_type(path)
    ctype = ctype or "application/octet-stream"
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception:
        handler._json({"ok": False, "error": "文件读取失败。"}, 500)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-cache")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except Exception:
        pass


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "DeepSeekImageGenWebUI/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _html(self, text: str, code: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("请求体过大")
        return self.rfile.read(length)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html", "/settings"):
                self._html(PAGE_HTML)
            elif path == "/api/config":
                self._json({"ok": True, "config": masked_config()})
            elif path == "/api/history":
                self._json({"ok": True, "history": load_history()})
            elif path == "/api/wallpaper":
                if WALLPAPER_FILE.is_file():
                    serve_file(self, str(WALLPAPER_FILE))
                else:
                    self._json({"ok": False, "error": "壁纸不存在，请到设置页上传。"}, 404)
            elif path == "/api/image":
                target = safe_image_path(query.get("path", [""])[0])
                if target:
                    serve_file(self, target)
                else:
                    self._json({"ok": False, "error": "路径不在允许范围内。"}, 403)
            else:
                self._json({"ok": False, "error": "未找到该页面。"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"服务器错误：{exc}"}, 500)

    def do_POST(self):  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        try:
            payload = json.loads(self._read_body().decode("utf-8"))
        except Exception:
            self._json({"ok": False, "error": "请求体不是合法 JSON。"}, 400)
            return
        try:
            if path == "/api/config":
                raw = raw_config()
                merged = deep_merge(raw, normalize_edits(payload))
                save_raw(merged)
                self._json({"ok": True, "config": masked_config()})
            elif path == "/api/generate":
                self._json(run_generate(payload))
            elif path == "/api/wallpaper":
                if save_wallpaper(str(payload.get("image_base64") or "")):
                    self._json({"ok": True})
                else:
                    self._json({"ok": False, "error": "壁纸保存失败（需 ≤20MB 的图片）。"}, 400)
            else:
                self._json({"ok": False, "error": "未找到该接口。"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"服务器错误：{exc}"}, 500)

    def do_DELETE(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/api/history":
                items = load_history()
                target = query.get("id", [""])[0]
                if target:
                    items = [it for it in items if str(it.get("id") or "") != target]
                else:
                    items = []
                save_history(items)
                self._json({"ok": True, "history": items})
            elif path == "/api/history/clear":
                save_history([])
                self._json({"ok": True, "history": []})
            else:
                self._json({"ok": False, "error": "未找到该接口。"}, 404)
        except Exception as exc:  # noqa: BLE001
            self._json({"ok": False, "error": f"服务器错误：{exc}"}, 500)


# ---------- 页面 ----------

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>洛天依 · 生图工坊</title>
<style>
:root{--cyan:#00e5ff;--ink:#dce9f5;--mut:#8fa8c0;--glass:rgba(14,22,36,.58);--line:rgba(0,229,255,.24)}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;color:var(--ink);background:#08101c;overflow-x:hidden}
#bg{position:fixed;inset:-14px;background:url('/api/wallpaper') center/cover no-repeat;filter:blur(7px) brightness(.62) saturate(1.05);z-index:-2}
#veil{position:fixed;inset:0;background:linear-gradient(180deg,rgba(6,11,20,.38),rgba(6,11,20,.74) 72%);z-index:-1}
.shell{max-width:1180px;margin:0 auto;padding:16px 18px 46px}
.top{display:flex;align-items:center;gap:14px;margin:6px 2px 16px;flex-wrap:wrap}
.logo{font-size:22px;font-weight:700;letter-spacing:2px;background:linear-gradient(90deg,#7df6ff,#00e5ff,#ff5fa2);-webkit-background-clip:text;background-clip:text;color:transparent}
.sub{color:var(--mut);font-size:12px;margin-top:2px}
.tabs{display:flex;gap:8px;margin-left:auto}
.tab{padding:8px 18px;border-radius:999px;border:1px solid var(--line);background:rgba(10,18,30,.55);color:var(--ink);cursor:pointer;font-size:14px}
.tab.on{background:linear-gradient(135deg,#0096b8,#00e5ff);color:#03222e;font-weight:700;border-color:transparent}
.glass{background:var(--glass);border:1px solid var(--line);border-radius:18px;box-shadow:0 8px 30px rgba(0,0,0,.35)}
.card{padding:18px 20px;margin-bottom:16px}
h3{margin:0 0 12px;font-size:15px;color:#9ff2ff;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
.field{display:flex;flex-direction:column;gap:5px;margin-bottom:11px}
.field label{font-size:12px;color:var(--mut)}
input,select,textarea{background:rgba(8,16,28,.72);border:1px solid rgba(0,229,255,.28);border-radius:9px;color:var(--ink);padding:8px 10px;font-size:13px;outline:none;width:100%}
input:focus,select:focus,textarea:focus{border-color:var(--cyan);box-shadow:0 0 0 2px rgba(0,229,255,.16)}
textarea{min-height:96px;resize:vertical}
.btn{border:none;border-radius:10px;padding:10px 22px;font-size:14px;cursor:pointer;font-weight:700}
.btn.main{background:linear-gradient(135deg,#0096b8,#00e5ff);color:#03222e}
.btn.main:disabled{opacity:.55;cursor:wait}
.btn.ghost{background:rgba(10,18,30,.6);border:1px solid var(--line);color:var(--ink)}
.btn.small{padding:6px 12px;font-size:12px;border-radius:8px}
.drop{border:1.5px dashed rgba(0,229,255,.45);border-radius:12px;padding:16px;text-align:center;color:var(--mut);cursor:pointer;background:rgba(8,16,28,.45)}
.drop.over{border-color:var(--cyan);background:rgba(0,229,255,.08);color:#bff4ff}
.drop img{max-height:120px;border-radius:8px;display:block;margin:8px auto 0}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
#status{font-size:13px;color:#9ff2ff;min-height:20px;margin:10px 0}
#error{display:none;background:rgba(255,70,110,.14);border:1px solid rgba(255,95,150,.5);color:#ffb9cd;border-radius:10px;padding:10px 12px;font-size:13px;margin-bottom:12px;white-space:pre-wrap}
#result{display:none}
#resultImg{max-width:100%;max-height:520px;border-radius:12px;border:1px solid var(--line);display:block;margin:0 auto 12px}
.info{font-size:12px;color:var(--mut);line-height:1.8;word-break:break-all}
.info b{color:#bff4ff;font-weight:600}
.warn{color:#ffd27a}
#saveChip{display:none;color:#7dffb0;font-size:13px}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px}
.gcard{background:rgba(12,20,34,.66);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.gcard img{width:100%;height:160px;object-fit:cover;cursor:pointer;display:block}
.gbody{padding:9px 11px}
.gp{font-size:11px;color:var(--mut);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-height:30px}
.gm{font-size:11px;color:#6f8aa5;margin:5px 0}
.gbtn{display:flex;gap:6px}
.hint{font-size:12px;color:var(--mut);margin-top:6px}
.set-group h4{color:#c9f7ff;font-size:13px;margin:16px 0 10px;padding-left:8px;border-left:3px solid var(--cyan)}
.wall-prev{max-width:220px;border-radius:10px;border:1px solid var(--line);margin-top:8px}
@media (max-width:720px){.tabs{margin-left:0}.grid{grid-template-columns:1fr}}
</style>
</head><body>
<div id="bg"></div><div id="veil"></div>
<div class="shell">
  <div class="top">
    <div>
      <div class="logo">洛天依 · 生图工坊</div>
      <div class="sub">DeepSeek ImageGen Web UI · 本地出图</div>
    </div>
    <div class="tabs">
      <button class="tab on" data-tab="generate">生成</button>
      <button class="tab" data-tab="settings">设置</button>
      <button class="tab" data-tab="gallery">画廊</button>
    </div>
  </div>

  <section class="glass card" id="page-generate">
    <h3>✍ 描述你想画的画面</h3>
    <div class="field">
      <textarea id="prompt" placeholder="例如：洛天依在樱花树下弹着古筝，全身构图，日系插画风格，金色夕阳……"></textarea>
    </div>
    <div class="field">
      <label>参考图（可多张，最多 4 张；每张可选用途：角色/服装/风格/姿势/场景/构图/物品）</label>
      <div class="drop" id="drop"><span id="dropText">拖拽图片到这里，或点击选择（可多张）</span></div>
      <input type="file" id="refFile" accept=".png,.jpg,.jpeg,.webp" multiple hidden>
      <div class="gal" id="refList" style="margin-top:10px"></div>
      <div class="row" style="margin-top:8px">
        <button class="btn ghost small" id="refClear" type="button">清空参考图</button>
        <span class="hint" id="refName"></span>
      </div>
    </div>
    <div class="grid">
      <div class="field"><label>出图后端</label><select id="backend"><option value="vertex">本地 Vertex（默认）</option></select></div>
      <div class="field"><label>尺寸（宽x高，留空=默认）</label><input id="size" placeholder="1024x1024"></div>
      <div class="field"><label>质量（仅备用后端）</label>
        <select id="quality"><option value="auto">auto（默认）</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select>
      </div>
      <div class="field"><label>批量张数（1~10）</label><input id="count" type="number" min="1" max="10" value="1"></div>
      <div class="field"><label>构图预设</label>
        <select id="composition">
          <option value="auto">自动</option><option value="full-body">全身（竖版）</option><option value="half-body">半身</option><option value="portrait">特写</option><option value="landscape">横版广角</option>
        </select>
      </div>
      <div class="field"><label>参考图类型</label>
        <select id="ref_type">
          <option value="auto">自动识别</option><option value="character">角色人物</option><option value="outfit">服装造型</option><option value="style">艺术风格</option><option value="scene">场景背景</option><option value="composition">构图布局</option><option value="pose">姿势动作</option><option value="object">物品产品</option>
        </select>
      </div>
      <div class="field"><label>去噪强度（0~1，仅参考图生图）</label><input id="denoise" type="number" min="0" max="1" step="0.05" placeholder="0.6"></div>
      <div class="field"><label>种子（留空=随机）</label><input id="seed" placeholder="随机"></div>
      <div class="field"><label>模型（留空=自动选最佳）</label><input id="model" placeholder="自动"></div>
      <div class="field"><label>翻译官引擎</label>
        <select id="translator">
          <option value="auto">跟随配置</option><option value="deepseek">DeepSeek</option><option value="gemini">Gemini</option><option value="off">直传（不改写）</option>
        </select>
      </div>
      <div class="field"><label>提示词词库</label>
        <select id="library">
          <option value="auto">跟随配置</option><option value="on">开启</option><option value="off">关闭</option>
        </select>
      </div>
    </div>
    <div class="row" style="margin-top:4px"><span class="hint">快捷尺寸：</span><span id="sizeChips"></span></div>
    <div class="row">
      <button class="btn main" id="genBtn">开始生成</button>
      <span class="hint">生成可能要 1~3 分钟，请耐心等待</span>
    </div>
    <div id="status"></div>
    <div id="error"></div>
    <div id="result">
      <div class="gal" id="resultStrip" style="margin-bottom:12px"></div>
      <img id="resultImg" alt="生成结果">
      <div class="row" style="justify-content:center;margin-bottom:10px">
        <a class="btn main small" id="dlLink" download>下载图片</a>
        <span class="hint" id="mirrorNote"></span>
      </div>
      <div class="info" id="resultInfo"></div>
    </div>
  </section>

  <section class="glass card" id="page-settings" style="display:none">
    <div class="row" style="justify-content:space-between">
      <h3>⚙ 设置（保存后立即生效，密钥打码显示）</h3>
      <span id="saveChip">✓ 已保存</span>
    </div>
    <div class="set-group"><h4>出图默认</h4>
      <div class="grid">
        <div class="field"><label>默认尺寸</label><input data-path="default_size" placeholder="1024x1024"></div>
        <div class="field"><label>默认保存目录（留空=网页目录内）</label><input data-path="save_dir" placeholder="例如 D:\images"></div>
        <div class="field"><label>自动副本目录</label><input data-path="mirror_dir" placeholder="C:\Users\yjq\Pictures\codex"></div>
      </div>
    </div>
    <div class="set-group"><h4>翻译官</h4>
      <div class="grid">
        <div class="field"><label>引擎</label>
          <select data-path="translator.engine"><option value="deepseek">DeepSeek</option><option value="gemini">Gemini</option><option value="off">直传</option></select>
        </div>
        <div class="field"><label>输出语言</label><select data-path="translator.output_lang"><option value="zh">中文</option><option value="en">English</option></select></div>
        <div class="field"><label>DeepSeek 地址</label><input data-path="translator.deepseek.base_url" placeholder="https://api.deepseek.com"></div>
        <div class="field"><label>DeepSeek 密钥（留空=自动读 Codex 配置）</label><input data-path="translator.deepseek.api_key" type="password" placeholder="sk-..."></div>
        <div class="field"><label>DeepSeek 模型</label><input data-path="translator.deepseek.model" placeholder="deepseek-v4-flash"></div>
        <div class="field"><label>Gemini 翻译模型（留空=自动最佳）</label><input data-path="translator.gemini.model" placeholder="自动"></div>
      </div>
    </div>
    <div class="set-group"><h4>构图与尺寸策略</h4>
      <div class="grid">
        <div class="field"><label>默认构图</label>
          <select data-path="composition.preset"><option value="auto">自动</option><option value="full-body">全身</option><option value="half-body">半身</option><option value="portrait">特写</option><option value="landscape">横版</option></select>
        </div>
        <div class="field"><label>尺寸策略</label>
          <select data-path="size_policy.mode"><option value="auto">auto（自动兜底）</option><option value="strict">strict（不符报错）</option><option value="warn">warn（仅提示）</option></select>
        </div>
        <div class="field"><label>尺寸兜底重试次数</label><input data-path="size_policy.retries" type="number" min="0" max="5"></div>
        <div class="field"><label>尺寸容差（如 0.06）</label><input data-path="size_policy.tolerance" type="number" step="0.01" min="0" max="0.3"></div>
      </div>
    </div>
    <div class="set-group"><h4>提示词词库（MySQL + 向量检索）</h4>
      <div class="grid">
        <div class="field"><label>启用词库</label><select data-path="prompt_library.enabled"><option value="true">开启</option><option value="false">关闭</option></select></div>
        <div class="field"><label>喂给翻译官</label><select data-path="prompt_library.use_in_translator"><option value="true">开启</option><option value="false">关闭</option></select></div>
        <div class="field"><label>初选数量</label><input data-path="prompt_library.top_k" type="number" min="1" max="200"></div>
        <div class="field"><label>最终参考条数</label><input data-path="prompt_library.final_k" type="number" min="1" max="20"></div>
        <div class="field"><label>分类过滤（逗号分隔，留空=全部）</label><input data-path="prompt_library.categories" placeholder="插画艺术,表情包贴纸"></div>
        <div class="field"><label>优先分类（置顶）</label><input data-path="prompt_library.priority_category" placeholder="留空=无"></div>
        <div class="field"><label>优先条数</label><input data-path="prompt_library.priority_count" type="number" min="0" max="10"></div>
        <div class="field"><label>Embedding 地址</label><input data-path="prompt_library.embedding.base_url" placeholder="https://api.siliconflow.com/v1/embeddings"></div>
        <div class="field"><label>Embedding 密钥</label><input data-path="prompt_library.embedding.api_key" type="password" placeholder="sk-..."></div>
        <div class="field"><label>Rerank 开启</label><select data-path="prompt_library.rerank.enabled"><option value="true">开启</option><option value="false">关闭</option></select></div>
        <div class="field"><label>Rerank 地址</label><input data-path="prompt_library.rerank.base_url" placeholder="https://api.siliconflow.com/v1/rerank"></div>
        <div class="field"><label>Rerank 密钥</label><input data-path="prompt_library.rerank.api_key" type="password" placeholder="sk-..."></div>
        <div class="field"><label>MySQL 主机</label><input data-path="prompt_library.mysql.host" placeholder="127.0.0.1"></div>
        <div class="field"><label>MySQL 端口</label><input data-path="prompt_library.mysql.port" type="number" placeholder="3306"></div>
        <div class="field"><label>MySQL 账号</label><input data-path="prompt_library.mysql.user" placeholder="root"></div>
        <div class="field"><label>MySQL 密码</label><input data-path="prompt_library.mysql.password" type="password"></div>
        <div class="field"><label>数据库名</label><input data-path="prompt_library.mysql.db" placeholder="prompt_library"></div>
      </div>
    </div>
    <div class="set-group"><h4>Vertex 代理（本地后端）</h4>
      <div class="grid">
        <div class="field"><label>代理目录（自动读端口/密钥/模型）</label><input data-path="vertex.dir" placeholder="vertex-proxy 的 dist 目录"></div>
        <div class="field"><label>代理地址（留空=自动）</label><input data-path="vertex.base_url" placeholder="http://127.0.0.1:2156/v1"></div>
        <div class="field"><label>代理密钥（留空=自动读 api_keys.txt）</label><input data-path="vertex.api_key" type="password" placeholder="sk-..."></div>
        <div class="field"><label>图像模型（留空=自动最佳）</label><input data-path="vertex.model" placeholder="自动"></div>
      </div>
    </div>
    <div class="set-group"><h4>备用后端（dragtokens）</h4>
      <div class="grid">
        <div class="field"><label>地址</label><input data-path="extra_backends.dragtokens.base_url" placeholder="https://draw.dragtokens.com/v1"></div>
        <div class="field"><label>密钥</label><input data-path="extra_backends.dragtokens.api_key" type="password" placeholder="sk-..."></div>
        <div class="field"><label>模型</label><input data-path="extra_backends.dragtokens.model" placeholder="gpt-image-2 / gpt-image-2-4k超分 / gpt-image-2-原生4k"></div>
        <div class="field"><label>尺寸白名单（逗号分隔，留空=按模型自动）</label><input data-path="extra_backends.dragtokens.sizes" placeholder="1254x1254,1536x1024,1024x1536"></div>
        <div class="field"><label>默认质量</label>
          <select data-path="extra_backends.dragtokens.quality"><option value="auto">auto</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select>
        </div>
      </div>
    </div>
    <div class="set-group"><h4>参考图识别与壁纸</h4>
      <div class="grid">
        <div class="field"><label>参考图自动分类</label><select data-path="reference.auto_classify"><option value="true">开启</option><option value="false">关闭</option></select></div>
        <div class="field"><label>视觉识别脚本路径（留空=自动查找）</label><input data-path="reference.vision_script" placeholder="vision_bridge.py 完整路径"></div>
        <div class="field"><label>更换壁纸（PNG/JPG ≤20MB）</label><input type="file" id="wallFile" accept="image/*"></div>
      </div>
      <img class="wall-prev" id="wallPrev" alt="当前壁纸">
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn main" id="saveBtn">保存设置</button>
      <span class="hint">保存后网页立即生效，下次生成自动使用新设置</span>
    </div>
  </section>

  <section class="glass card" id="page-gallery" style="display:none">
    <div class="row" style="justify-content:space-between">
      <h3 style="margin:0">🖼 历史画廊（最近 200 张）</h3>
      <button class="btn ghost small" id="galClear">清空历史</button>
    </div>
    <div class="row" style="margin-top:10px">
      <div class="field" style="flex:1;margin:0"><input id="galSearch" placeholder="搜索提示词…"></div>
      <div class="field" style="margin:0"><select id="galFilter"><option value="">全部后端</option></select></div>
    </div>
    <div class="gal" id="gal" style="margin-top:12px"></div>
    <div class="hint" id="galEmpty" style="display:none">还没有生成记录，先去生成一张吧。</div>
  </section>
</div><script>
const $=id=>document.getElementById(id);
let refs=[];
const ROLE_OPTIONS=[["auto","自动"],["character","角色"],["outfit","服装"],["style","风格"],["pose","姿势"],["scene","场景"],["composition","构图"],["object","物品"]];
const api=async(url,opt)=>{const r=await fetch(url,opt);let j;try{j=await r.json()}catch(e){throw new Error("服务器响应异常")}if(!r.ok||j.ok===false){throw new Error(j.error||"请求失败")}return j};
const getPath=(o,p)=>{let v=o;for(const k of p.split(".")){if(v==null)return undefined;v=v[k]}return v};
const setPath=(o,p,v)=>{const ks=p.split(".");let t=o;for(let i=0;i<ks.length-1;i++){if(t[ks[i]]==null||typeof t[ks[i]]!=="object")t[ks[i]]={};t=t[ks[i]]}t[ks[ks.length-1]]=v};
const esc=s=>String(s??"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
function switchTab(name){document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("on",t.dataset.tab===name));["generate","settings","gallery"].forEach(p=>$("page-"+p).style.display=p===name?"":"none");}
document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>{switchTab(t.dataset.tab);if(t.dataset.tab==="gallery")loadHistory()});
const SIZE_PRESETS={
 "vertex":["1024x1024","768x1408","1408x768","1536x1024"],
 "gpt-image-2":["1254x1254","1536x1024","1024x1536"],
 "4k超分":["2048x2048","2560x1440","3840x2160","2160x3840","3696x1584"],
 "原生4k":["2048x2048","3840x2160","2160x3840"],
};
function presetFor(backend,model){
 const m=(model||"").toLowerCase();
 if(m.includes("原生4k"))return SIZE_PRESETS["原生4k"];
 if(m.includes("4k超分"))return SIZE_PRESETS["4k超分"];
 if(backend!=="vertex")return SIZE_PRESETS["gpt-image-2"];
 return SIZE_PRESETS.vertex;
}
function renderSizeChips(){
 const chips=$("sizeChips");chips.innerHTML="";
 presetFor($("backend").value,$("model").value.trim()).forEach(s=>{
  const b=document.createElement("button");b.type="button";b.className="btn ghost small";b.style.marginRight="6px";b.style.marginBottom="6px";b.textContent=s;
  b.onclick=()=>{$("size").value=s};chips.appendChild(b);
 });
}
$("backend").onchange=renderSizeChips;
$("model").oninput=renderSizeChips;
const drop=$("drop"),refFile=$("refFile");
drop.onclick=()=>refFile.click();
drop.ondragover=e=>{e.preventDefault();drop.classList.add("over")};
drop.ondragleave=()=>drop.classList.remove("over");
drop.ondrop=e=>{e.preventDefault();drop.classList.remove("over");if(e.dataTransfer.files.length)handleRefs(e.dataTransfer.files)};
refFile.onchange=()=>{if(refFile.files.length)handleRefs(refFile.files);refFile.value=""};
function handleRefs(files){
 const arr=[...files].filter(f=>/\.(png|jpe?g|webp)$/i.test(f.name));
 if(!arr.length){showErr("仅支持 PNG/JPG/WebP");return}
 const room=4-refs.length;
 if(arr.length>room){showErr("参考图最多 4 张，多余的已忽略");arr.length=room}
 if(!arr.length){renderRefList();return}
 let pending=arr.length;
 arr.forEach(f=>{
  if(f.size>20*1024*1024){showErr("参考图超过 20MB："+f.name);pending--;if(!pending)renderRefList();return}
  const rd=new FileReader();
  rd.onload=()=>{const s=String(rd.result);
   refs.push({name:f.name,base64:s.slice(s.indexOf(",")+1),dataUrl:s,role:refs.length===1?"character":"auto"});
   pending--;if(!pending)renderRefList()};
  rd.readAsDataURL(f);
 });
}
function renderRefList(){
 const box=$("refList");box.innerHTML="";
 if(!refs.length){$("refName").textContent="";$("dropText").textContent="拖拽图片到这里，或点击选择（可多张）";return}
 $("dropText").textContent="已选 "+refs.length+" 张参考图（再拖可继续添加，最多 4 张）";
 refs.forEach((r,idx)=>{
  const card=document.createElement("div");card.className="gcard";
  const img=document.createElement("img");img.src=r.dataUrl||"";img.alt="参考图";img.onclick=()=>{window.open(img.src)};
  const sel=document.createElement("select");
  ROLE_OPTIONS.forEach(o=>{const op=document.createElement("option");op.value=o[0];op.textContent=o[1];if(o[0]===r.role)op.selected=true;sel.appendChild(op)});
  sel.onchange=()=>{r.role=sel.value};
  const del=document.createElement("button");del.type="button";del.className="btn ghost small";del.textContent="删除";
  del.onclick=()=>{refs.splice(idx,1);renderRefList()};
  const body=document.createElement("div");body.className="gbody";
  const lab=document.createElement("div");lab.className="gm";lab.textContent="图"+(idx+1);
  body.appendChild(lab);body.appendChild(sel);body.appendChild(del);
  card.appendChild(img);card.appendChild(body);
  box.appendChild(card);
 });
}
$("refClear").onclick=()=>{refs=[];renderRefList()};
async function restoreRefs(list){
 refs=[];
 for(const it of (list||[])){
  try{
   const resp=await fetch("/api/image?path="+encodeURIComponent(it.path||""));
   const blob=await resp.blob();
   const dataUrl=await new Promise((res,rej)=>{const rd=new FileReader();rd.onload=()=>res(String(rd.result));rd.onerror=rej;rd.readAsDataURL(blob)});
   const s=String(dataUrl);
   refs.push({name:(it.path||"ref.png").split(/[\\/]/).pop()||"ref.png",base64:s.slice(s.indexOf(",")+1),dataUrl:s,role:it.role||(refs.length===0?"character":"auto")});
  }catch(e){}
 }
 renderRefList();
}
function showErr(m){const e=$("error");e.textContent=m;e.style.display="block"}
function hideErr(){$("error").style.display="none"}
function setStatus(s){$("status").textContent=s||""}
function showResult(res){
 const enc=encodeURIComponent(res.path);
 $("resultImg").src="/api/image?path="+enc;$("dlLink").href="/api/image?path="+enc;$("dlLink").download="result.png";
 const ref=res.reference||{};const tr=res.translator||{};const warns=res.warnings||[];
 let info="<b>后端：</b>"+esc(res.backend||"vertex")+" · <b>文件：</b>"+esc(res.path)+"<br><b>种子：</b>"+esc(res.seed)+"<br><b>尺寸：</b>请求 "+esc(res.size)+" → 实际 "+(res.actual_size||"未知")+" "+(res.size_match?"✓":"✗")+"<br>";
 if(res.quality)info+="<b>质量：</b>"+esc(res.quality)+"<br>";
 if(res.composition_preset&&res.composition_preset!=="auto")info+="<b>构图：</b>"+esc(res.composition_preset)+"<br>";
 if(ref.items&&ref.items.length>1)info+="<b>参考图分工：</b>"+ref.items.map((it,i)=>"图"+(i+1)+"·"+esc(it.label||it.type)).join(" + ")+"<br>";
 else if(ref.type)info+="<b>参考图类型：</b>"+esc(ref.label||ref.type)+"（"+(ref.method||"")+"）<br>";
 if(tr.engine_used&&tr.engine_used!=="off")info+="<b>翻译官：</b>"+esc(tr.engine_used)+(tr.fallback?"（已自动降级）":"")+"<br>";
 info+="<b>镜像副本：</b>"+esc(res.mirror_path||"无");
 if(tr.rewritten)info+='<br><details><summary style="cursor:pointer"><b>实际生效提示词</b></summary>'+esc(tr.rewritten)+'</details>';
 if(tr.original)info+='<details><summary style="cursor:pointer"><b>你的原文</b></summary>'+esc(tr.original)+'</details>';
 if(warns.length)info+="<br><span class=\"warn\">提示："+esc(warns.join("；"))+"</span>";
 $("resultInfo").innerHTML=info;$("result").style.display="block";setStatus("✅ 生成完成");
 $("mirrorNote").textContent=res.mirror_path?"已自动备份一份到镜像目录":"";
}
function addStripCard(res){
 const strip=$("resultStrip");
 const card=document.createElement("div");card.className="gcard";
 const enc=encodeURIComponent(res.path);
 card.innerHTML='<img src="/api/image?path='+enc+'" alt="结果"><div class="gbody"><div class="gm">'+(res.actual_size||"")+' · '+(res.backend||"")+'</div><a class="btn ghost small" href="/api/image?path='+enc+'" download="result.png">下载</a></div>';
 card.querySelector("img").onclick=()=>{window.open("/api/image?path="+enc)};
 strip.appendChild(card);
}
$("genBtn").onclick=async()=>{
 const prompt=$("prompt").value.trim();if(!prompt){showErr("请先输入提示词");return}
 const count=Math.min(10,Math.max(1,parseInt($("count").value||"1",10)||1));
 hideErr();$("result").style.display="none";$("resultStrip").innerHTML="";$("genBtn").disabled=true;
 let firstRes=null,errs=[];
 try{
  for(let i=1;i<=count;i++){
   setStatus("⏳ 正在生成第 "+i+"/"+count+" 张，请耐心等待（约 1~3 分钟）…");
   const body={prompt,images:refs.map(r=>({name:r.name,base64:r.base64,role:r.role})),size:$("size").value.trim(),composition:$("composition").value,ref_type:$("ref_type").value,denoise:$("denoise").value.trim(),seed:(i===1?$("seed").value.trim():""),model:$("model").value.trim(),translator:$("translator").value,library:$("library").value,backend:$("backend").value,quality:$("quality").value};
   try{
    const r=await api("/api/generate",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const res=r.result;
    if(i===1){firstRes=res;showResult(res)}
    addStripCard(res);
   }catch(err){errs.push("第 "+i+" 张失败："+err.message)}
  }
  if(count>1){setStatus(errs.length?"⚠ 生成完成，成功 "+(count-errs.length)+" 张，失败 "+errs.length+" 张":"✅ 全部生成完成（共 "+count+" 张）");if(errs.length)showErr(errs.join("\n"));}
  loadHistory();
 }catch(err){showErr("生成失败："+err.message);setStatus("")}
 finally{$("genBtn").disabled=false;}
};
async function loadConfig(){const r=await api("/api/config");const c=r.config;
 document.querySelectorAll("[data-path]").forEach(el=>{const v=getPath(c,el.dataset.path);if(v===undefined)return;
  if(el.tagName==="SELECT"){el.value=String(v)}else if(el.type==="checkbox"){el.checked=!!v}else{el.value=Array.isArray(v)?v.join(", "):v}});
 const names=Object.keys(c.extra_backends||{});
 const sel=$("backend");const cur=sel.value||"vertex";
 sel.innerHTML='<option value="vertex">本地 Vertex（默认）</option>'+names.map(n=>'<option value="'+esc(n)+'">'+esc(n)+'</option>').join("");
 sel.value=names.includes(cur)?cur:"vertex";
 const all=["vertex"].concat(names.filter(n=>n!=="vertex"));
 const flt=$("galFilter");const fcur=flt.value;
 flt.innerHTML='<option value="">全部后端</option>'+all.map(n=>'<option value="'+esc(n)+'">'+esc(n)+'</option>').join("");
 flt.value=all.includes(fcur)?fcur:"";
 renderSizeChips();
 $("wallPrev").src="/api/wallpaper?"+Date.now();}
function collect(){const o={};document.querySelectorAll("[data-path]").forEach(el=>{
 let v;if(el.tagName==="SELECT")v=el.value;else if(el.type==="checkbox")v=el.checked;else v=el.value.trim();
 if(v==="")return;setPath(o,el.dataset.path,v)});return o;}
$("saveBtn").onclick=async()=>{hideErr();try{await api("/api/config",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(collect())});
 $("saveChip").style.display="inline";setTimeout(()=>$("saveChip").style.display="none",2500);await loadConfig()}catch(err){showErr("保存失败："+err.message)}};
$("wallFile").onchange=async()=>{const f=$("wallFile").files[0];if(!f)return;if(f.size>20*1024*1024){showErr("壁纸超过 20MB");return}
 const rd=new FileReader();rd.onload=async()=>{try{const s=String(rd.result);await api("/api/wallpaper",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({image_base64:s.slice(s.indexOf(",")+1)})});
  location.reload()}catch(err){showErr("壁纸保存失败："+err.message)}};rd.readAsDataURL(f)};
async function loadHistory(){
 const r=await api("/api/history");let h=r.history||[];
 const kw=$("galSearch").value.trim().toLowerCase();const fb=$("galFilter").value;
 if(fb)h=h.filter(it=>(it.backend||"vertex")===fb);
 if(kw)h=h.filter(it=>String(it.prompt||"").toLowerCase().includes(kw)||String(it.prompt_used||"").toLowerCase().includes(kw));
 const g=$("gal");g.innerHTML="";
 if(!h.length){$("galEmpty").style.display="block";return}$("galEmpty").style.display="none";
 h.forEach(it=>{const card=document.createElement("div");card.className="gcard";
  card.innerHTML='<img src="/api/image?path='+encodeURIComponent(it.path||"")+'" alt="缩略图"><div class="gbody"><div class="gp">'+esc(it.prompt||"")+'</div><div class="gm">'+esc(it.backend||"vertex")+' · 种子 '+(it.seed??"-")+' · '+(it.size||"")+' · '+(it.actual_size||"")+(it.refs&&it.refs.length?' · 参考'+(it.refs.length)+'张':'')+'<br>'+esc(it.ts||"")+'</div>'+(it.prompt_used?'<details class="gm"><summary style="cursor:pointer">生效提示词</summary><div class="gp">'+esc(it.prompt_used)+'</div></details>':"")+'<div class="gbtn"><button class="btn ghost small" data-act="fill">回填重搞</button><button class="btn ghost small" data-act="del">删除</button><a class="btn ghost small" href="/api/image?path='+encodeURIComponent(it.path||"")+'" download="result.png">下载</a></div></div>';
  card.querySelector("img").onclick=()=>{window.open("/api/image?path="+encodeURIComponent(it.path||""))};
  card.querySelector('[data-act="fill"]').onclick=()=>{fillForm(it);switchTab("generate")};
  card.querySelector('[data-act="del"]').onclick=async()=>{if(!confirm("删除这张历史记录？"))return;try{await api("/api/history?id="+encodeURIComponent(it.id||""),{method:"DELETE"});loadHistory()}catch(e){showErr("删除失败："+e.message)}};
  g.appendChild(card)});}
$("galSearch").oninput=loadHistory;$("galFilter").onchange=loadHistory;
$("galClear").onclick=async()=>{if(!confirm("确定清空全部历史记录？此操作不可恢复。"))return;try{await api("/api/history/clear",{method:"DELETE"});loadHistory()}catch(e){showErr("清空失败："+e.message)}};
function fillForm(it){$("prompt").value=it.prompt||"";if(it.seed!=null&&it.seed!=="")$("seed").value=it.seed;if(it.size)$("size").value=it.size;if(it.composition&&it.composition!=="auto")$("composition").value=it.composition;if(it.backend){const sel=$("backend");if(Array.from(sel.options).some(o=>o.value===it.backend))sel.value=it.backend;renderSizeChips();}if(it.refs&&it.refs.length){restoreRefs(it.refs)}else{refs=[];renderRefList();}}
loadConfig().catch(e=>showErr("加载配置失败："+e.message));
</script>
</body>
</html>
"""


def main() -> None:
    ensure_utf8()
    ap = argparse.ArgumentParser(description="洛天依主题生图网页（标准库实现）")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=8766, help="端口（默认 8766）")
    ap.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()
    ensure_wallpaper()
    WEBUI_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"洛天依生图网页已启动：{url}  （按 Ctrl+C 退出）")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()