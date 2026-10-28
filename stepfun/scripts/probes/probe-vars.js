import mysql from 'mysql2/promise';
import { config } from '../server/config.js';

const conn = await mysql.createConnection({
  host: config.dbHost, port: config.dbPort, user: config.dbUser,
  password: config.dbPassword, database: config.database,
});
async function show(label, sql) {
  try {
    const [rows] = await conn.query(sql);
    console.log(`\n### ${label}`);
    console.log(JSON.stringify(rows, null, 1).slice(0, 2500));
  } catch (e) { console.log(`\n### ${label} ERROR ${String(e.message).slice(0,200)}`); }
}
await show('SHOW INDEX stepfun_docs', 'SHOW INDEX FROM `stepfun_docs`');
await show('SHOW CREATE stepfun_docs', 'SHOW CREATE TABLE `stepfun_docs`');
await show('vars %index%', "SHOW VARIABLES LIKE '%index%'");
await show('vars %hnsw%', "SHOW VARIABLES LIKE '%hnsw%'");
await show('vars %vsag%', "SHOW VARIABLES LIKE '%vsag%'");
await show('vars %fulltext%', "SHOW VARIABLES LIKE '%fulltext%'");
await show('vars %parser%', "SHOW VARIABLES LIKE '%parser%'");
await show('vars %optimizer%', "SHOW VARIABLES LIKE '%optimizer%'");
await show('changelog', 'SELECT * FROM oceanbase.GV$OB_LOG_STAT LIMIT 1').catch(()=>{});
await conn.end();
