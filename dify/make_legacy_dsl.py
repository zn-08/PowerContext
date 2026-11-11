"""Make the legacy copy of the chatflow DSL from the current one.

The legacy copy is the same flow with the older DSL version, for a Dify that
does not accept the newer one.
"""

from __future__ import annotations

import io
import sys

MAIN = "dify/npc-memory-chatflow.yml"
LEGACY = "dify/npc-memory-chatflow-legacy.yml"


def main() -> int:
    text = io.open(MAIN, encoding="utf-8").read()
    if "version: 0.5.0" not in text:
        raise SystemExit("the main file does not carry version 0.5.0")
    legacy = text.replace("version: 0.5.0", "version: 0.1.5", 1)
    # the pipeline variables of the knowledge base do not exist in the old format
    legacy = legacy.replace("  rag_pipeline_variables: []\n", "")
    io.open(LEGACY, "w", encoding="utf-8", newline="\n").write(legacy)
    print(f"wrote {LEGACY} ({len(legacy)} characters)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
