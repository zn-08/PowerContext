/**
 * public/app.js — 浏览器端应用
 *
 * 端侧推理（Transformers.js / ONNX Runtime Web）：
 *   1. 优先使用 WebGPU（navigator.gpu 存在且后端初始化成功）
 *   2. 任何一步失败都自动降级到 WASM，界面明确提示当前后端与降级原因
 *   3. 模型文件从本机 /models/ 目录加载（离线可用），ORT wasm 同样来自本机 /vendor/ort/
 *
 * 约束：文档向量与查询向量 **全部在本机浏览器计算**，不调用任何云端推理 API。
 *        后端只负责写入 seekdb 并执行混合检索 SQL。
 */
import { pipeline, env } from '/vendor/transformers/transformers.min.js';

/**
 * Transformers.js 模块句柄。
 * 注意：一次失败的 WebGPU 初始化会污染 ONNX Runtime Web 的全局后端注册表，
 * 导致后续 WASM 会话也报同一个 WebGPU 错误。因此 WebGPU 失败后需要
 * 用「带 cache-buster 的重新 import」拿到一个全新的模块实例，才能真正降级到 WASM。
 */
let TF = { pipeline, env };
let moduleSeq = 0;

/** 全局忙碌标志：避免重复提交，同时给自动化脚本一个可靠的完成信号 */
function setBusy(v) {
  window.__appBusy = !!v;
  const importing = !!v && window.__appBusyKind === 'import';
  const searching = !!v && window.__appBusyKind === 'search';
  $('btnImport').disabled = importing || !state.extractor;
  $('btnSearch').disabled = searching || !state.extractor;
  $('btnReset').disabled = !!v;
  $('btnLoadCorpus').disabled = !!v;
}

const $ = (id) => document.getElementById(id);

const state = {
  cfg: null,
  extractor: null,
  device: null,
  dtype: null,
  modelId: null,
  fallbackReason: null,
  modelBytes: null,
  loadMs: null,
  busy: false,
};

/* ------------------------------------------------------------------ *
 * 工具
 * ------------------------------------------------------------------ */
const fmtMs = (v) => (v === null || v === undefined ? '—' : `${Number(v).toFixed(1)} ms`);
const fmtMB = (b) => (b === null || b === undefined ? '—' : `${(b / 1048576).toFixed(2)} MB`);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({ error: 'bad json' }));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

function highlight(text, query) {
  const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  let html = esc(text);
  const terms = String(query || '')
    .split(/[\s,，、]+/)
    .filter((t) => t.length >= 2);
  for (const t of terms) {
    const safe = t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    html = html.replace(new RegExp(`(${safe})`, 'gi'), '<mark>$1</mark>');
  }
  return html;
}

/* ------------------------------------------------------------------ *
 * 端侧模型加载（含 WebGPU → WASM 降级）
 * ------------------------------------------------------------------ */
function configureEnv() {
  // 模型从本机目录加载
  TF.env.allowLocalModels = true;
  TF.env.localModelPath = state.cfg?.modelLocalPath || '/models/';
  TF.env.allowRemoteModels = state.cfg?.allowRemoteModels !== false;
  // ORT 的 wasm 也走本机，避免访问 CDN（离线可用）
  TF.env.backends.onnx.wasm.wasmPaths = '/vendor/ort/';
  TF.env.backends.onnx.wasm.numThreads = 1;
}

/**
 * 探测 WebGPU 是否真的可用。
 * 不能只看 navigator.gpu 是否存在——某些环境（无独显的服务器、无头浏览器、
 * 驱动初始化失败）里 navigator.gpu 存在但 requestAdapter() 返回 null。
 * 若此时仍然尝试 WebGPU，失败的初始化会污染 ORT 的全局后端状态，
 * 导致后续 WASM 降级同样失败（实测复现）。
 */
async function probeWebGPU() {
  if (typeof navigator === 'undefined' || !navigator.gpu) {
    return { ok: false, reason: '浏览器未提供 navigator.gpu（不支持 WebGPU）' };
  }
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) {
      return { ok: false, reason: 'navigator.gpu.requestAdapter() 返回 null（无可用 GPU 适配器）' };
    }
    return { ok: true, reason: null };
  } catch (e) {
    return { ok: false, reason: `requestAdapter() 异常：${String(e && e.message).slice(0, 120)}` };
  }
}

/** 丢弃被污染的 Transformers.js / ORT 模块实例，取一个全新的 */
async function resetTransformersModule() {
  moduleSeq += 1;
  TF = await import(`/vendor/transformers/transformers.min.js?reset=${moduleSeq}`);
  configureEnv();
}

const ONNX_FILE = { q8: 'onnx/model_quantized.onnx', fp16: 'onnx/model_fp16.onnx', fp32: 'onnx/model.onnx' };

async function probeModelSize(dtype, modelId) {
  const file = ONNX_FILE[dtype];
  if (!file) return null;
  const id = modelId || state.cfg.modelId;
  try {
    const r = await fetch(`/models/${id}/${file}`, { method: 'HEAD' });
    const len = r.headers.get('content-length');
    return len ? Number(len) : null;
  } catch {
    return null;
  }
}

function makeProgress() {
  const files = new Map();
  const bar = $('modelBar');
  const text = $('modelProgressText');
  $('modelProgress').classList.remove('hidden');
  return (p) => {
    if (!p) return;
    if (p.status === 'progress' && p.file) {
      files.set(p.file, { loaded: p.loaded || 0, total: p.total || 0 });
      let loaded = 0, total = 0;
      for (const v of files.values()) { loaded += v.loaded; total += v.total; }
      const pct = total ? (loaded / total) * 100 : 0;
      bar.style.width = `${pct.toFixed(1)}%`;
      text.textContent = `下载中 ${pct.toFixed(1)}%  (${fmtMB(loaded)} / ${fmtMB(total)})  ${p.file}`;
    } else if (p.status === 'done') {
      const f = files.get(p.file);
      if (f && f.total) text.textContent = `${p.file} 完成 ${fmtMB(f.total)}`;
    } else if (p.status === 'ready') {
      bar.style.width = '100%';
      text.textContent = '模型就绪';
    } else if (p.status === 'initiate') {
      text.textContent = `准备加载 ${p.file || ''}`;
    }
  };
}

async function loadModel(dtype, modelId) {
  if (state.busy) return;
  state.busy = true;
  $('btnLoadModel').disabled = true;
  $('btnImport').disabled = true;
  $('btnSearch').disabled = true;

  const modelIdToUse = modelId || state.cfg.modelId;
  const gpu = await probeWebGPU();
  // 候选配置按优先级排列，逐个尝试，直到成功
  const candidates = [];
  if (gpu.ok) {
    candidates.push({ device: 'webgpu', dtype });
    if (dtype !== 'fp16') candidates.push({ device: 'webgpu', dtype: 'fp16' });
    if (dtype !== 'fp32') candidates.push({ device: 'webgpu', dtype: 'fp32' });
  }
  candidates.push({ device: 'wasm', dtype });
  if (dtype !== 'q8') candidates.push({ device: 'wasm', dtype: 'q8' });
  candidates.push({ device: 'wasm', dtype: 'fp32' });

  const attempts = [];
  let loaded = null;
  const progress = makeProgress();

  for (const cand of candidates) {
    const t0 = performance.now();
    try {
      loaded = await TF.pipeline('feature-extraction', modelIdToUse, {
        device: cand.device,
        dtype: cand.dtype,
        progress_callback: progress,
      });
      state.device = cand.device;
      state.dtype = cand.dtype;
      state.modelId = modelIdToUse;
      state.loadMs = performance.now() - t0;
      // 若最终后端与用户请求不同，记录降级原因
      const expected = { device: gpu.ok ? 'webgpu' : 'wasm', dtype };
      if (cand.device !== expected.device) {
        state.fallbackReason = gpu.reason || 'WebGPU 不可用，已自动降级到 WASM';
      } else if (cand.dtype !== expected.dtype) {
        state.fallbackReason = `WebGPU 上的 ${dtype.toUpperCase()} 权重不可用，已改用 ${cand.dtype.toUpperCase()}`;
      } else {
        state.fallbackReason = '—（首选后端直接可用）';
      }
      attempts.push({ ...cand, ok: true, ms: Number(state.loadMs.toFixed(1)) });
      break;
    } catch (e) {
      attempts.push({ ...cand, ok: false, error: String(e && e.message).slice(0, 220) });
      // WebGPU 失败会污染 ORT 全局后端状态，必须重置模块才能继续降级
      if (cand.device === 'webgpu') {
        try { await resetTransformersModule(); } catch { /* ignore */ }
      }
    }
  }

  if (!loaded) {
    $('modelProgressText').textContent = '模型加载失败：' + JSON.stringify(attempts);
    $('mFallback').textContent = '全部候选后端均失败';
    state.busy = false;
    $('btnLoadModel').disabled = false;
    return;
  }

  state.extractor = loaded;
  state.modelBytes = await probeModelSize(state.dtype, state.modelId);

  // 界面更新
  const devLabel = state.device === 'webgpu' ? 'WebGPU' : 'WASM';
  const badge = $('badgeDevice');
  badge.className = 'badge ' + (state.device === 'webgpu' ? 'ok' : 'warn');
  $('deviceValue').textContent = devLabel;
  $('dtypeValue').textContent = state.dtype.toUpperCase();
  $('modelValue').textContent = state.modelId.split('/').pop();
  $('mDevice').textContent = devLabel;
  $('mFallback').textContent = state.fallbackReason || '—';
  $('mSource').textContent = `HuggingFace ${state.modelId}（本机 /models/ 目录）`;
  $('mQuant').textContent = {
    q8: 'INT8 动态量化（onnx/model_quantized.onnx）',
    fp16: 'FP16 半精度（onnx/model_fp16.onnx）',
    fp32: 'FP32 未量化基线（onnx/model.onnx）',
  }[state.dtype] || state.dtype;
  $('mSize').textContent = fmtMB(state.modelBytes);
  $('mLoadMs').textContent = fmtMs(state.loadMs);
  $('mDim').textContent = String(state.cfg.dim);
  $('modelProgressText').textContent = `模型就绪（${devLabel} · ${state.dtype.toUpperCase()}），加载耗时 ${fmtMs(state.loadMs)}`;

  state.busy = false;
  $('btnLoadModel').disabled = false;
  $('btnImport').disabled = false;
  $('btnSearch').disabled = false;
}

/** 把 SQL 里内联的 384 维向量缩写成 [v0, v1, ...]，便于阅读 */
function compactSql(sql) {
  return String(sql || '').replace(/\[[-\d.eE,\s]{200,}\]/g, (m) => {
    const parts = m.slice(1, -1).split(',');
    if (parts.length < 8) return m;
    return `[${parts[0].trim()}, ${parts[1].trim()}, … , ${parts[parts.length - 1].trim()}  /* 共 ${parts.length} 维 */]`;
  });
}

/** 在浏览器本机计算 embedding（mean pooling + L2 归一化） */
async function embed(texts) {
  const t0 = performance.now();
  const out = await state.extractor(texts, { pooling: 'mean', normalize: true });
  const dims = out.dims;
  const data = out.data;
  const n = dims[0];
  const dim = dims[dims.length - 1];
  const vecs = [];
  for (let i = 0; i < n; i++) {
    vecs.push(Array.from(data.slice(i * dim, (i + 1) * dim)));
  }
  return { vecs, ms: performance.now() - t0, dim };
}

/* ------------------------------------------------------------------ *
 * 导入
 * ------------------------------------------------------------------ */
function parseDocs(text) {
  const docs = [];
  let idx = 0;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    idx += 1;
    const m = line.match(/^([^|]{1,200})\s*\|\s*(.+)$/);
    const title = m ? m[1].trim() : line.slice(0, 60);
    const body = m ? m[2].trim() : line;
    docs.push({
      id: `doc_${Date.now().toString(36)}_${idx}`,
      title,
      text: body,
      source: 'manual',
      meta: { idx },
    });
  }
  return docs;
}

async function doImport() {
  if (!state.extractor) { alert('请先加载模型'); return; }
  const docs = parseDocs($('docInput').value);
  if (!docs.length) { alert('没有可导入的文档'); return; }

  window.__appBusyKind = 'import';
  setBusy(true);
  try {
    const { vecs, ms: embedMs, dim } = await embed(docs.map((d) => `${d.title}\n${d.text}`));
    if (dim !== state.cfg.dim) throw new Error(`向量维度 ${dim} 与数据库 ${state.cfg.dim} 不一致`);

    const r = await api('/api/import', {
      method: 'POST',
      body: {
        docs: docs.map((d) => ({ id: d.id, title: d.title, text: d.text, source: d.source, meta: d.meta })),
        vectors: vecs,
        device: state.device,
        dtype: state.dtype,
      },
    });

    $('iCount').textContent = String(docs.length);
    $('iEmbedMs').textContent = `${fmtMs(embedMs)}`;
    $('iThroughput').textContent = `${(docs.length / (embedMs / 1000)).toFixed(1)} 篇/秒`;
    $('iWriteMs').textContent = fmtMs(r.timings.writeMs);
    $('iRefreshMs').textContent = fmtMs(r.timings.refreshMs);
    $('iTotal').textContent = String(r.docCount);
    await refreshStatus();
  } catch (e) {
    alert('导入失败：' + e.message);
  } finally {
    window.__appBusyKind = null;
    setBusy(false);
  }
}

/* ------------------------------------------------------------------ *
 * 检索
 * ------------------------------------------------------------------ */
async function doSearch() {
  if (!state.extractor) { alert('请先加载模型'); return; }
  const query = $('queryInput').value.trim();
  if (!query) return;
  const topK = Number($('topK').value);
  const mode = $('modeSelect').value;

  const t0 = performance.now();
  window.__appBusyKind = 'search';
  setBusy(true);
  try {
    const { vecs, ms: embedMs } = await embed([query]);
    const r = await api('/api/search', {
      method: 'POST',
      body: { query, vector: vecs[0], topK, mode },
    });

    renderResults(r.rows, query);
    $('sqlBox').textContent = compactSql(r.sql) || '（本次未返回 SQL）';
    $('sqlNote').textContent = mode === 'native'
      ? '当前模式：OceanBase 原生 DBMS_HYBRID_SEARCH——由数据库生成并执行一条同时包含「全文候选」与「向量候选」的 SQL，RRF 融合在数据库内完成。'
      : '当前模式：手写单条 RRF 混合检索 SQL——一条 SQL 内同时计算全文候选与向量候选并做 RRF 融合。';

    // 端到端：提交 -> 结果渲染完成
    requestAnimationFrame(() => {
      const e2e = performance.now() - t0;
      $('qEmbedMs').textContent = fmtMs(embedMs);
      $('qDbMs').textContent = fmtMs(r.timings.dbMs);
      $('qE2eMs').textContent = fmtMs(e2e);
      const both = r.rows.filter((x) => x.hit_by === 'both').length;
      $('qHit').textContent = `both ${both} / vector ${r.rows.filter((x) => x.hit_by === 'vector').length} / fulltext ${r.rows.filter((x) => x.hit_by === 'fulltext').length}`;
    });
  } catch (e) {
    alert('检索失败：' + e.message);
  } finally {
    window.__appBusyKind = null;
    setBusy(false);
  }
}

function renderResults(rows, query) {
  const box = $('results');
  if (!rows || !rows.length) {
    box.innerHTML = '<p class="empty">没有命中任何文档。</p>';
    return;
  }
  box.innerHTML = rows
    .map((r, i) => {
      const hitTag = r.hit_by ? `<span class="tag ${r.hit_by}">${r.hit_by}</span>` : '';
      const score = r.score !== null && r.score !== undefined ? Number(r.score).toFixed(5) : '—';
      return `
      <article class="result">
        <div class="head">
          <span class="rank">${i + 1}</span>
          <span class="title">${highlight(r.title, query)}</span>
          ${hitTag}
          <span class="meta">score=${score} · vec_rank=${r.vec_rank ?? '—'} · ft_rank=${r.ft_rank ?? '—'}</span>
        </div>
        <p class="snippet">${highlight(String(r.snippet || '').replace(/\n/g, ' '), query)}</p>
        <div class="meta" style="margin:6px 0 0">id=${r.id} · vec_dist=${r.vec_dist ?? '—'} · ft_score=${r.ft_score ?? '—'}</div>
      </article>`;
    })
    .join('');
}

/* ------------------------------------------------------------------ *
 * 状态
 * ------------------------------------------------------------------ */
async function refreshStatus() {
  try {
    const s = await api('/api/status');
    $('sConn').textContent = s.db.connected ? `已连接 ${s.db.host}:${s.db.port}` : '未连接';
    $('sVersion').textContent = s.db.version;
    $('sDb').textContent = `${s.db.database} / ${s.db.table}`;
    $('sCount').textContent = String(s.db.docCount);
    $('sSession').textContent = s.session.id;
    if ($('iTotal').textContent === '—') $('iTotal').textContent = String(s.db.docCount);
  } catch (e) {
    $('sConn').textContent = '连接失败：' + e.message;
  }
}

/* ------------------------------------------------------------------ *
 * 事件绑定与启动
 * ------------------------------------------------------------------ */
function bind() {
  $('btnLoadModel').addEventListener('click', () => loadModel($('dtypeSelect').value, $('modelSelect').value));
  $('modelSelect').addEventListener('change', () => {
    // 切换模型时禁用该模型未下载的量化档位
    const id = $('modelSelect').value;
    const m = (state.cfg.availableModels || []).find((x) => x.id === id);
    for (const opt of $('dtypeSelect').options) {
      opt.disabled = !!(m && !m.sizes[opt.value]);
    }
    if ($('dtypeSelect').selectedOptions[0]?.disabled) {
      const firstOk = Array.from($('dtypeSelect').options).find((o) => !o.disabled);
      if (firstOk) $('dtypeSelect').value = firstOk.value;
    }
  });
  $('btnImport').addEventListener('click', doImport);
  $('btnSearch').addEventListener('click', doSearch);
  $('queryInput').addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });

  $('btnLoadCorpus').addEventListener('click', async () => {
    const r = await fetch('/data/corpus.json');
    const corpus = await r.json();
    $('docInput').value = corpus.docs.map((d) => `${d.title} | ${d.text}`).join('\n');
  });
  $('btnClearCorpus').addEventListener('click', () => { $('docInput').value = ''; });

  $('btnReset').addEventListener('click', async () => {
    if (!confirm('确认清空 seekdb 中的全部文档？')) return;
    window.__appBusyKind = 'import';
    setBusy(true);
    try {
      await api('/api/reset', { method: 'POST' });
      await refreshStatus();
      $('iTotal').textContent = '0';
      $('results').innerHTML = '<p class="empty">集合已清空。</p>';
    } finally {
      window.__appBusyKind = null;
      setBusy(false);
    }
  });

  $('btnRefreshIdx').addEventListener('click', async () => {
    const r = await api('/api/refresh', { method: 'POST' });
    $('iRefreshMs').textContent = `刷新完成（${r.refreshed}），文档数 ${r.docCount}`;
    await refreshStatus();
  });

  $('btnExplain').addEventListener('click', async () => {
    const query = $('queryInput').value.trim() || '向量检索';
    let vector = null;
    if (state.extractor) {
      const { vecs } = await embed([query]);
      vector = vecs[0];
    }
    const r = await api('/api/explain', {
      method: 'POST',
      body: { query, vector, topK: Number($('topK').value), mode: $('modeSelect').value },
    });
    $('planBox').textContent = (r.plan || [])
      .map((row) => row['Query Plan'] ?? Object.values(row).join(' '))
      .join('\n');
    document.querySelector('.collapsible').classList.remove('closed');
  });

  document.querySelectorAll('.chip').forEach((c) => {
    c.addEventListener('click', () => { $('queryInput').value = c.dataset.q; doSearch(); });
  });

  document.querySelector('.collapsible h2.toggle').addEventListener('click', () => {
    document.querySelector('.collapsible').classList.toggle('closed');
  });
}

async function boot() {
  bind();
  state.cfg = await api('/api/config');
  $('dtypeSelect').value = state.cfg.dtype;
  $('modelValue').textContent = state.cfg.modelId.split('/').pop();
  $('mSource').textContent = `HuggingFace ${state.cfg.modelId}（本机 /models/ 目录）`;
  $('mDim').textContent = String(state.cfg.dim);

  // 填充本地可用模型
  const sel = $('modelSelect');
  const models = state.cfg.availableModels || [];
  sel.innerHTML = models.length
    ? models.map((m) => {
        const sizes = Object.entries(m.sizes)
          .map(([k, v]) => `${k.toUpperCase()} ${(v / 1048576).toFixed(1)}MB`)
          .join(' / ');
        return `<option value="${m.id}" ${m.id === state.cfg.modelId ? 'selected' : ''}>${m.id} — ${sizes}</option>`;
      }).join('')
    : `<option value="${state.cfg.modelId}">${state.cfg.modelId}</option>`;
  sel.dispatchEvent(new Event('change'));

  await refreshStatus();

  // 自动加载模型（首选后端失败会自动降级）
  configureEnv();
  await loadModel(state.cfg.dtype, sel.value);
}

boot().catch((e) => {
  $('modelProgressText').textContent = '初始化失败：' + e.message;
  console.error(e);
});
