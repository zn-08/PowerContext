"""Allow ``python -m minimax_ctx`` to run the command line interface."""

from __future__ import annotations

from minimax_ctx.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
