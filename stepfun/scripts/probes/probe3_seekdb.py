"""Probe 3: exact vector index DDL syntax + fulltext parser 'ik' + hybrid single-SQL."""
import pymysql

conn = pymysql.connect(host='127.0.0.1', port=2881, user='root', password='', charset='utf8mb4')
cur = conn.cursor()

def run(label, sql, fetch=True):
    try:
        cur.execute(sql)
        if fetch:
            rows = cur.fetchall()
            print(f"[OK]   {label}: {len(rows)} rows")
            for r in rows[:6]:
                print("        ", r)
        else:
            print(f"[OK]   {label}")
        return True
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:180]}")
        return False

run("use db", "USE stepfun_probe", fetch=False)

print("\n--- vector index DDL variants ---")
cands = [
    "CREATE VECTOR INDEX vidx ON vec_demo (emb) WITH (distance = cosine, type = hnsw)",
    "CREATE VECTOR INDEX vidx ON vec_demo (emb) WITH (distance = cosine, type = HNSW, m = 16, ef_construction = 64)",
    "CREATE VECTOR INDEX vidx ON vec_demo (emb) USING hnsw WITH (distance = cosine, type = hnsw)",
    "CREATE INDEX vidx ON vec_demo (emb) USING hnsw WITH (distance = cosine, type = hnsw)",
    "CREATE VECTOR INDEX vidx ON vec_demo (emb) WITH (type = hnsw, distance = l2)",
    "CREATE VECTOR INDEX vidx ON vec_demo (emb) WITH (distance=cosine, type=hnsw)",
]
for c in cands:
    if run(f"vi: {c}", c, fetch=False):
        run("show index", "SHOW INDEX FROM vec_demo")

print("\n--- drop idx & retry ---")
run("drop index vidx", "DROP INDEX vidx ON vec_demo", fetch=False)

print("\n--- fulltext parser ik ---")
for p in ["ik", "ngram"]:
    run(f"create ft_{p}", f"""CREATE TABLE ftp_{p} (id INT PRIMARY KEY, txt VARCHAR(512), FULLTEXT KEY ft (txt) WITH PARSER {p})""", fetch=False)
run("insert ik", "INSERT INTO ftp_ik (id, txt) VALUES (1,'海洋数据库 向量 混合检索'),(2,'oceanbase vector hybrid search'),(3,'端侧推理 性能优化')", fetch=False)
run("ik zh match", "SELECT id, MATCH(txt) AGAINST ('向量' IN NATURAL LANGUAGE MODE) s FROM ftp_ik")
run("ik en match", "SELECT id, MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) s FROM ftp_ik")
run("show create ftp_ik", "SHOW CREATE TABLE ftp_ik", fetch=False)

print("\n--- show create / describe ---")
run("describe vec_demo", "DESCRIBE vec_demo")

print("\n--- hybrid single SQL (explicit, unambiguous) ---")
sql = """
SELECT
  h.id,
  h.title,
  h.v_score,
  h.f_score,
  ROUND(0.5 * h.v_score + 0.5 * h.f_score, 6) AS hybrid_score
FROM (
  SELECT
    s.id,
    s.title,
    1.0 - (cosine_distance(s.emb, %s) / (cosine_distance(s.emb, %s) + 1e-9)) AS v_score,
    MATCH(s.title, s.body) AGAINST (%s IN NATURAL LANGUAGE MODE) AS f_score
  FROM stepfun_probe.docs s
) h
ORDER BY hybrid_score DESC
LIMIT 10
"""
run("multi-col MATCH", "SELECT id, MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) s FROM vec_demo")
run("MATCH 2 cols", "SELECT id, MATCH(title, body) AGAINST ('x' IN NATURAL LANGUAGE MODE) s FROM (SELECT id, txt AS title, txt AS body FROM vec_demo) t")

cur.close(); conn.close()
print("\n=== probe3 done ===")
