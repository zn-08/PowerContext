/**
 * scripts/setup-db.js — 只做 DDL：创建数据库、表、全文索引与向量索引
 *
 * 刻意 **不** 在这里生成任何向量或预置检索结果：
 * 文档向量与查询向量必须由浏览器端的 Transformers.js 计算后写入。
 * （演示数据请用浏览器界面导入，或用 scripts/selftest.js 做等价 Node 侧验证。）
 *
 * 用法： node scripts/setup-db.js
 */
import * as db from '../server/db.js';
import { config } from '../server/config.js';

const c = await db.connect();
console.log(`已连接 ${config.dbHost}:${config.dbPort}`);

await db.ensureSchema();
console.log(`schema 就绪: database=${config.database} table=${config.table}`);

const ddl = await c.execute(`SHOW CREATE TABLE \`${config.table}\``);
console.log('\n--- 建表结果 ---');
console.log(ddl?.[0]?.['Create Table'] ?? '(无法读取)');

const idx = await c.execute(`SHOW INDEX FROM \`${config.table}\``);
console.log('\n--- 索引 ---');
for (const r of idx) {
  console.log(`  ${String(r.Key_name).padEnd(28)} ${String(r.Column_name).padEnd(12)} ${r.Index_type}`);
}

console.log('\n--- 说明 ---');
console.log('  FULLTEXT INDEX ft_<table>_body : body 列全文索引（ik 分词），中英文均可');
console.log('  VECTOR   INDEX idx_<table>_emb : emb 列 HNSW 向量索引（distance=cosine）');
console.log('  表不使用用户主键（原生混合检索要求使用隐藏 __pk_increment 列），');
console.log('  id 上建 UNIQUE 索引以支持幂等 upsert 与增量导入。');
console.log('\n下一步： node server/index.js  然后在浏览器里导入文档。');

await db.close();
