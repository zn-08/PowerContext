/* 前端逻辑：全部通过 /api 与后端交互，检索与记忆都在 seekdb 里完成 */

// 分词规则和后端同源：高亮出来的是真正参与打分的词，不是前端自己猜的词
import { tokenize } from '/tokenize.js';

const $ = (id) => document.getElementById(id);

const HIT_LABEL = { both: '全文+向量', fulltext: '全文命中', vector: '向量命中' };

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const payload = await response.json();
  if (!payload.ok) throw new Error(payload.error || '请求失败');
  return payload;
}

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

/**
 * 提问切成高亮用的 token。
 * 中文会同时产出单字和二元组，只保留更长的，否则标记层层嵌套。
 */
function queryTokens(query) {
  const tokens = [...new Set(tokenize(query))].sort((a, b) => b.length - a.length);
  return tokens.filter((t) => !tokens.some((longer) => longer.length > t.length && longer.includes(t)));
}

/** 按命中位置包上 <mark>，重叠区间先合并，保证标签不嵌套 */
function highlight(text, tokens) {
  const source = String(text ?? '');
  if (!tokens.length) return escapeHtml(source);
  const lower = source.toLowerCase();
  if (lower.length !== source.length) return escapeHtml(source); // 折叠大小写改变了长度，下标就不可信了

  const ranges = [];
  for (const token of tokens) {
    let from = 0;
    for (;;) {
      const at = lower.indexOf(token, from);
      if (at === -1) break;
      ranges.push([at, at + token.length]);
      from = at + token.length;
    }
  }
  if (!ranges.length) return escapeHtml(source);

  ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
  const merged = [];
  for (const [start, end] of ranges) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }

  let html = '';
  let cursor = 0;
  for (const [start, end] of merged) {
    html += escapeHtml(source.slice(cursor, start));
    html += `<mark>${escapeHtml(source.slice(start, end))}</mark>`;
    cursor = end;
  }
  return html + escapeHtml(source.slice(cursor));
}

function hitCard(hit, tokens) {
  const rank = [];
  if (hit.ftRank !== null && hit.ftRank !== undefined) rank.push(`全文名次 ${hit.ftRank}`);
  if (hit.vecRank !== null && hit.vecRank !== undefined) rank.push(`向量名次 ${hit.vecRank}`);
  return `
    <article class="card">
      <div class="head">
        <span class="title">${escapeHtml(hit.title)}</span>
        <span class="tag ${escapeHtml(hit.hitBy)}">${HIT_LABEL[hit.hitBy] || hit.hitBy}</span>
        <span class="tag meta">RRF ${Number(hit.rrfScore).toFixed(5)}</span>
        <span class="tag meta">${escapeHtml(hit.source)} #${hit.chunkIndex}</span>
      </div>
      <p class="snippet">${highlight(hit.snippet, tokens)}</p>
      ${rank.length ? `<p class="result-note">${rank.join(' · ')}</p>` : ''}
      <div class="card-actions">
        <button data-schedule="${escapeHtml(hit.id)}" data-title="${escapeHtml(hit.title)}">排进复习计划</button>
        <span class="result-note schedule-note"></span>
      </div>
    </article>`;
}

async function refreshStatus() {
  try {
    const health = await api('/api/health');
    const { stats } = await api('/api/stats');
    $('status').innerHTML =
      `seekdb <b>${escapeHtml(health.database)}</b> · 段落 <b>${stats.docs}</b> · 词表 <b>${stats.terms}</b> · 模式 ${escapeHtml(health.searchMode)}`;
  } catch (error) {
    $('status').textContent = `未连接：${error.message}`;
  }
}

/** 「最近检索」下拉：读的是 seekdb 里的检索足迹 paper_study，不是浏览器本地历史 */
async function loadHistory() {
  const box = $('history-box');
  try {
    const user = $('user').value || 'demo';
    const { history } = await api(`/api/history?user=${encodeURIComponent(user)}`);
    if (!history.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    $('history-list').innerHTML = history
      .map((query) => `<li><button class="chip" data-q="${escapeHtml(query)}">${escapeHtml(query)}</button></li>`)
      .join('');
  } catch {
    box.hidden = true; // 足迹读不到不影响检索本身
  }
}

async function doSearch() {
  const query = $('query').value.trim();
  if (!query) return;
  const mode = $('mode').value;
  const box = $('results');
  box.innerHTML = '<p class="result-note">检索中…</p>';
  try {
    const result = await api('/api/search', {
      method: 'POST',
      body: JSON.stringify({ query, mode, topK: 8, userId: $('user').value || 'demo' }),
    });
    if (!result.hits.length) {
      box.innerHTML = '<p class="result-note">没有命中，先在左边导入一些资料。</p>';
    } else {
      box.innerHTML = result.hits.map((hit) => hitCard(hit, queryTokens(query))).join('');
    }
    $('sql').textContent = result.sql || '';
    $('sql-box').hidden = false;
    await Promise.all([refreshStatus(), loadHistory()]);
  } catch (error) {
    box.innerHTML = `<p class="result-note">检索失败：${escapeHtml(error.message)}</p>`;
  }
}

async function doIngest() {
  const text = $('doc-text').value.trim();
  if (!text) {
    $('ingest-result').textContent = '请先粘贴资料内容。';
    return;
  }
  try {
    const result = await api('/api/ingest', {
      method: 'POST',
      body: JSON.stringify({
        title: $('doc-title').value.trim() || '未命名资料',
        source: $('doc-source').value.trim() || `paste/${Date.now()}`,
        text,
        userId: $('user').value || 'demo',
      }),
    });
    $('ingest-result').textContent =
      `已写入 seekdb：${result.chunks} 个段落，重算 ${result.updated} 个向量，词表 ${result.terms} 个词。`;
    await refreshStatus();
  } catch (error) {
    $('ingest-result').textContent = `导入失败：${error.message}`;
  }
}

async function loadDue() {
  const user = $('user').value || 'demo';
  const box = $('due-list');
  box.innerHTML = '<p class="result-note">读取中…</p>';
  try {
    const { due } = await api(`/api/review/due?user=${encodeURIComponent(user)}`);
    if (!due.length) {
      box.innerHTML = '<p class="result-note">今天没有到期的复习条目。</p>';
      return;
    }
    box.innerHTML = due.map((row) => `
      <article class="card">
        <div class="head">
          <span class="title">${escapeHtml(row.title)}</span>
          <span class="tag meta">到期 ${escapeHtml(row.due_at)}</span>
          <span class="tag meta">已复习 ${row.reps} 次</span>
        </div>
        <p class="snippet">${escapeHtml(row.snippet || '')}</p>
        <div class="grade">
          <button data-id="${escapeHtml(row.doc_id)}" data-q="1">忘了</button>
          <button data-id="${escapeHtml(row.doc_id)}" data-q="3">有点印象</button>
          <button data-id="${escapeHtml(row.doc_id)}" data-q="5">很熟</button>
        </div>
      </article>`).join('');

    for (const button of box.querySelectorAll('button[data-id]')) {
      button.addEventListener('click', async () => {
        const result = await api('/api/review/grade', {
          method: 'POST',
          body: JSON.stringify({
            userId: user,
            docId: button.dataset.id,
            quality: Number(button.dataset.q),
          }),
        });
        button.parentElement.innerHTML =
          `<p class="result-note">已记入复习计划：下次 ${escapeHtml(result.dueAt)}（间隔 ${result.intervalDays} 天）</p>`;
      });
    }
  } catch (error) {
    box.innerHTML = `<p class="result-note">读取失败：${escapeHtml(error.message)}</p>`;
  }
}

$('search-btn').addEventListener('click', doSearch);
$('query').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') doSearch();
});
$('ingest-btn').addEventListener('click', doIngest);
$('due-btn').addEventListener('click', loadDue);

$('history-list').addEventListener('click', (event) => {
  const chip = event.target.closest('button[data-q]');
  if (!chip) return;
  $('query').value = chip.dataset.q;
  $('history-box').open = false;
  doSearch();
});

$('results').addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-schedule]');
  if (!button) return;
  const note = button.parentElement.querySelector('.schedule-note');
  note.textContent = '排期中…';
  try {
    await api('/api/review/schedule', {
      method: 'POST',
      body: JSON.stringify({
        userId: $('user').value || 'demo',
        docId: button.dataset.schedule,
        title: button.dataset.title,
      }),
    });
    note.textContent = '已排入复习计划，到期后出现在「③ 复习记忆」';
    button.disabled = true;
  } catch (error) {
    note.textContent = `排入失败：${error.message}`;
  }
});

// 快捷键：/ 聚焦搜索框，Esc 清空
document.addEventListener('keydown', (event) => {
  const tag = document.activeElement?.tagName;
  const typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT';
  if (event.key === '/' && !typing) {
    event.preventDefault();
    $('query').focus();
    return;
  }
  if (event.key === 'Escape') {
    $('query').value = '';
    $('results').innerHTML = '';
    $('sql-box').hidden = true;
    $('history-box').open = false;
    $('query').blur();
  }
});

// 支持 ?q=... 直接带查询打开（便于分享与截图）
const initialQuery = new URLSearchParams(location.search).get('q');
if (initialQuery) {
  $('query').value = initialQuery;
  doSearch();
}

refreshStatus();
loadHistory();
