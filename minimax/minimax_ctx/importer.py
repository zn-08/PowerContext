"""Import engineering material into seekdb.

Material is described by a manifest so the import is explicit and repeatable:
every entry names a repository relative path, and the importer decides how to
split that file into retrievable units (specification sections, source symbols
and test functions).
"""

from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from minimax_ctx.materials import KIND_CODE, KIND_DOC, KIND_SPEC, KIND_TEST, Material

MANIFEST_NAME = "materials.manifest.json"

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_TEST_FUNCTION = re.compile(r"^def (test_[A-Za-z0-9_]+)\(", re.MULTILINE)
_TEST_CLASS = re.compile(r"^class (Test[A-Za-z0-9_]+)\b", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class Manifest:
    """The declared material of one project."""

    specs: tuple[str, ...]
    code: tuple[str, ...]
    tests: tuple[str, ...]
    docs: tuple[str, ...]

    @staticmethod
    def load(path: Path) -> "Manifest":
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return Manifest(
            specs=tuple(payload.get("specs", [])),
            code=tuple(payload.get("code", [])),
            tests=tuple(payload.get("tests", [])),
            docs=tuple(payload.get("docs", [])),
        )

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "specs": list(self.specs),
            "code": list(self.code),
            "tests": list(self.tests),
            "docs": list(self.docs),
        }


def find_repo_root(start: Path) -> Path:
    """Return the repository root that owns the given directory."""

    current = start.resolve()
    for candidate in [current, *current.parents]:
        if (candidate / MANIFEST_NAME).exists() or (candidate / "pyproject.toml").exists():
            return candidate
    return current


def _split_markdown(path: str, text: str) -> list[tuple[str, str]]:
    """Split one Markdown file into ``(section, body)`` units."""

    sections: list[tuple[str, str]] = []
    title = path.rsplit("/", 1)[-1]
    current_title = title
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            sections.append((current_title, body))

    for line in text.splitlines():
        match = _HEADING.match(line)
        if match:
            flush()
            buffer = []
            current_title = f"{title}#{match.group(2).strip()}"
            continue
        buffer.append(line)
    flush()
    return sections or [(title, text.strip())]


def _split_python(path: str, text: str) -> list[tuple[str, str]]:
    """Split one Python file into its module body and its top-level symbols."""

    units: list[tuple[str, str]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [(path.rsplit("/", 1)[-1], text)]

    lines = text.splitlines()
    module_lines: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            symbol = node.name
            units.append((symbol, "\n".join(lines[node.lineno - 1 : end])))
        else:
            module_lines.extend(lines[node.lineno - 1 : (getattr(node, "end_lineno", node.lineno) or node.lineno)])
    header = "\n".join(module_lines).strip()
    if header:
        units.insert(0, (path.rsplit("/", 1)[-1], header))
    return units


def _split_tests(path: str, text: str) -> list[tuple[str, str]]:
    """Split one test file into the file itself and its test functions."""

    units: list[tuple[str, str]] = [(path.rsplit("/", 1)[-1], text)]
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return units
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            units.append((node.name, "\n".join(lines[node.lineno - 1 : end])))
    return units


def _title_for(relative: str, symbol: str) -> str:
    name = relative.rsplit("/", 1)[-1]
    return f"{name}::{symbol}" if symbol and symbol != name else name


def _language_for(relative: str) -> str:
    suffix = relative.rsplit(".", 1)[-1] if "." in relative else ""
    return {
        "py": "python",
        "md": "markdown",
        "mdx": "markdown",
        "toml": "toml",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "sh": "shell",
    }.get(suffix, suffix or "text")


def _read(repo_root: Path, relative: str) -> str | None:
    """Read one declared file, or report that it is not present yet.

    A manifest may name a file that only exists after the change under test
    (the new test file, for example).  The import then records what exists
    today instead of refusing to run.
    """

    absolute = repo_root / relative
    if not absolute.exists():
        print(f"[import] skipping missing file: {relative}", file=sys.stderr)
        return None
    return absolute.read_text(encoding="utf-8")


def collect_materials(repo_root: Path, manifest: Manifest) -> list[Material]:
    """Turn the manifest into the material rows that are stored in seekdb."""

    materials: list[Material] = []
    seen: set[str] = set()

    def add(kind: str, relative: str, symbol: str, title: str, content: str) -> None:
        material = Material.build(
            kind=kind,
            path=relative,
            title=title,
            content=content,
            symbol=symbol,
            language=_language_for(relative),
        )
        if material.material_id in seen:
            return
        seen.add(material.material_id)
        materials.append(material)

    for relative in manifest.specs:
        text = _read(repo_root, relative)
        if text is None:
            continue
        for symbol, body in _split_markdown(relative, text):
            add(KIND_SPEC, relative, symbol, _title_for(relative, symbol), body)

    for relative in manifest.docs:
        text = _read(repo_root, relative)
        if text is None:
            continue
        for symbol, body in _split_markdown(relative, text):
            add(KIND_DOC, relative, symbol, _title_for(relative, symbol), body)

    for relative in manifest.code:
        text = _read(repo_root, relative)
        if text is None:
            continue
        if relative.endswith(".py"):
            units = _split_python(relative, text)
        else:
            units = [(relative.rsplit("/", 1)[-1], text)]
        for symbol, body in units:
            add(KIND_CODE, relative, symbol, _title_for(relative, symbol), body)

    for relative in manifest.tests:
        text = _read(repo_root, relative)
        if text is None:
            continue
        if relative.endswith(".py"):
            units = _split_tests(relative, text)
        else:
            units = [(relative.rsplit("/", 1)[-1], text)]
        for symbol, body in units:
            add(KIND_TEST, relative, symbol, _title_for(relative, symbol), body)

    return materials


__all__ = [
    "MANIFEST_NAME",
    "Manifest",
    "collect_materials",
    "find_repo_root",
]
