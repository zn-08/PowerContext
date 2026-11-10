# Dify 记忆服务 · 验证样例实测记录

- 执行时间：2026-11-10
- seekdb：`127.0.0.1:2881`，数据库 `npc_memory_demo`
- 记忆服务：`http://127.0.0.1:8808`
- 命令行与返回均为真实执行结果（未做任何改写）

## 0. 环境与健康检查

```
$ curl http://127.0.0.1:8808/health
{"ok": true, "seekdb": "5.7.25-OceanBase seekdb-v1.3.0.0", "database": "npc_memory_demo"}
```

## 1. 样例一：第一次来，掌柜还不认识你

```
$ curl 'http://127.0.0.1:8808/context?session=player-utf8&query=我把房东的茶壶藏起来了'
{
  "session_id": "player-utf8",
  "query": "我把房东的茶壶藏起来了",
  "memory": [],
  "lore": [
    {
      "id": 6,
      "keyword": "kettle",
      "title": "the singing kettle",
      "body": "The kettle sings only when the fire is fed with old letters. The tune it sings is always the one the writer wanted to send but never did.",
      "title_zh": "会唱歌的茶壶",
      "body_zh": "茶壶只有在用旧信纸烧火时才会唱歌，唱的总是写信人想送出去却一直没送的那支曲子。",
      "relevance": 0.9112987714926823
    },
    {
      "id": 3,
      "keyword": "tavern",
      "title": "the crooked tavern",
      "body": "The crooked tavern leans three degrees to the left, which is why the regulars always walk in circles and the landlord insists the ale is to blame.",
      "title_zh": "歪脖子酒馆",
      "body_zh": "歪脖子酒馆朝左歪三度，所以常客进门总绕圈走，房东坚持说是麦芽酒的问题。",
      "relevance": 0.5376060828212262
    },
    {
      "id": 1,
      "keyword": "lamp",
      "title": "moon lamp",
      "body": "The moon lamp hangs above the tavern door and only lights up when someone says a word that rhymes with 'lobster'. The keeper claims it is broken.",
      "title_zh": "月灯",
      "body_zh": "酒馆门上的月灯，只在有人念出跟『龙虾』押韵的词时才会亮。掌柜嘴上说它是坏的。",
      "relevance": 0.18965517241379312
    }
  ]
}
```

> 第一次对话没有任何历史，掌柜不会凭空提起账本。

## 2. 记忆写入（seekdb 落库）

```
$ curl -X POST http://127.0.0.1:8808/memory -H 'Content-Type: application/json' \
    -d '{"session_id":"player-utf8","player_input":"我把房东的茶壶藏起来了",
         "npc_reply":"记下来了。茶壶，失踪，暂记在玩家名下。"}'
{"ok": true, "session_id": "player-utf8", "turn_no": 1}
```

## 3. 样例二：同一个玩家再来，掌柜翻旧账并引用世界设定

```
$ curl 'http://127.0.0.1:8808/context?session=player-utf8&query=我要再藏一次茶壶'
{
  "session_id": "player-utf8",
  "query": "我要再藏一次茶壶",
  "memory": [
    {
      "turn_no": 1,
      "player_input": "我把房东的茶壶藏起来了",
      "npc_reply": "记下来了。茶壶，失踪，暂记在玩家名下。",
      "created_at": "2026-11-10 12:48:30"
    }
  ],
  "lore": [
    {
      "id": 6,
      "keyword": "kettle",
      "title": "the singing kettle",
      "body": "The kettle sings only when the fire is fed with old letters. The tune it sings is always the one the writer wanted to send but never did.",
      "title_zh": "会唱歌的茶壶",
      "body_zh": "茶壶只有在用旧信纸烧火时才会唱歌，唱的总是写信人想送出去却一直没送的那支曲子。",
      "relevance": 0.7254879606818715
    }
  ]
}
```

> 掌柜应当指出：账本上已经记着茶壶这一条，而且规矩是记过的恶作剧不许重复。
> 前者来自 `memory`（上一轮对话），后者来自 `lore`（全文索引检索出的设定）。

## 4. 样例三：换个话题，全文索引照样捞得到设定

```
$ curl 'http://127.0.0.1:8808/context?session=player-utf8&query=龙虾节的配方到底怎么写'
[
  {
    "id": 2,
    "keyword": "lobster",
    "title": "lobster festival",
    "body": "Once a year the villagers cook a lobster the size of a rowing boat in the square. The recipe is a state secret and the last cook vanished mid-recipe.",
    "title_zh": "龙虾节",
    "body_zh": "一年一度，镇上要在广场烤一只赛龙舟那么大的龙虾。配方是镇上的秘密，上一位厨子失踪在配方写到一半的时候。",
    "relevance": 1.3267671675808472
  },
  {
    "id": 1,
    "keyword": "lamp",
    "title": "moon lamp",
    "body": "The moon lamp hangs above the tavern door and only lights up when someone says a word that rhymes with 'lobster'. The keeper claims it is broken.",
    "title_zh": "月灯",
    "body_zh": "酒馆门上的月灯，只在有人念出跟『龙虾』押韵的词时才会亮。掌柜嘴上说它是坏的。",
    "relevance": 0.18965517241379312
  },
  {
    "id": 6,
    "keyword": "kettle",
    "title": "the singing kettle",
    "body": "The kettle sings only when the fire is fed with old letters. The tune it sings is always the one the writer wanted to send but never did.",
    "title_zh": "会唱歌的茶壶",
    "body_zh": "茶壶只有在用旧信纸烧火时才会唱歌，唱的总是写信人想送出去却一直没送的那支曲子。",
    "relevance": 0.18581081081081086
  }
]
```

## 5. 跨会话记忆：换个 session 就没有历史

```
$ curl 'http://127.0.0.1:8808/context?session=player-other&query=我要再藏一次茶壶'
{"memory": []}
```

## 6. 直接在 seekdb 上核对

```sql
SELECT session_id, turn_no, player_input, npc_reply FROM npc_memory ORDER BY session_id, turn_no;
  player-default | 1 | 我把房东的茶壶藏起来了 | 记下来了。房东已经在数第三遍茶叶罐，账本上写着：茶壶，失踪，暂记在房东名下。
  player-utf8 | 1 | 我把房东的茶壶藏起来了 | 记下来了。茶壶，失踪，暂记在玩家名下。

SHOW INDEX FROM npc_lore;
  npc_lore | 0 | PRIMARY | 1 | id | A | None | None | None |  | BTREE | available |  | YES | None
  npc_lore | 1 | ft_npc_lore | 1 | title | A | None | None | None |  | FULLTEXT | available |  | YES | None
  npc_lore | 1 | ft_npc_lore | 2 | body | A | None | None | None |  | FULLTEXT | available |  | YES | None
  npc_lore | 1 | ft_npc_lore | 3 | title_zh | A | None | None | None |  | FULLTEXT | available |  | YES | None
  npc_lore | 1 | ft_npc_lore | 4 | body_zh | A | None | None | None |  | FULLTEXT | available |  | YES | None
  npc_lore | 1 | ft_npc_lore | 5 | tags | A | None | None | None |  | FULLTEXT | available |  | YES | None

SELECT keyword, title_zh, MATCH(title, body, title_zh, body_zh, tags) AGAINST ('lobster kettle') AS relevance
  FROM npc_lore
 WHERE MATCH(title, body, title_zh, body_zh, tags) AGAINST ('lobster kettle')
 ORDER BY relevance DESC LIMIT 3;
  kettle | 会唱歌的茶壶 | 0.9656832990157346
  lobster | 龙虾节 | 0.43686846715698047
  lamp | 月灯 | 0.33587809422978226

-- 中文提问同样命中同一条设定（ik 分词）：
SELECT keyword, title_zh, MATCH(title, body, title_zh, body_zh, tags) AGAINST ('龙虾节的配方') AS relevance
  FROM npc_lore
 WHERE MATCH(title, body, title_zh, body_zh, tags) AGAINST ('龙虾节的配方')
 ORDER BY relevance DESC LIMIT 3;
  lobster | 龙虾节 | 1.3267671675808472
  lamp | 月灯 | 0.18965517241379312
  kettle | 会唱歌的茶壶 | 0.18581081081081086
```

## 7. 结论

- 记忆按 `session_id` 落 seekdb 表 `npc_memory`，跨会话可复用；
- 世界设定落 `npc_lore`，带**原生全文索引**，检索走 `MATCH(title, body) AGAINST (...)`；
- Dify Chatflow 通过两个 HTTP Request 节点调用本服务（取上下文 / 写回记忆），
  模型只负责按资料说话，不负责检索。
