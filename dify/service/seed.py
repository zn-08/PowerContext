"""Create and fill the seekdb tables the Dify application reads.

The schema is intentionally small: one table for the world lore, one for the
turn log of every player.  The lore table carries a native full-text index so
the workflow can ask "what does the world know about this topic" with a
``MATCH ... AGAINST`` query instead of a scan.

Every lore row is **bilingual**: the English columns carry the original tavern
canon, the Chinese columns carry the same setting in the language the players
actually type in.  The full-text index uses the ``ik`` parser so a Chinese
query such as ``龙虾节的配方`` is segmented and matched against the Chinese
column of the same row, and an English query such as ``lobster`` still matches
the English column.

Run it once against the seekdb instance the application will use::

    python seed.py
"""

from __future__ import annotations

import os

import pymysql

HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
USER = os.environ.get("SEEKDB_USER", "root")
PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
DATABASE = os.environ.get("SEEKDB_DATABASE", "npc_memory_demo")

# (keyword, title_en, body_en, title_zh, body_zh, tags)
LORE = [
    (
        "lamp",
        "moon lamp",
        "The moon lamp hangs above the tavern door and only lights up when someone "
        "says a word that rhymes with 'lobster'. The keeper claims it is broken.",
        "月灯",
        "酒馆门上的月灯，只在有人念出跟『龙虾』押韵的词时才会亮。掌柜嘴上说它是坏的。",
        "lamp,tavern,light,lobster,rhyme",
    ),
    (
        "lobster",
        "lobster festival",
        "Once a year the villagers cook a lobster the size of a rowing boat in the "
        "square. The recipe is a state secret and the last cook vanished mid-recipe.",
        "龙虾节",
        "一年一度，镇上要在广场烤一只赛龙舟那么大的龙虾。配方是镇上的秘密，"
        "上一位厨子失踪在配方写到一半的时候。",
        "lobster,festival,village,recipe,secret",
    ),
    (
        "tavern",
        "the crooked tavern",
        "The crooked tavern leans three degrees to the left, which is why the regulars "
        "always walk in circles and the landlord insists the ale is to blame.",
        "歪脖子酒馆",
        "歪脖子酒馆朝左歪三度，所以常客进门总绕圈走，房东坚持说是麦芽酒的问题。",
        "tavern,landlord,ale,inn",
    ),
    (
        "prank",
        "the pranking ledger",
        "The inn keeps a ledger of every prank ever played. The rule is simple: never "
        "repeat a prank that has already been written down.",
        "恶作剧账本",
        "酒馆有一本账本，记着每个客人干过的每一件恶作剧。规矩很简单："
        "记过的恶作剧不许重复。",
        "prank,ledger,inn,rule,repeat",
    ),
    (
        "memory",
        "memory of the night watch",
        "The night watch remembers everything that happened under the moon lamp, and "
        "it never forgets a face. That is why the villagers trust it and fear it.",
        "夜巡的记忆",
        "夜巡记着月灯底下发生过的所有事，一张脸也不忘。所以镇民既信它，也怕它。",
        "memory,watch,moon lamp,trust,night",
    ),
    (
        "kettle",
        "the singing kettle",
        "The kettle sings only when the fire is fed with old letters. The tune it "
        "sings is always the one the writer wanted to send but never did.",
        "会唱歌的茶壶",
        "茶壶只有在用旧信纸烧火时才会唱歌，唱的总是写信人想送出去却一直没送的那支曲子。",
        "kettle,fire,letters,tune,tea",
    ),
]

SEED_TURNS = [
    ("player-default", "我把房东的茶壶藏起来了", "记下来了。房东已经在数第三遍茶叶罐，账本上写着：茶壶，失踪，暂记在房东名下。"),
]


def main() -> int:
    connection = pymysql.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        charset="utf8mb4",
        autocommit=True,
    )
    with connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {DATABASE}")
        cursor.execute(f"CREATE DATABASE {DATABASE} DEFAULT CHARSET utf8mb4")
        cursor.execute(f"USE {DATABASE}")
        cursor.execute(
            """
            CREATE TABLE npc_lore (
              id INT AUTO_INCREMENT PRIMARY KEY,
              keyword VARCHAR(64) NOT NULL,
              title VARCHAR(128) NOT NULL,
              body TEXT NOT NULL,
              title_zh VARCHAR(128) NOT NULL DEFAULT '',
              body_zh TEXT NOT NULL,
              tags VARCHAR(255) NOT NULL,
              FULLTEXT INDEX ft_npc_lore (title, body, title_zh, body_zh, tags)
                WITH PARSER ik
            ) DEFAULT CHARSET=utf8mb4
            """
        )
        cursor.execute(
            """
            CREATE TABLE npc_memory (
              id BIGINT AUTO_INCREMENT PRIMARY KEY,
              session_id VARCHAR(64) NOT NULL,
              turn_no INT NOT NULL,
              player_input TEXT NOT NULL,
              npc_reply TEXT NOT NULL,
              created_at DATETIME NOT NULL,
              UNIQUE KEY uk_npc_memory_turn (session_id, turn_no)
            ) DEFAULT CHARSET=utf8mb4
            """
        )
        cursor.executemany(
            "INSERT INTO npc_lore (keyword, title, body, title_zh, body_zh, tags) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            LORE,
        )
        for session_id, player_input, npc_reply in SEED_TURNS:
            cursor.execute(
                "INSERT INTO npc_memory (session_id, turn_no, player_input, npc_reply, created_at) "
                "VALUES (%s, 1, %s, %s, NOW())",
                (session_id, player_input, npc_reply),
            )
        cursor.execute("SELECT COUNT(*) FROM npc_lore")
        lore_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM npc_memory")
        turn_count = cursor.fetchone()[0]
        cursor.execute(
            "SELECT keyword, title_zh, MATCH(title, body, title_zh, body_zh, tags) AGAINST (%s) AS relevance "
            "FROM npc_lore WHERE MATCH(title, body, title_zh, body_zh, tags) AGAINST (%s) "
            "ORDER BY relevance DESC LIMIT 2",
            ("lobster kettle", "lobster kettle"),
        )
        sample = cursor.fetchall()
    connection.close()
    print(f"database {DATABASE}: lore rows={lore_count}, memory rows={turn_count}")
    for keyword, title_zh, relevance in sample:
        print(f"  full-text hit: keyword={keyword} title_zh={title_zh} relevance={relevance}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
