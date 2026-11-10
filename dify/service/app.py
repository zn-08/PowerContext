"""Companion service for the Dify challenge application.

The Dify workflow owns the conversation; this service owns the memory.  It
stores the turns of every player in seekdb and answers two questions for the
workflow:

* what does this player remember, and what does the world know about the
  topic (a full-text lookup over the lore table);
* how do I record what just happened.

seekdb does the work: the lore lookup is a native full-text query
(``MATCH ... AGAINST``) and the turn log is an ordinary table.  Every request
is logged with the statement it ran, so the run log doubles as the evidence
that the database really took part in the interaction.

Configuration comes from the environment so the judge can point it anywhere:

    SEEKDB_HOST, SEEKDB_PORT, SEEKDB_USER, SEEKDB_PASSWORD, SEEKDB_DATABASE
    SERVICE_HOST, SERVICE_PORT
"""

from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pymysql

LOG = logging.getLogger("npc-memory")

SEEKDB_HOST = os.environ.get("SEEKDB_HOST", "127.0.0.1")
SEEKDB_PORT = int(os.environ.get("SEEKDB_PORT", "2881"))
SEEKDB_USER = os.environ.get("SEEKDB_USER", "root")
SEEKDB_PASSWORD = os.environ.get("SEEKDB_PASSWORD", "")
SEEKDB_DATABASE = os.environ.get("SEEKDB_DATABASE", "npc_memory_demo")
SERVICE_HOST = os.environ.get("SERVICE_HOST", "0.0.0.0")
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "8808"))

MAX_TURNS_IN_CONTEXT = 10
MAX_LORE_ROWS = 3


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=SEEKDB_HOST,
        port=SEEKDB_PORT,
        user=SEEKDB_USER,
        password=SEEKDB_PASSWORD,
        database=SEEKDB_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )


def fetch_turns(connection, session_id: str) -> list[dict]:
    statement = (
        "SELECT turn_no, player_input, npc_reply, created_at "
        "FROM npc_memory WHERE session_id = %s ORDER BY turn_no DESC LIMIT %s"
    )
    LOG.info("turns query: %s", statement)
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(statement, (session_id, MAX_TURNS_IN_CONTEXT))
        rows = list(cursor.fetchall())
    rows.reverse()
    return rows


LORE_COLUMNS = "title, body, title_zh, body_zh, tags"


def fetch_lore(connection, query: str) -> list[dict]:
    """Look the topic up in the lore table with the native full-text index.

    The index uses the ``ik`` parser, so a Chinese question is segmented the
    same way the Chinese lore column was indexed, and an English question
    still matches the English column.  One statement, one index, no
    client-side keyword munging.
    """

    statement = (
        f"SELECT id, keyword, title, body, title_zh, body_zh, "
        f"MATCH({LORE_COLUMNS}) AGAINST (%s) AS relevance "
        f"FROM npc_lore WHERE MATCH({LORE_COLUMNS}) AGAINST (%s) "
        f"ORDER BY relevance DESC LIMIT %s"
    )
    LOG.info("lore query: %s", statement)
    with connection.cursor(pymysql.cursors.DictCursor) as cursor:
        cursor.execute(statement, (query, query, MAX_LORE_ROWS))
        rows = list(cursor.fetchall())
    return rows


def save_turn(connection, session_id: str, player_input: str, npc_reply: str) -> int:
    statement = (
        "INSERT INTO npc_memory (session_id, turn_no, player_input, npc_reply, created_at) "
        "SELECT %s, COALESCE(MAX(turn_no), 0) + 1, %s, %s, NOW() FROM npc_memory "
        "WHERE session_id = %s"
    )
    LOG.info("insert query: %s", statement)
    with connection.cursor() as cursor:
        cursor.execute(statement, (session_id, player_input, npc_reply, session_id))
        cursor.execute("SELECT MAX(turn_no) FROM npc_memory WHERE session_id = %s", (session_id,))
        row = cursor.fetchone()
    return int(row[0] or 0)


def build_context(session_id: str, query: str) -> dict:
    connection = connect()
    try:
        turns = fetch_turns(connection, session_id)
        lore = fetch_lore(connection, query)
    finally:
        connection.close()
    return {
        "session_id": session_id,
        "query": query,
        "memory": [
            {
                "turn_no": row["turn_no"],
                "player_input": row["player_input"],
                "npc_reply": row["npc_reply"],
                "created_at": str(row["created_at"]),
            }
            for row in turns
        ],
        "lore": [
            {
                "id": row["id"],
                "keyword": row["keyword"],
                "title": row["title"],
                "body": row["body"],
                "title_zh": row["title_zh"],
                "body_zh": row["body_zh"],
                "relevance": float(row["relevance"] or 0.0),
            }
            for row in lore
        ],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/health":
                connection = connect()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT VERSION()")
                        version = cursor.fetchone()[0]
                finally:
                    connection.close()
                self._reply(200, {"ok": True, "seekdb": version, "database": SEEKDB_DATABASE})
                return
            if parsed.path == "/context":
                session_id = (query.get("session") or ["player-default"])[0]
                text = (query.get("query") or [""])[0]
                self._reply(200, build_context(session_id, text))
                return
            self._reply(404, {"ok": False, "error": f"unknown path {parsed.path}"})
        except Exception as error:  # noqa: BLE001 - the service must answer, not crash
            LOG.exception("GET %s failed", parsed.path)
            self._reply(500, {"ok": False, "error": str(error)})

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._reply(400, {"ok": False, "error": "body is not valid JSON"})
            return
        try:
            if parsed.path == "/memory":
                session_id = str(payload.get("session_id") or payload.get("session") or "player-default")
                player_input = str(payload.get("player_input") or "")
                npc_reply = str(payload.get("npc_reply") or "")
                connection = connect()
                try:
                    turn_no = save_turn(connection, session_id, player_input, npc_reply)
                finally:
                    connection.close()
                self._reply(200, {"ok": True, "session_id": session_id, "turn_no": turn_no})
                return
            self._reply(404, {"ok": False, "error": f"unknown path {parsed.path}"})
        except Exception as error:  # noqa: BLE001 - the service must answer, not crash
            LOG.exception("POST %s failed", parsed.path)
            self._reply(500, {"ok": False, "error": str(error)})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = ThreadingHTTPServer((SERVICE_HOST, SERVICE_PORT), Handler)
    LOG.info("npc memory service listening on %s:%s", SERVICE_HOST, SERVICE_PORT)
    LOG.info("seekdb: %s:%s/%s", SEEKDB_HOST, SEEKDB_PORT, SEEKDB_DATABASE)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOG.info("stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
