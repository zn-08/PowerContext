/* 前端逻辑：全部通过 /api 与后端交互，检索与记忆都在 seekdb 里完成 */
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

function hitCard(hit) {
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
      <p class="snippet">${escapeHtml(hit.snippet)}</p>
      ${rank.length ? `<p class="result-note">${rank.join(' · ')}</p>` : ''}
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
      box.innerHTML = result.hits.map(hitCard).join('');
    }
    $('sql').textContent = result.sql || '';
    $('sql-box').hidden = false;
    await refreshStatus();
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

// 支持 ?q=... 直接带查询打开（便于分享与截图）
const initialQuery = new URLSearchParams(location.search).get('q');
if (initialQuery) {
  $('query').value = initialQuery;
  doSearch();
}

refreshStatus();
