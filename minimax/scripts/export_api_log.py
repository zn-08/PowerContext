"""Turn the local MiniMax API log into a reviewable evidence file.

The script refuses to write anything that contains a credential: the raw key is
never part of the record in the first place, but the check is cheap and the
evidence file is going into a public repository.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/minimax-api-log.jsonl")
TARGET = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docs/evidence/minimax-api-log.jsonl")

SECRET_MARKERS = ("sk-", "Bearer ", "api_key", "MINIMAX_API_KEY=")


def main() -> int:
    records = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    successful = [record for record in records if record.get("answer")]
    if not successful:
        print("no successful record found")
        return 1
    record = successful[-1]

    finish_reason = str(record.get("finish_reason") or "")
    if finish_reason == "length":
        print("refusing to write: the answer was cut off by the token limit")
        return 1

    blob = json.dumps(record, ensure_ascii=False)
    for marker in SECRET_MARKERS:
        if marker in blob:
            print(f"refusing to write: the record contains {marker!r}")
            return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(blob + "\n", encoding="utf-8", newline="\n")
    print("wrote", TARGET, len(blob), "bytes")
    print("model:", record.get("model"))
    print("timestamp:", record.get("timestamp"))
    print("usage:", record.get("usage"))
    print("finish_reason:", finish_reason or "(none)")
    print("answer chars:", len(record.get("answer") or ""))
    print("context_digest:", (record.get("metadata") or {}).get("context_digest"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
