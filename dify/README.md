# 记得你所有恶作剧的掌柜 · Dify × seekdb

> 一个会把你过去的恶作剧一条条记下来的酒馆掌柜 NPC。
> Dify 负责编排与台词生成，**seekdb 负责记忆与世界设定**。

## 一、作品创意与玩法

**背景**：歪脖子酒馆的掌柜有一本「恶作剧账本」。他记得每一个客人在这里干过的每一件坏事，
包括你上次藏起来的茶壶。你骗不了他——他会把账本上的旧事翻出来念给你听。

**玩法**：你是进店的客人。掌柜有三条规矩，藏在世界设定里：

1. 酒馆上方的「月灯」只在有人说押 `lobster` 韵的词时才会亮；
2. 一年一度的龙虾节，配方是镇上的秘密，上一位厨子失踪在配方写到一半的时候；
3. **账本上记过的恶作剧不许重复**。

你可以说任何话，掌柜会用酒馆的口吻回应你。关键在于：

- 你**说过什么他会记住**（本轮对话写回 seekdb 的记忆表）；
- 下一轮他会**翻旧账**（从记忆表读回这个玩家的历史）；
- 你提到某个话题时，他会用**全文索引**从世界设定表里把相关设定捞出来并**引用**它
  （`MATCH ... AGAINST`，走 seekdb 的原生全文索引）；
- 你如果重复了以前干过的事，他会当场点破。

**跨会话记忆**：把「玩家标识」改成同一个值再开一个新会话，掌柜依然记得你。

## 二、技术分工

| 组成 | 承担的作用 |
| --- | --- |
| **Dify（必选）** | 承担应用的核心流程编排：玩家输入 → 取记忆与设定 → 模型生成台词 → 写回记忆 → 回复玩家。流程以 Chatflow（`advanced-chat`）形式提供，提交 DSL。 |
| **seekdb** | 作为应用的数据存储与检索能力：① `npc_memory` 表保存每个玩家的每一轮对话；② `npc_lore` 表保存世界设定，并建**原生全文索引**，用 `MATCH(title, body) AGAINST (?)` 检索与当前话题相关的设定条目。 |

> 题目允许「Dify + seekdb」「Dify + PowerContext」「Dify + seekdb + PowerContext」三者之一。
> 本作品选择 **Dify + seekdb**：seekdb 同时承担了「游戏数据存储」与「设定检索」两种职责，
> 而记忆服务只依赖 MySQL 协议，评审环境准备成本最低。

## 三、目录结构

```
dify/
├── README.md                       本文件
├── dsl/
│   └── npc-memory-chatflow.yml     Dify Chatflow 导出（导入即用）
└── service/
    ├── app.py                      记忆服务：读记忆 / 全文检索设定 / 写回记忆
    ├── seed.py                    建库建表 + 灌入世界设定与样例记忆
    └── requirements.txt            仅依赖 PyMySQL
```

## 四、运行环境

| 组件 | 版本 / 要求 |
| --- | --- |
| seekdb | 官方发行版，MySQL 协议；本文用 `5.7.25-OceanBase seekdb-v1.3.0.0` 验证 |
| Python | 3.10+，仅需 `PyMySQL`（`pip install -r service/requirements.txt`） |
| Dify | 支持 DSL `version: 0.1.5` 的版本（`advanced-chat` 模式）。Dify Cloud 同样适用 |

## 五、完整运行步骤

### 1. 起一个 seekdb

任选一种：

```bash
# 方式 A：官方 docker 镜像
docker run -d --name seekdb-npc -p 2881:2881 oceanbase/seekdb:latest

# 方式 B：本地二进制
./seekdb --port=2881 --nodaemon
```

确认可用（root 账号、空密码）：

```bash
mysql -h127.0.0.1 -P2881 -uroot -e "SELECT VERSION()"
```

### 2. 建库建表 + 灌初始数据

```bash
cd dify/service
pip install -r requirements.txt
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=''
python seed.py
```

预期输出：

```
database npc_memory_demo: lore rows=6, memory rows=1
  full-text hit: keyword=kettle title=the singing kettle relevance=1.4256471646317077
  full-text hit: keyword=lobster title=lobster festival relevance=0.6307954452608108
```

> `seed.py` 会 `DROP DATABASE IF EXISTS npc_memory_demo` 后重建，可重复执行以复位样例状态。

### 3. 起记忆服务

```bash
export SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_USER=root SEEKDB_PASSWORD=''
export SEEKDB_DATABASE=npc_memory_demo
export SERVICE_HOST=0.0.0.0 SERVICE_PORT=8808
python app.py
```

自检：

```bash
curl http://127.0.0.1:8808/health
# {"ok": true, "seekdb": "5.7.25-OceanBase seekdb-v1.3.0.0", "database": "npc_memory_demo"}
```

### 4. 导入 Dify DSL

1. Dify 控制台 → 「工作室」→ 「导入 DSL」→ 上传 `dsl/npc-memory-chatflow.yml`。
2. **设置环境变量** `SERVICE_BASE_URL` = 记忆服务地址（默认 `http://127.0.0.1:8808`；
   若 Dify 与记忆服务不在同一台机器，改成可达地址，例如 `http://host.docker.internal:8808`）。
3. **配置模型**：DSL 里预置了 `langgenius/openai/openai` + `gpt-4o-mini`，导入后请改成
   你自己有额度、且评审环境能访问的模型；模型只负责「按资料说话」，不负责检索。
4. 发布应用（建议先在「预览」里跑一遍第五节的样例）。

> 两个 HTTP Request 节点分别指向 `{{#env.SERVICE_BASE_URL#}}/context` 与
> `{{#env.SERVICE_BASE_URL#}}/memory`，换地址只需要改这一个环境变量。

## 六、验证样例

下面三步在**本机真实跑过**，输出是实际结果（服务日志里的 SQL 一并附上）。

### 样例 1 · 第一次来：掌柜还不认识你

工作流输入：

- 玩家标识：`player-utf8`
- 玩家这次说了什么：`我把房东的茶壶藏起来了`

记忆服务返回（`GET /context`）：

```json
{
  "session_id": "player-utf8",
  "query": "我把房东的茶壶藏起来了",
  "memory": [],
  "lore": []
}
```

> 第一次对话没有任何历史，掌柜不会凭空提起账本。

### 样例 2 · 同一个玩家再来：他翻旧账，并引用世界设定

工作流输入：

- 玩家标识：`player-utf8`
- 玩家这次说了什么：`我要再藏一次茶壶`

记忆服务返回（`GET /context`，**这次带回了上一轮的记忆**，并且用全文索引捞出了设定）：

```json
{
  "session_id": "player-utf8",
  "query": "我要再藏一次茶壶",
  "memory": [
    {
      "turn_no": 1,
      "player_input": "我把房东的茶壶藏起来了",
      "npc_reply": "记下来了。茶壶，失踪，暂记在玩家名下。",
      "created_at": "2026-11-04 19:13:21"
    }
  ],
  "lore": [
    {
      "id": 4,
      "keyword": "prank",
      "title": "the pranking ledger",
      "body": "The inn keeps a ledger of every prank ever played. The rule is simple: never repeat a prank that has already been written down.",
      "relevance": 0.615
    }
  ]
}
```

掌柜应当指出：**账本上已经记着茶壶这一条，而且规矩是记过的恶作剧不许重复**——
这两条分别来自 `memory`（上一轮对话）和 `lore`（全文索引检索出的设定）。

### 样例 3 · 换个话题：全文索引照样能捞到设定

工作流输入：

- 玩家标识：`player-utf8`
- 玩家这次说了什么：`龙虾节的配方到底怎么写`

记忆服务返回的 `lore`：

```json
[
  {
    "id": 2,
    "keyword": "lobster",
    "title": "lobster festival",
    "body": "Once a year the villagers cook a lobster the size of a rowing boat in the square. The recipe is a state secret and the last cook vanished mid-recipe.",
    "relevance": 0.63
  }
]
```

掌柜应当引用「龙虾节配方是秘密、上一个厨子失踪在配方写到一半时」这条设定。

### 记忆写入证据（`POST /memory`）

```
$ curl -X POST http://127.0.0.1:8808/memory -H 'Content-Type: application/json' \
    -d '{"session_id":"player-utf8","player_input":"我把房东的茶壶藏起来了","npc_reply":"记下来了。茶壶，失踪，暂记在玩家名下。"}'
{"ok": true, "session_id": "player-utf8", "turn_no": 1}
```

## 七、seekdb 确实参与的证据

记忆服务把每次执行的语句打进日志（`logging` 输出到 stderr），例如：

```
2026-11-04 19:13:58,345 INFO insert query: INSERT INTO npc_memory (session_id, turn_no, player_input, npc_reply, created_at) SELECT %s, COALESCE(MAX(turn_no), 0) + 1, %s, %s, NOW() FROM npc_memory WHERE session_id = %s
2026-11-04 19:13:58,394 INFO turns query: SELECT turn_no, player_input, npc_reply, created_at FROM npc_memory WHERE session_id = %s ORDER BY turn_no DESC LIMIT %s
2026-11-04 19:13:58,405 INFO lore query: SELECT id, keyword, title, body, MATCH(title, body) AGAINST (%s) AS relevance FROM npc_lore WHERE MATCH(title, body) AGAINST (%s) ORDER BY relevance DESC LIMIT %s
```

还可以直接在 seekdb 上核对：

```sql
-- 记忆表里确实有这一轮
SELECT session_id, turn_no, player_input, npc_reply FROM npc_memory ORDER BY session_id, turn_no;

-- 全文索引确实建在设定表上
SHOW INDEX FROM npc_lore;

-- 设定检索走的是原生全文索引
SELECT keyword, title, MATCH(title, body) AGAINST ('lobster kettle') AS relevance
  FROM npc_lore
 WHERE MATCH(title, body) AGAINST ('lobster kettle')
 ORDER BY relevance DESC LIMIT 3;
```

## 八、已知限制

- 记忆服务是单文件 HTTP 服务，用 Python 标准库实现，只依赖 PyMySQL，方便评审环境快速拉起；
  它**不是**生产级组件，没有鉴权与多副本部署。
- `SERVICE_BASE_URL` 必须从 Dify 容器/实例可达；若 Dify 在容器里而记忆服务在宿主机，
  需要用 `host.docker.internal` 或容器网络内的服务名。
- 记忆按 `session_id` 划分，没有做跨会话的用户身份认证——题目考察的是「上下文是否被真正复用」，
  身份属于作品自己的设计。
- 世界设定是 6 条种子数据，通过全文索引检索；设定更多时 `MATCH` 的召回效果会更好。
