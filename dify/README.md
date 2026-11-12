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
  （`MATCH(title, body, title_zh, body_zh, tags) AGAINST (?)`，走 seekdb 的原生全文索引，
  分词器用 `ik`，中英文提问都能命中）；
- 你如果重复了以前干过的事，他会当场点破。

**跨会话记忆**：把「玩家标识」改成同一个值再开一个新会话，掌柜依然记得你。

## 二、技术分工

| 组成 | 承担的作用 |
| --- | --- |
| **Dify（必选）** | 承担应用的核心流程编排：玩家输入 → 取记忆与设定 → 模型生成台词 → 写回记忆 → 回复玩家。流程以 Chatflow（`advanced-chat`）形式提供，提交 DSL。 |
| **seekdb** | 作为应用的数据存储与检索能力：① `npc_memory` 表保存每个玩家的每一轮对话；② `npc_lore` 表保存世界设定，并建**原生全文索引**（`ik` 分词），用 `MATCH(...) AGAINST (...)` 检索与当前话题相关的设定条目。 |

> 题目允许「Dify + seekdb」「Dify + PowerContext」「Dify + seekdb + PowerContext」三者之一。
> 本作品选择 **Dify + seekdb**：seekdb 同时承担了「游戏数据存储」与「设定检索」两种职责，
> 而记忆服务只依赖 MySQL 协议，评审环境准备成本最低。

## 三、目录结构

```
dify/
├── README.md                              本文件（运行说明）
├── npc-memory-chatflow.yml                Dify Chatflow 导出，DSL version 0.5.0（Dify ≥ 1.12 用这个）
├── check_dsl.py                           DSL 自检：解析 YAML，检查图连通、模板引用、导入器需要的字段、每个节点的降级策略
├── verify_samples.py                      验证样例脚本：照着工作流发同样的两个 POST 请求
├── service/
│   ├── app.py                             记忆服务：读记忆 / 全文检索设定 / 写回记忆
│   ├── seed.py                            建库建表 + 灌入世界设定与样例记忆
│   ├── start.sh                           一条命令：装依赖 → 灌数据 → 起服务
│   └── requirements.txt                   仅依赖 PyMySQL
└── evidence/
    ├── verification.md                    验证样例的**真实执行记录**（命令行 + 返回值 + SQL 核对）
    ├── verification-console.log           生成上述记录时的控制台输出
    ├── seed-console.log                   seed.py 的控制台输出
    └── service-run.err.log                记忆服务运行日志（含每条实际执行的 SQL）
```

### 自检

```bash
python dify/check_dsl.py dify/npc-memory-chatflow.yml     # 期望输出：OK: no problems found
```

`check_dsl.py` 会检查：YAML 能解析、只有一个 start 与一个 answer、所有节点从 start 可达、
所有 `{{#...#}}` 模板引用都有对应变量、**没有任何 `env.` 引用**、每个可能失败的节点
（两个 HTTP 节点与 LLM 节点）都带 `error_strategy: default-value` 与同级 `default_value`——
保证主流程不会因为外部依赖失败而中断；并且核对**导入器需要的结构**：
`kind: app`、`app.mode: advanced-chat`、`dependencies`、`workflow.conversation_variables`、
`workflow.environment_variables`（0.5.0 还需 `workflow.rag_pipeline_variables`）、
HTTP 节点的 `method` 大写、`ssl_verify` 与 `timeout.connect/read/write`、
LLM 节点的 `model.provider` 与 `model.name`。
这些键名与 Dify 自身导出的 DSL 完全一致（对照公开样例 `dzh-gitcode/dify-workflows`、
`wwwzhouhui/dify-for-dsl` 的导出文件核过）。

### DSL 文件的用途与导入顺序

本作品只有**一个** Dify 应用，因此**只导入一个 DSL 文件**：

| 文件 | DSL version | 适用 Dify | 什么时候用 |
| --- | --- | --- | --- |
| `npc-memory-chatflow.yml` | `0.5.0` | Dify ≥ 1.12（当前版本） | **默认用这个** |

如果你的 Dify 更老、导入时报「版本不支持 / app version is too high」，把这个文件里的
`version: 0.5.0` 改成 `version: 0.1.5` 再导入即可——**除了这一行，工作流的节点、连线、
提示词、字段完全一致**，不需要换别的文件。

导入顺序：先按第五节把 **seekdb + 记忆服务**跑起来，再导入 DSL，再按 4.2 绑定模型，最后发布应用。
**导入后不需要设置任何环境变量**：两个 HTTP 节点把记忆服务地址写成了 `http://127.0.0.1:8808`，
与 `service/start.sh` 的默认监听地址一致。

## 四、运行环境

| 组件 | 版本 / 要求 |
| --- | --- |
| seekdb | 官方发行版，MySQL 协议；本文用 `5.7.25-OceanBase seekdb-v1.3.0.0` 验证 |
| Python | 3.10+，仅需 `PyMySQL`（`pip install -r service/requirements.txt`） |
| Dify | **≥ 1.12**（对应 DSL `version: 0.5.0`）；更早的版本把文件里 `version:` 改成 `0.1.5` 即可。Dify Cloud 同样适用 |
| 模型 | 任意能通过 Dify 插件接入的对话模型。DSL 里预置了 `langgenius/openai/openai` + `gpt-4o-mini`，**导入后请在节点里换成你自己有额度、且评审环境能访问的模型**；模型只负责「按资料说话」，不负责检索 |

> 模型插件不在 DSL 里打包（DSL 不含任何凭据）。导入后若掌柜节点显示「模型不可用」，
> 在该节点右上角选择你自己的模型即可，其余节点无需改动。
>
> **流程不会因为外部依赖失败而中断**：三个节点都带降级策略（见第八节），
> 记忆服务不可达或模型调用失败时，掌柜照样给出回复，只是资料为空。

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
  full-text hit: keyword=kettle title_zh=会唱歌的茶壶 relevance=0.9112987714926823
  full-text hit: keyword=lobster title_zh=龙虾节 relevance=0.6307954452608108
```

> `seed.py` 会 `DROP DATABASE IF EXISTS npc_memory_demo` 后重建，可重复执行以复位样例状态。

### 3. 起记忆服务

一条命令（装依赖 → 灌数据 → 起服务）：

```bash
cd dify/service
bash start.sh
```

等价的手工步骤（服务已在运行时用这三条）：

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

1. Dify 控制台 → 「工作室」→ 「导入 DSL」→ 上传 **`npc-memory-chatflow.yml`**
   （只有一个 DSL 文件，不要重复导入）。
2. **不需要设置环境变量**：DSL 里两个 HTTP 节点已经把记忆服务地址写成 `http://127.0.0.1:8808`，
   与 `service/start.sh` 的默认监听地址一致，导入即可用。
   （若 Dify 跑在容器里而记忆服务在宿主机，把两个节点的 `url` 改成
   `http://host.docker.internal:8808/...` 即可；这是唯一需要改的地方。）
3. **配置模型**：DSL 里预置了 `langgenius/openai/openai` + `gpt-4o-mini`，导入后请在「掌柜」节点里
   改成你自己有额度、且评审环境能访问的模型；模型只负责「按资料说话」，不负责检索。
   DSL 不含任何 API Key，导入后需要你在 Dify 里配置自己的模型凭据。
4. 发布应用。
5. **应用入口**：发布后 Dify 会给出应用地址（形如 `http://<你的域名>/chat/<app-id>`），
   在「工作室 → 记得你所有恶作剧的掌柜 → 预览 / API」里都能直接对话；
   也可以用应用 API（`/v1/chat-messages`）按第六节的样例调用。

> 两个 HTTP Request 节点分别指向 `http://127.0.0.1:8808/context` 与 `http://127.0.0.1:8808/memory`，
> 都用 `POST` + JSON body（玩家说的话里有空格、`&`、`#` 也不会把 URL 拼坏）。
> 两个 HTTP 节点各重试 3 次；失败后各自降级为一份空资料/空写入并**继续往下走**，
> 「掌柜」节点也带了重试与 `default_value` 兜底——模型侧偶发失败也不会让整轮对话崩掉。

## 六、验证样例

下面三步在**本机真实跑过**，输出是实际结果。完整记录（含每条 SQL 与 seekdb 侧核对）
见 [`evidence/verification.md`](evidence/verification.md)。

### 样例 1 · 第一次来：掌柜还不认识你

工作流输入：

- 玩家标识：`player-utf8`
- 玩家这次说了什么：`我把房东的茶壶藏起来了`

记忆服务返回（`POST /context`）：

```json
{
  "session_id": "player-utf8",
  "query": "我把房东的茶壶藏起来了",
  "memory": [],
  "lore": [
    {
      "id": 6,
      "keyword": "kettle",
      "title": "the singing kettle",
      "body": "The kettle sings only when the fire is fed with old letters. ...",
      "title_zh": "会唱歌的茶壶",
      "body_zh": "茶壶只有在用旧信纸烧火时才会唱歌，唱的总是写信人想送出去却一直没送的那支曲子。",
      "relevance": 0.9112987714926823
    }
  ]
}
```

> 第一次对话没有任何历史，掌柜不会凭空提起账本；
> 但他**已经能引用设定**——中文提问被 `ik` 分词后命中了中文设定列。

### 样例 2 · 同一个玩家再来：他翻旧账，并引用世界设定

工作流输入：

- 玩家标识：`player-utf8`
- 玩家这次说了什么：`我要再藏一次茶壶`

记忆服务返回（`POST /context`，**这次带回了上一轮的记忆**）：

```json
{
  "session_id": "player-utf8",
  "query": "我要再藏一次茶壶",
  "memory": [
    {
      "turn_no": 1,
      "player_input": "我把房东的茶壶藏起来了",
      "npc_reply": "记下来了。茶壶，失踪，暂记在玩家名下。",
      "created_at": "2026-11-10 12:47:27"
    }
  ],
  "lore": [
    {
      "id": 4,
      "keyword": "prank",
      "title": "the pranking ledger",
      "title_zh": "恶作剧账本",
      "body_zh": "酒馆有一本账本，记着每个客人干过的每一件恶作剧。规矩很简单：记过的恶作剧不许重复。",
      "relevance": 0.6
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
    "title_zh": "龙虾节",
    "body_zh": "一年一度，镇上要在广场烤一只赛龙舟那么大的龙虾。配方是镇上的秘密，上一位厨子失踪在配方写到一半的时候。",
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
2026-11-10 12:47:27,101 INFO insert query: INSERT INTO npc_memory (session_id, turn_no, player_input, npc_reply, created_at) SELECT %s, COALESCE(MAX(turn_no), 0) + 1, %s, %s, NOW() FROM npc_memory WHERE session_id = %s
2026-11-10 12:47:27,140 INFO turns query: SELECT turn_no, player_input, npc_reply, created_at FROM npc_memory WHERE session_id = %s ORDER BY turn_no DESC LIMIT %s
2026-11-10 12:47:27,151 INFO lore query: SELECT id, keyword, title, body, title_zh, body_zh, MATCH(title, body, title_zh, body_zh, tags) AGAINST (%s) AS relevance FROM npc_lore WHERE MATCH(title, body, title_zh, body_zh, tags) AGAINST (%s) ORDER BY relevance DESC LIMIT %s
```

还可以直接在 seekdb 上核对：

```sql
-- 记忆表里确实有这一轮
SELECT session_id, turn_no, player_input, npc_reply FROM npc_memory ORDER BY session_id, turn_no;

-- 全文索引确实建在设定表上（注意 FULLTEXT + ik）
SHOW INDEX FROM npc_lore;

-- 设定检索走的是原生全文索引
SELECT keyword, title_zh, MATCH(title, body, title_zh, body_zh, tags) AGAINST ('lobster kettle') AS relevance
  FROM npc_lore
 WHERE MATCH(title, body, title_zh, body_zh, tags) AGAINST ('lobster kettle')
 ORDER BY relevance DESC LIMIT 3;
```

## 八、异常处理

| 情况 | 行为 |
| --- | --- |
| 客户端中途断开（取消请求、网络抖动） | 记一条 info 日志，不打 traceback，不影响其它请求 |
| seekdb 不可达 | `/health` 与 `/context` 返回 500 与错误信息；`seed.py` 报错退出 |
| 请求路径不存在 | 返回 404 与 `{"ok": false, "error": "unknown path ..."}` |
| 请求体不是合法 JSON | 返回 400 与错误信息 |
| 模型侧超时 / 429 / 5xx | 「掌柜」节点重试 3 次（间隔 1s），仍失败则输出兜底台词继续，流程不崩 |
| 记忆服务地址不可达 | 两个 HTTP 节点各重试 3 次；仍失败则分别降级为「空资料」与「跳过写回」，**对话流程照常走完**，掌柜仍会回话，只是没有记忆与设定可引 |
| 玩家说的话里有空格、`&`、`#` 等特殊字符 | 两个 HTTP 节点都用 `POST` + JSON body，不拼 URL 查询串，不会被截断 |

## 九、已知限制

- 记忆服务是单文件 HTTP 服务，用 Python 标准库实现，只依赖 PyMySQL，方便评审环境快速拉起；
  它**不是**生产级组件，没有鉴权与多副本部署。
- DSL 里把记忆服务地址写死为 `http://127.0.0.1:8808`（与 `service/start.sh` 的默认监听地址一致），
  这样导入后不需要再配环境变量。若 Dify 跑在容器里而记忆服务在宿主机，
  把两个 HTTP 节点的 `url` 改成 `http://host.docker.internal:8808/...`（或容器网络内的服务名）即可。
  地址不可达时流程不会中断，只会退化成「没有资料的掌柜」。
- 记忆按 `session_id` 划分，没有做跨会话的用户身份认证——题目考察的是「上下文是否被真正复用」，
  身份属于作品自己的设计。
- 世界设定是 6 条种子数据（中英双语），通过全文索引检索；设定更多时 `MATCH` 的召回效果会更好。
