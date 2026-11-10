"""Run the Dify verification samples against the running memory service.

Every request here is the request the Dify HTTP Request node makes, so the
output doubles as the evidence that the workflow's two HTTP nodes work.
"""

from __future__ import annotations

import json
import urllib.request

BASE = "http://127.0.0.1:8808"


def post(path: str, payload: dict) -> str:
    request = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)


def get(path: str) -> str:
    with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as response:
        raw = response.read().decode("utf-8")
    return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)


def main() -> int:
    print("### GET /health")
    print(get("/health"))

    print("### POST /context  (sample 1: the keeper does not know you yet)")
    print(
        post(
            "/context",
            {"session_id": "player-utf8", "query": "我把房东的茶壶藏起来了"},
        )
    )

    print("### POST /memory  (the keeper writes this turn down)")
    print(
        post(
            "/memory",
            {
                "session_id": "player-utf8",
                "player_input": "我把房东的茶壶藏起来了",
                "npc_reply": "记下来了。茶壶，失踪，暂记在玩家名下。",
            },
        )
    )

    print("### POST /context  (sample 2: the same player comes back, the keeper reads the ledger)")
    print(
        post(
            "/context",
            {"session_id": "player-utf8", "query": "我要再藏一次茶壶"},
        )
    )

    print("### POST /context  (sample 3: another topic, the full-text index still finds the lore)")
    print(
        post(
            "/context",
            {"session_id": "player-utf8", "query": "龙虾节的配方到底怎么写"},
        )
    )

    print("### POST /context  (a player input that would break a query string)")
    print(
        post(
            "/context",
            {"session_id": "player-utf8", "query": "a & b = c #d 空格也要能过"},
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
