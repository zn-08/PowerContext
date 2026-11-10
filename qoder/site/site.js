/* 站点脚本：加载真实运行快照并提供可交互的演示 */
const HIT_LABEL = { both: '全文+向量', fulltext: '全文命中', vector: '向量命中' };

function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

function renderQuery(item) {
  const cards = item.hits.map((hit) => `
    <article class="hit">
      <div class="hit-head">
        <span class="hit-title">${escapeHtml(hit.title)}</span>
        <span class="tag ${escapeHtml(hit.hitBy)}">${HIT_LABEL[hit.hitBy] || hit.hitBy}</span>
        <span class="tag">RRF ${Number(hit.rrfScore).toFixed(5)}</span>
        <span class="tag">${escapeHtml(hit.source)} #${hit.chunkIndex}</span>
        ${hit.ftRank !== null && hit.ftRank !== undefined ? `<span class="tag">全文名次 ${hit.ftRank}</span>` : ''}
        ${hit.vecRank !== null && hit.vecRank !== undefined ? `<span class="tag">向量名次 ${hit.vecRank}</span>` : ''}
      </div>
      <p class="hit-body">${escapeHtml(hit.snippet)}</p>
    </article>`).join('');
  return `
    <p class="demo-q">${escapeHtml(item.query)} <span class="muted">（${escapeHtml(item.note)}）</span></p>
    ${cards || '<p class="muted">没有命中。</p>'}
    <details class="sql"><summary>数据库生成的混合检索 SQL</summary><pre>${escapeHtml(item.sql || '')}</pre></details>`;
}

async function main() {
  const response = await fetch('./snapshot.json');
  const snapshot = await response.json();
  const bar = document.getElementById('demo-bar');
  const out = document.getElementById('demo-out');

  snapshot.queries.forEach((item, index) => {
    const button = document.createElement('button');
    button.className = 'demo-btn';
    button.textContent = item.query;
    button.addEventListener('click', () => {
      for (const other of bar.children) other.classList.remove('active');
      button.classList.add('active');
      out.innerHTML = renderQuery(item);
    });
    bar.appendChild(button);
    if (index === 0) {
      button.classList.add('active');
      out.innerHTML = renderQuery(item);
    }
  });

  const stats = snapshot.stats || {};
  document.getElementById('foot-meta').textContent =
    `快照生成于 ${snapshot.generatedAt} · 库内段落 ${stats.docs} · 词表 ${stats.terms} · 向量维度 ${stats.dim} · 检索模式 ${stats.mode}`;
}

main().catch((error) => {
  document.getElementById('demo-out').innerHTML = `<p class="muted">快照加载失败：${error.message}</p>`;
});
