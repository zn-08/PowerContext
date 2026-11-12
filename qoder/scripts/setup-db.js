/**
 * setup-db.js —— 建库建表 + 灌入示例语料
 *
 *   node scripts/setup-db.js
 *
 * 幂等：重复执行只会覆盖同名段落（id 由 来源 + 段落序号 派生）。
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chunkId, chunkText } from '../src/chunk.js';
import { config } from '../src/config.js';
import { close, ensureSchema, ingest, stats } from '../src/db.js';
import { assertEmbedderReady } from '../src/embed.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, '..');
const NOTES_DIR = path.join(ROOT, 'data', 'notes');

async function main() {
  await assertEmbedderReady();
  await ensureSchema();
  const files = (await fs.readdir(NOTES_DIR)).filter((name) => name.endsWith('.md')).sort();
  let total = 0;
  for (const file of files) {
    const raw = await fs.readFile(path.join(NOTES_DIR, file), 'utf8');
    const title = (raw.match(/^#\s+(.+)$/m) || [null, path.basename(file, '.md')])[1].trim();
    const source = file;
    const body = raw.replace(/^#\s+.+$/m, '').trim();
    const chunks = chunkText(body, { chunkSize: config.chunkSize, chunkOverlap: config.chunkOverlap });
    const docs = chunks.map((chunk, index) => ({
      id: chunkId(source, index),
      title,
      source,
      chunkIndex: index,
      body: chunk,
    }));
    const result = await ingest(docs);
    total += docs.length;
    console.log(`[setup] ${source}: ${docs.length} chunks (${result.updated} vectors, ${result.terms} terms)`);
  }
  console.log(`[setup] total chunks = ${total}`);
  console.log(`[setup] stats = ${JSON.stringify(await stats())}`);
  await close();
}

main().catch((error) => {
  console.error('[setup] failed:', error);
  process.exit(1);
});
