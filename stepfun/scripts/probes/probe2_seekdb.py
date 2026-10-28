"""Probe 2: vector index syntax, fulltext parser options, hybrid SQL feasibility."""
import pymysql

conn = pymysql.connect(host='127.0.0.1', port=2881, user='root', password='', charset='utf8mb4')
cur = conn.cursor()

def run(label, sql, fetch=True):
    try:
        cur.execute(sql)
        if fetch:
            rows = cur.fetchall()
            print(f"[OK]   {label}: {len(rows)} rows")
            for r in rows[:10]:
                print("        ", r)
        else:
            print(f"[OK]   {label}")
        return True, None
    except Exception as e:
        print(f"[FAIL] {label}: {type(e).__name__}: {e}")
        return False, str(e)

run("use db", "USE stepfun_probe", fetch=False)

print("\n--- vector index variants ---")
variants = [
    "CREATE VECTOR INDEX vidx_a ON vec_demo (emb) WITH (distance = cosine)",
    "CREATE VECTOR INDEX vidx_b ON vec_demo (emb) USING HNSW WITH (distance = cosine)",
    "CREATE VECTOR INDEX vidx_c ON vec_demo (emb) WITH (distance=cosine, m=16, ef_construction=64)",
    "CREATE VECTOR INDEX vidx_d ON vec_demo (emb) USING HNSW WITH (distance = l2, m = 16, ef_construction = 64)",
    "CREATE VECTOR INDEX vidx_e ON vec_demo (emb) WITH (M = 16, EF_CONSTRUCTION = 64)",
]
for v in variants:
    ok, _ = run(f"vi: {v[:70]}", v, fetch=False)
    if ok:
        run("show index after", "SHOW INDEX FROM vec_demo")

print("\n--- index params / hints ---")
run("select with approx hint", "SELECT /*+ approx_vector_index */ id FROM vec_demo ORDER BY cosine_distance(emb, '[1,0,0]') LIMIT 3")
run("select normal order", "SELECT id FROM vec_demo ORDER BY cosine_distance(emb, '[1,0,0]') LIMIT 3")

print("\n--- fulltext parser variants (chinese support) ---")
for parser in ["space", "ngram", "standard", "basic"]:
    ok, _ = run(f"create ft table parser={parser}", f"""
CREATE TABLE ft_p_{parser} (
  id INT PRIMARY KEY, txt VARCHAR(512),
  FULLTEXT KEY ft (txt) WITH PARSER {parser}
)""", fetch=False)
    if ok:
        run(f"insert {parser}", f"INSERT INTO ft_p_{parser} (id, txt) VALUES (1,'海洋数据库 向量 检索'), (2,'hybrid search vector database')", fetch=False)
        run(f"match {parser} en", f"SELECT id, MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) s FROM ft_p_{parser}")
        run(f"match {parser} zh", f"SELECT id, MATCH(txt) AGAINST ('向量' IN NATURAL LANGUAGE MODE) s FROM ft_p_{parser}")

print("\n--- hybrid single-SQL candidates ---")
run("union rrf-ish", """
SELECT id, txt,
       0.5 * (1.0 / (60 + vrank)) + 0.5 * (1.0 / (60 + frank)) AS score
FROM (
  SELECT id, txt, ROW_NUMBER() OVER (ORDER BY cosine_distance(emb, '[1,0,0]')) AS vrank, 999 AS frank
  FROM vec_demo
) v
JOIN (
  SELECT id, ROW_NUMBER() OVER (ORDER BY MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) DESC) AS frank
  FROM vec_demo WHERE MATCH(txt) AGAINST ('vector' IN NATURAL LANGUAGE MODE) > 0
) f ON v.id = f.id
ORDER BY score DESC
""")

print("\n--- window fn / cte support ---")
run("cte", "WITH t AS (SELECT 1 AS a) SELECT * FROM t")
run("row_number", "SELECT id, ROW_NUMBER() OVER (ORDER BY id) rn FROM vec_demo")
run("union all", "SELECT id FROM vec_demo UNION ALL SELECT id FROM vec_demo")

print("\n--- vars ---")
run("vars parser", "SHOW VARIABLES LIKE '%parser%'")
run("vars vector", "SHOW VARIABLES LIKE '%vector%'")

cur.close(); conn.close()
print("\n=== probe2 done ===")
