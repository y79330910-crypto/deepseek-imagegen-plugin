"use strict";

/* ============ API ============ */

async function apiRequest(method, path, body) {
  const opts = { method, headers: {} };
  if (body instanceof FormData) {
    opts.body = body; // fetch 自动设置 multipart boundary
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, opts);
  } catch (err) {
    throw new Error("无法连接 ImageGen 服务：" + err.message);
  }
  let data = null;
  const text = await resp.text();
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    data = null;
  }
  if (!resp.ok) {
    const payload = data && data.error;
    const err = new Error(
      (payload && payload.message) || "请求失败（HTTP " + resp.status + "）"
    );
    err.type = (payload && payload.type) || "unknown";
    throw err;
  }
  return data;
}

const api = {
  generate: (req) => apiRequest("POST", "/api/v1/generate", req),
  uploadAsset: (file) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", "reference");
    return apiRequest("POST", "/api/v1/assets", fd);
  },
  importAsset: (path) =>
    apiRequest("POST", "/api/v1/assets/import", { path, kind: "reference" }),
  listAssets: (kind, q, limit, offset) => {
    const params = new URLSearchParams();
    if (kind) params.set("kind", kind);
    if (q) params.set("q", q);
    if (limit) params.set("limit", String(limit));
    if (offset) params.set("offset", String(offset));
    return apiRequest("GET", "/api/v1/assets?" + params.toString());
  },
  getConfig: () => apiRequest("GET", "/api/v1/config"),
  updateConfig: (patch) => apiRequest("PATCH", "/api/v1/config", patch),
  listBackends: () => apiRequest("GET", "/api/v1/backends"),
  getBackend: (id) => apiRequest("GET", "/api/v1/backends/" + encodeURIComponent(id)),
  listModels: (id) =>
    apiRequest("GET", "/api/v1/backends/" + encodeURIComponent(id) + "/models"),
  doctor: () => apiRequest("POST", "/api/v1/doctor"),
  listHistory: (q, limit, offset) => {
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (limit) params.set("limit", String(limit));
    if (offset) params.set("offset", String(offset));
    return apiRequest("GET", "/api/v1/history?" + params.toString());
  },
  deleteHistory: (id) =>
    apiRequest("DELETE", "/api/v1/history/" + encodeURIComponent(id)),
};

/* ============ State ============ */

const state = {
  backends: [],
  backend: "vertex",
  models: [],
  config: {},
  history: [],
  references: [],
  libItems: [],
};

const REF_ROLES = ["auto", "character", "outfit", "style", "scene", "composition", "pose", "object"];
const REF_ROLE_LABELS = {
  auto: "自动识别",
  character: "角色人物",
  outfit: "服装造型",
  style: "艺术风格",
  scene: "场景背景",
  composition: "构图布局",
  pose: "姿势动作",
  object: "物品产品",
};
const MAX_REFS = 4;

const SIZE_PRESETS = {
  vertex: ["1024x1024", "768x1408", "1408x768", "1536x1024"],
  openai: ["1254x1254", "1536x1024", "1024x1536"],
  "4k超分": ["2048x2048", "2560x1440", "3840x2160", "2160x3840", "3696x1584"],
  "原生4k": ["2048x2048", "3840x2160", "2160x3840"],
};

const $ = (id) => document.getElementById(id);

function backendCapabilities() {
  const entry = state.backends.find((b) => b.id === state.backend) || {};
  return entry.capabilities || {};
}

/* ============ Render ============ */

function showError(msg) {
  const el = $("error");
  el.textContent = msg;
  el.style.display = "block";
}

function hideError() {
  $("error").style.display = "none";
}

function setStatus(text) {
  $("status").textContent = text || "";
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => {
    t.classList.toggle("on", t.dataset.tab === name);
  });
  ["generate", "settings", "diagnose", "gallery"].forEach((p) => {
    $("page-" + p).style.display = p === name ? "" : "none";
  });
  if (name === "gallery") renderGallery();
}

function renderBackendSelect() {
  const sel = $("backend");
  const cur = state.backend;
  const names = state.backends.map((b) => b.id);
  sel.innerHTML =
    '<option value="vertex">本地 Vertex（默认）</option>' +
    names
      .filter((n) => n !== "vertex")
      .map((n) => '<option value="' + esc(n) + '">' + esc(n) + "</option>")
      .join("");
  sel.value = names.includes(cur) ? cur : "vertex";
  state.backend = sel.value;
  renderBackendFilter();
}

function renderBackendFilter() {
  const names = state.backends.map((b) => b.id);
  const flt = $("galFilter");
  const fcur = flt.value;
  flt.innerHTML =
    '<option value="">全部后端</option>' +
    names.map((n) => '<option value="' + esc(n) + '">' + esc(n) + "</option>").join("");
  flt.value = names.includes(fcur) ? fcur : "";
}

function renderModelSelect() {
  const sel = $("model");
  const cur = sel.value;
  const list = state.models || [];
  sel.innerHTML =
    '<option value="">自动（推荐）</option>' +
    [...new Set(list)]
      .map((m) => '<option value="' + esc(m) + '">' + esc(m) + "</option>")
      .join("");
  if (list.includes(cur)) sel.value = cur;
  else sel.value = "";
  renderSizeChips();
  renderQualityHint();
}

function renderSizeChips() {
  const chips = $("sizeChips");
  const preset = presetFor(state.backend, $("model").value.trim());
  chips.innerHTML = "";
  preset.forEach((s) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn ghost small";
    b.style.marginRight = "6px";
    b.style.marginBottom = "6px";
    b.textContent = s;
    b.onclick = () => {
      $("size").value = s;
    };
    chips.appendChild(b);
  });
}

function presetFor(backend, model) {
  const m = (model || "").toLowerCase();
  if (m.includes("原生4k")) return SIZE_PRESETS["原生4k"];
  if (m.includes("4k超分")) return SIZE_PRESETS["4k超分"];
  if (backend !== "vertex") return SIZE_PRESETS.openai;
  return SIZE_PRESETS.vertex;
}

function renderQualityHint() {
  const caps = backendCapabilities();
  const quality = $("quality");
  const hint = $("qualityHint");
  if (state.backend !== "vertex" && caps.quality === false) {
    quality.disabled = true;
    hint.textContent = "当前后端不支持质量参数";
  } else {
    quality.disabled = false;
    hint.textContent = "";
  }
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/* ============ Config form ============ */

function getPath(obj, path) {
  let v = obj;
  for (const k of path.split(".")) {
    if (v == null) return undefined;
    v = v[k];
  }
  return v;
}

function setPath(obj, path, value) {
  const keys = path.split(".");
  let t = obj;
  for (let i = 0; i < keys.length - 1; i++) {
    if (t[keys[i]] == null || typeof t[keys[i]] !== "object") t[keys[i]] = {};
    t = t[keys[i]];
  }
  t[keys[keys.length - 1]] = value;
}

function normalizeSizePolicy(value) {
  const raw = String(value || "auto");
  if (raw === "strict") return "aspect";
  if (raw === "warn") return "auto";
  return raw;
}

function renderSettings() {
  document.querySelectorAll("[data-path]").forEach((el) => {
    let v = getPath(state.config, el.dataset.path);
    if (el.dataset.path === "size_policy.mode") v = normalizeSizePolicy(v);
    if (v === undefined) return;
    if (el.tagName === "SELECT") el.value = String(v);
    else if (el.type === "checkbox") el.checked = !!v;
    else el.value = Array.isArray(v) ? v.join(", ") : String(v);
  });
}

function collectPatch() {
  const patch = {};
  document.querySelectorAll("[data-path]").forEach((el) => {
    let v;
    if (el.tagName === "SELECT") v = el.value;
    else if (el.type === "checkbox") v = el.checked;
    else v = el.value.trim();
    if (v === "") return;
    setPath(patch, el.dataset.path, v);
  });
  return patch;
}

/* ============ Generate ============ */

function renderRefPanel() {
  const list = $("refList");
  list.innerHTML = "";
  state.references.forEach((ref, idx) => {
    const card = document.createElement("div");
    card.className = "refcard" + (ref.status === "failed" ? " failed" : "");
    const roleOptions = REF_ROLES.map(
      (r) =>
        '<option value="' + r + '"' + (ref.role === r ? " selected" : "") + ">" +
        REF_ROLE_LABELS[r] + "</option>"
    ).join("");
    card.innerHTML =
      '<div class="refthumb"><img src="' + esc(ref.content_url) +
      '" alt="' + esc(ref.original_name || "") + '"></div>' +
      '<div class="refbody">' +
      '<div class="refname">' + esc(ref.original_name || "") + "</div>" +
      '<div class="row"><select class="refrole">' + roleOptions + "</select>" +
      '<button class="btn ghost small refdel" type="button">×</button></div>' +
      (ref.status === "uploading"
        ? '<div class="refstatus">上传中…</div>'
        : ref.status === "failed"
          ? '<div class="refstatus fail">上传失败</div>'
          : '<div class="refstatus">已就绪</div>') +
      "</div>";
    card.querySelector(".refrole").onchange = (e) => {
      ref.role = e.target.value;
    };
    card.querySelector(".refdel").onclick = () => {
      state.references.splice(idx, 1);
      renderRefPanel();
    };
    list.appendChild(card);
  });
  $("refCount").textContent = state.references.length + " / " + MAX_REFS;
}

async function addFiles(files) {
  const list = Array.from(files || []);
  for (const file of list) {
    if (state.references.length >= MAX_REFS) {
      showError("参考图最多 " + MAX_REFS + " 张");
      break;
    }
    if (!/^image\/(png|jpeg|jpg|webp|gif)$/.test(file.type || "")) {
      showError("仅支持 PNG / JPEG / WebP / GIF 图片：" + (file.name || "未知文件"));
      continue;
    }
    const item = {
      asset_id: "",
      content_url: "",
      original_name: file.name || "image",
      role: "auto",
      status: "queued",
    };
    state.references.push(item);
    renderRefPanel();
    item.status = "uploading";
    renderRefPanel();
    try {
      const asset = await api.uploadAsset(file);
      item.asset_id = asset.asset_id;
      item.content_url = asset.content_url;
      item.original_name = asset.original_name || item.original_name;
      item.status = "ready";
    } catch (err) {
      item.status = "failed";
      showError("上传失败：" + err.message);
    }
    renderRefPanel();
  }
}

let libSelection = new Set();

async function loadLibrary(q) {
  try {
    const data = await api.listAssets("reference", q, 50, 0);
    state.libItems = data.items || [];
  } catch (err) {
    state.libItems = [];
    showError("素材库加载失败：" + err.message);
  }
  renderLibrary();
}

function renderLibrary() {
  const grid = $("libGrid");
  grid.innerHTML = "";
  const items = state.libItems.filter(
    (it) => !state.references.some((r) => r.asset_id === it.asset_id)
  );
  if (!items.length) {
    grid.innerHTML = '<div class="hint">素材库为空，先上传或导入图片吧。</div>';
    return;
  }
  items.forEach((it) => {
    const dims = it.width && it.height ? it.width + "×" + it.height : "";
    const card = document.createElement("div");
    card.className = "gcard libcard" + (libSelection.has(it.asset_id) ? " selected" : "");
    card.innerHTML =
      '<img src="' + esc(it.content_url) + '" alt="' + esc(it.original_name || "") + '">' +
      '<div class="gbody"><div class="gp">' + esc(it.original_name || "") +
      '</div><div class="gm">' + esc(dims) + "</div></div>";
    card.onclick = () => {
      if (libSelection.has(it.asset_id)) {
        libSelection.delete(it.asset_id);
      } else {
        if (libSelection.size + state.references.length >= MAX_REFS) {
          showError("参考图最多 " + MAX_REFS + " 张");
          return;
        }
        libSelection.add(it.asset_id);
      }
      renderLibrary();
    };
    grid.appendChild(card);
  });
}

function openLibrary() {
  libSelection = new Set();
  $("libModal").style.display = "flex";
  $("libSearch").value = "";
  loadLibrary("");
}

function addSelectedFromLibrary() {
  let added = 0;
  for (const asset of state.libItems) {
    if (!libSelection.has(asset.asset_id)) continue;
    if (state.references.length >= MAX_REFS) {
      showError("参考图最多 " + MAX_REFS + " 张");
      break;
    }
    state.references.push({
      asset_id: asset.asset_id,
      content_url: asset.content_url,
      original_name: asset.original_name || "",
      role: "auto",
      status: "ready",
    });
    added += 1;
  }
  libSelection = new Set();
  $("libModal").style.display = "none";
  if (added) renderRefPanel();
}

function buildGenerateRequest() {
  const refs = state.references
    .filter((r) => r.status === "ready" && r.asset_id)
    .slice(0, MAX_REFS);
  const sizePolicyRaw =
    (state.config.size_policy && state.config.size_policy.mode) || "auto";
  const seedRaw = $("seed").value.trim();
  const lib = $("library").value;
  return {
    prompt: $("prompt").value.trim(),
    size: $("size").value.trim(),
    backend: state.backend,
    model: $("model").value,
    seed: seedRaw ? Number(seedRaw) : null,
    quality: $("quality").value,
    composition: $("composition").value,
    translator: $("translator").value,
    size_policy: normalizeSizePolicy(sizePolicyRaw),
    references: refs.map((r) => ({ asset_id: r.asset_id, role: r.role || "auto" })),
    images: [],
    reference_roles: [],
    ref_type: "auto",
    library_enabled: lib === "auto" ? null : lib === "on",
  };
}

function renderResult(res) {
  const enc = encodeURIComponent(res.output_url);
  $("resultImg").src = res.output_url;
  $("dlLink").href = res.output_url;
  const ref = res.reference || {};
  const tr = res.translator || {};
  const warns = res.warnings || [];
  let info =
    "<b>后端：</b>" + esc(res.backend || "vertex") +
    " · <b>图像模型：</b>" + esc(res.image_model_used || "自动") +
    " · <b>文件：</b>" + esc(res.path) +
    "<br><b>种子：</b>" + esc(res.seed) +
    "<br><b>尺寸：</b>请求 " + esc(res.size) + " → 实际 " + esc(res.actual_size) +
    " " + (res.size_match ? "✓" : "✗") + "<br>";
  if (res.composition_preset && res.composition_preset !== "auto") {
    info += "<b>构图：</b>" + esc(res.composition_preset) + "<br>";
  }
  if (ref.type) {
    info +=
      "<b>参考图类型：</b>" + esc(ref.label || ref.type) +
      "（" + esc(ref.method || "") + "）<br>";
  }
  if (tr.engine_used && tr.engine_used !== "off") {
    info += "<b>翻译官：</b>" + esc(tr.engine_used) +
      (tr.fallback ? "（已自动降级）" : "") + "<br>";
  }
  if (res.mirror_path) info += "<b>镜像副本：</b>" + esc(res.mirror_path) + "<br>";
  if (res.prompt_used) {
    info +=
      '<details><summary style="cursor:pointer"><b>实际生效提示词</b></summary>' +
      esc(res.prompt_used) + "</details>";
  }
  if (warns.length) info += '<br><span class="warn">提示：' + esc(warns.join("；")) + "</span>";
  $("resultInfo").innerHTML = info;
  $("mirrorNote").textContent = res.mirror_path ? "已自动备份一份到镜像目录" : "";
  $("result").style.display = "block";
}

function addStripCard(res) {
  const strip = $("resultStrip");
  const card = document.createElement("div");
  card.className = "gcard";
  card.innerHTML =
    '<img src="' + esc(res.output_url) + '" alt="结果">' +
    '<div class="gbody"><div class="gm">' +
    esc(res.actual_size || "") + " · " + esc(res.backend || "") +
    '</div><a class="btn ghost small" href="' + esc(res.output_url) +
    '" download="result.png">下载</a></div>';
  card.querySelector("img").onclick = () => window.open(res.output_url);
  strip.appendChild(card);
}

async function handleGenerate() {
  const prompt = $("prompt").value.trim();
  if (!prompt) {
    showError("请先输入提示词");
    return;
  }
  const count = Math.min(10, Math.max(1, parseInt($("count").value || "1", 10) || 1));
  hideError();
  $("result").style.display = "none";
  $("resultStrip").innerHTML = "";
  $("genBtn").disabled = true;
  let firstRes = null;
  const errs = [];
  try {
    for (let i = 1; i <= count; i++) {
      setStatus("⏳ 正在生成第 " + i + "/" + count + " 张，请耐心等待…");
      const req = buildGenerateRequest();
      if (i > 1) req.seed = null;
      try {
        const res = await api.generate(req);
        if (i === 1) {
          firstRes = res;
          renderResult(res);
        }
        addStripCard(res);
      } catch (err) {
        errs.push("第 " + i + " 张失败：" + err.message);
      }
    }
    if (count > 1) {
      setStatus(
        errs.length
          ? "⚠ 生成完成，成功 " + (count - errs.length) + " 张，失败 " + errs.length + " 张"
          : "✅ 全部生成完成（共 " + count + " 张）"
      );
      if (errs.length) showError(errs.join("\n"));
    } else {
      setStatus(errs.length ? "" : "✅ 生成完成");
      if (errs.length) showError(errs.join("\n"));
    }
    await loadHistory();
  } catch (err) {
    showError(err.message);
    setStatus("");
  } finally {
    $("genBtn").disabled = false;
  }
}

/* ============ Doctor ============ */

async function handleDoctor() {
  const btn = $("doctorBtn");
  const box = $("doctorResult");
  btn.disabled = true;
  $("doctorStatus").textContent = "运行中…";
  box.innerHTML = "";
  try {
    const result = await api.doctor();
    $("doctorStatus").textContent = result.message || "";
    let html =
      '<div class="hint">配置文件：' + esc(result.config_file || "") +
      "（" + (result.config_exists ? "存在" : "不存在，使用默认配置") + "）</div>";
    if (result.probes) {
      html += '<div class="hint">尺寸探针：</div>';
      for (const p of result.probes || []) {
        html +=
          '<div class="check"><span class="mono">' + esc(p.requested || "") + "</span> " +
          "文生图=" + esc(p.generations || "—") +
          " 画布优先=" + esc(p.canvas_first || "—") +
          " → " + esc(p.verdict || "") + "</div>";
      }
    }
    for (const c of result.checks || []) {
      const ok = !!c.ok;
      html +=
        '<div class="check"><span class="' + (ok ? "ok" : "fail") + '">' +
        (ok ? "OK" : "FAIL") + "</span> <b>" + esc(c.backend || "") + "</b> " +
        esc(c.message || "") +
        (c.best_model ? "（最佳模型：" + esc(c.best_model) + "，共 " + esc(c.model_count) + " 个）" : "") +
        "</div>";
    }
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = '<div class="check"><span class="fail">FAIL</span> ' + esc(err.message) + "</div>";
    $("doctorStatus").textContent = "";
  } finally {
    btn.disabled = false;
  }
}

/* ============ Gallery (persistent) ============ */

async function loadHistory(q) {
  try {
    const data = await api.listHistory(q, 50, 0);
    state.history = data.items || [];
  } catch (err) {
    state.history = [];
    showError("历史加载失败：" + err.message);
  }
  renderGallery();
}

function renderGallery() {
  const fb = $("galFilter").value;
  let items = state.history;
  if (fb) items = items.filter((it) => (it.backend || "vertex") === fb);
  const gal = $("gal");
  gal.innerHTML = "";
  if (!items.length) {
    $("galEmpty").style.display = "block";
    return;
  }
  $("galEmpty").style.display = "none";
  items.forEach((it) => {
    const card = document.createElement("div");
    card.className = "gcard";
    card.innerHTML =
      '<img src="' + esc(it.output_url) + '" alt="缩略图" ' +
      'onerror="this.onerror=null;this.src=\'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7\';this.alt=\'文件已不存在\'">' +
      '<div class="gbody"><div class="gp">' + esc(it.prompt || "") + "</div>" +
      '<div class="gm">' + esc(it.backend || "vertex") + " · 种子 " + esc(it.seed ?? "-") +
      " · " + esc(it.requested_size || "") + " → " + esc(it.actual_size || "") + "</div>" +
      (it.prompt_used
        ? '<details class="gm"><summary style="cursor:pointer">生效提示词（点击展开）</summary>' +
          '<div class="detail-prompt">' + esc(it.prompt_used) +
          '</div><button class="btn ghost small" data-act="copy" type="button">复制提示词</button></details>'
        : "") +
      '<div class="gbtn"><button class="btn ghost small" data-act="fill" type="button">回填提示词</button>' +
      '<button class="btn ghost small" data-act="del" type="button">删除记录</button>' +
      '<a class="btn ghost small" href="' + esc(it.output_url) + '" download="result.png">下载</a></div></div>';
    card.querySelector("img").onclick = () => window.open(it.output_url);
    card.querySelector('[data-act="fill"]').onclick = () => {
      $("prompt").value = it.prompt || "";
      switchTab("generate");
    };
    const delBtn = card.querySelector('[data-act="del"]');
    delBtn.onclick = async () => {
      if (!confirm("删除这条生成记录？图片文件不会受影响。")) return;
      try {
        await api.deleteHistory(it.generation_id);
        state.history = state.history.filter((x) => x.generation_id !== it.generation_id);
        renderGallery();
      } catch (err) {
        showError("删除失败：" + err.message);
      }
    };
    const cp = card.querySelector('[data-act="copy"]');
    if (cp) {
      cp.onclick = async () => {
        try {
          await navigator.clipboard.writeText(it.prompt_used || "");
        } catch (err) {
          const ta = document.createElement("textarea");
          ta.value = it.prompt_used || "";
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          ta.remove();
        }
        cp.textContent = "已复制";
        setTimeout(() => (cp.textContent = "复制提示词"), 1500);
      };
    }
    gal.appendChild(card);
  });
}

/* ============ Events / Init ============ */

async function loadModels() {
  try {
    const data = await api.listModels(state.backend);
    state.models = data.models || [];
  } catch (err) {
    state.models = [];
    showError("模型列表加载失败：" + err.message);
  }
  renderModelSelect();
}

async function init() {
  try {
    const cfg = await api.getConfig();
    state.config = cfg.config || {};
  } catch (err) {
    showError("配置加载失败：" + err.message);
  }
  try {
    const data = await api.listBackends();
    state.backends = data.backends || [];
  } catch (err) {
    showError("后端列表加载失败：" + err.message);
  }
  renderBackendSelect();
  await loadModels();
  renderSettings();
  await loadHistory();
}

document.querySelectorAll(".tab").forEach((t) => {
  t.onclick = () => switchTab(t.dataset.tab);
});

$("backend").onchange = () => {
  state.backend = $("backend").value;
  loadModels();
};
$("model").onchange = renderSizeChips;
$("genBtn").onclick = handleGenerate;
$("refPickBtn").onclick = () => $("refFile").click();
$("refFile").onchange = (e) => {
  addFiles(e.target.files);
  e.target.value = "";
};
$("refLibBtn").onclick = openLibrary;
$("libClose").onclick = () => ($("libModal").style.display = "none");
$("libAddBtn").onclick = addSelectedFromLibrary;
$("libSearch").oninput = () => loadLibrary($("libSearch").value.trim());
$("importBtn").onclick = async () => {
  const path = $("importPath").value.trim();
  if (!path) {
    showError("请输入服务器本机图片路径");
    return;
  }
  if (state.references.length >= MAX_REFS) {
    showError("参考图最多 " + MAX_REFS + " 张");
    return;
  }
  try {
    const asset = await api.importAsset(path);
    state.references.push({
      asset_id: asset.asset_id,
      content_url: asset.content_url,
      original_name: asset.original_name || path,
      role: "auto",
      status: "ready",
    });
    $("importPath").value = "";
    renderRefPanel();
  } catch (err) {
    showError("导入失败：" + err.message);
  }
};
const refDrop = $("refDrop");
["dragenter", "dragover"].forEach((evt) => {
  refDrop.addEventListener(evt, (e) => {
    e.preventDefault();
    refDrop.classList.add("drag");
  });
});
refDrop.addEventListener("dragleave", (e) => {
  e.preventDefault();
  refDrop.classList.remove("drag");
});
refDrop.addEventListener("drop", (e) => {
  e.preventDefault();
  refDrop.classList.remove("drag");
  addFiles(e.dataTransfer && e.dataTransfer.files);
});
document.addEventListener("paste", (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  const files = [];
  for (const item of items) {
    if (item.kind === "file" && item.type && item.type.startsWith("image/")) {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (files.length) {
    e.preventDefault();
    addFiles(files);
  }
});
$("saveBtn").onclick = async () => {
  hideError();
  try {
    const result = await api.updateConfig(collectPatch());
    state.config = result.config || {};
    $("saveChip").style.display = "inline";
    setTimeout(() => ($("saveChip").style.display = "none"), 2500);
    renderSettings();
  } catch (err) {
    showError("保存失败：" + err.message);
  }
};
$("doctorBtn").onclick = handleDoctor;
$("galSearch").oninput = () => loadHistory($("galSearch").value.trim());
$("galFilter").onchange = renderGallery;
$("galClear").onclick = () => loadHistory($("galSearch").value.trim());

init().catch((err) => showError("初始化失败：" + err.message));
