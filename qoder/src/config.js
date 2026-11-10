/**
 * config.js —— 全局配置（全部可用环境变量覆盖，评审环境按需改）
 */
export const config = {
  httpPort: Number(process.env.PORT || 5174),
  httpHost: process.env.HOST || '127.0.0.1',

  db: {
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD ?? '',
    database: process.env.SEEKDB_DATABASE || 'papercompass',
  },

  /** 向量维度：与 paper_doc.emb 的 VECTOR(384) 一致 */
  dim: Number(process.env.EMBED_DIM || 384),

  /** 切块参数（按字符计） */
  chunkSize: Number(process.env.CHUNK_SIZE || 320),
  chunkOverlap: Number(process.env.CHUNK_OVERLAP || 80),

  /** 检索返回条数 */
  topK: Number(process.env.TOP_K || 8),

  /** 混合检索模式：'native' = DBMS_HYBRID_SEARCH（数据库生成 SQL）；'sql' = 手写单条 RRF */
  searchMode: process.env.SEARCH_MODE || 'native',
};
