"""Probe seekdb (127.0.0.1:2881) for vector + fulltext capabilities."""
import pymysql, json, traceback

conn = pymysql.connect(host='127.0.0.1', port=2881, user='root', password='', charset='utf8mb4')
cur = conn.cursor()

def run(label, sql, fetch=True):
    try:
        cur.execute(sql)
        if fetch:
            rows = cur.fetchall()
            print(f"[OK]   {label}: {len(rows)} rows")
            for r in rows[:8]:
                print("        ", r)
        else:
            print(f"[OK]   {label}")
        return True
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        return False

run("version", "SELECT VERSION()")
run("current_user", "SELECT CURRENT_USER()")
run("show engines", "SHOW ENGINES")

print("\n--- create test db ---")
run("create db", "CREATE DATABASE IF NOT EXISTS stepfun_probe", fetch=False)

run("use db", "USE stepfun_probe", fetch=False)

print("\n--- vector column ---")
run("drop t", "DROP TABLE IF EXISTS vec_demo", fetch=False)
run("create table with VECTOR(3)", """
CREATE TABLE vec_demo (
  id INT PRIMARY KEY,
  emb VECTOR(3),
  txt VARCHAR(512),
  meta VARCHAR(256),
  FULLTEXT INDEX ft_txt (txt)
)""", fetch=False)

print("\n--- insert ---")
run("insert", "INSERT INTO vec_demo (id, emb, txt, meta) VALUES (1, '[1,0,0]', 'oceanbase vector database', 'a'), (2, '[0,1,0]', 'mysql fulltext search index', 'b'), (3, '[0.9,0.1,0]', 'hybrid retrieval with vector', 'c')", fetch=False)

print("\n--- vector distance functions ---")
for fn in ['cosine_distance', 'l2_distance', 'inner_product', 'innerproduct', 'dot_product', 'vector_distance']:
    run(f"fn {fn}", f"SELECT id, {fn}(emb, '[1,0,0]') AS d FROM vec_demo ORDER BY d LIMIT 3")

print("\n--- vector index ---")
for stmt in [
    "CREATE VECTOR INDEX vidx ON vec_demo (emb)",
    "CREATE INDEX vidx2 ON vec_demo (emb) USING HNSW",
    "ALTER TABLE vec_demo ADD VECTOR INDEX vidx3 (emb)",
]:
    run(f"idx: {stmt}", stmt, fetch=False)

print("\n--- fulltext ---")
for stmt in [
    "MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE)",
    "MATCH(txt) AGAINST ('vector')",
    "SELECT id, MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) AS s FROM vec_demo",
    "SELECT id FROM vec_demo WHERE MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE)",
]:
    run(f"ft: {stmt}", stmt)

print("\n--- show index ---")
run("show index", "SHOW INDEX FROM vec_demo")
run("show create table", "SHOW CREATE TABLE vec_demo")

print("\n--- json / hybrid candidate patterns ---")
run("json type support", "SELECT JSON_EXTRACT('{\"a\":1}', '$.a')")
run("unnest", "SELECT * FROM UNNEST(ARRAY[1,2,3])")

cur.close(); conn.close()
print("\n=== probe done ===")
