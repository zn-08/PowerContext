"""seekdb storage for engineering material.

The module keeps three kinds of material in seekdb:

* ``spec`` -- specification entries that state what a change must satisfy,
* ``code`` -- the source files a change is expected to touch,
* ``test`` -- the tests that must prove the change.

Every row keeps its repository relative path, so a context pack can point a
reviewer at a file that really exists.  A full-text index over the title and the
body is what turns a task description into the material that applies to it.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import pymysql
from pymysql.cursors import DictCursor

from minimax_ctx.config import Settings

KIND_SPEC = "spec"
KIND_CODE = "code"
KIND_TEST = "test"
KIND_DOC = "doc"
KINDS = (KIND_SPEC, KIND_CODE, KIND_TEST, KIND_DOC)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS material (
  material_id VARCHAR(191) NOT NULL PRIMARY KEY,
  kind        VARCHAR(16)  NOT NULL,
  path        VARCHAR(512) NOT NULL,
  title       VARCHAR(256) NOT NULL,
  symbol      VARCHAR(191) NOT NULL DEFAULT '',
  language    VARCHAR(32)  NOT NULL DEFAULT '',
  content     MEDIUMTEXT    NOT NULL,
  sha256      CHAR(64)     NOT NULL,
  imported_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) DEFAULT CHARSET=utf8mb4
"""


def _render_params(params: Sequence[Any]) -> str:
    """Render query parameters for the SQL log without dumping whole documents."""

    rendered = []
    for value in params:
        text = str(value)
        if len(text) > 120:
            text = f"{text[:117]}..."
        rendered.append(text)
    return f"({', '.join(rendered)})"


@dataclass(frozen=True, slots=True)
class Material:
    """One imported piece of engineering material."""

    material_id: str
    kind: str
    path: str
    title: str
    content: str
    symbol: str = ""
    language: str = ""
    sha256: str = ""

    @staticmethod
    def build(
        *,
        kind: str,
        path: str,
        title: str,
        content: str,
        symbol: str = "",
        language: str = "",
    ) -> "Material":
        if kind not in KINDS:
            raise ValueError(f"unknown material kind {kind!r}, expected one of {KINDS}")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        material_id = f"{kind}:{path}"
        if symbol:
            material_id = f"{material_id}#{symbol}"
        return Material(
            material_id=material_id,
            kind=kind,
            path=path,
            title=title,
            content=content,
            symbol=symbol,
            language=language,
            sha256=digest,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "kind": self.kind,
            "path": self.path,
            "title": self.title,
            "symbol": self.symbol,
            "language": self.language,
            "sha256": self.sha256,
            "bytes": len(self.content.encode("utf-8")),
        }


class MaterialStore:
    """Read and write engineering material in seekdb."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # -- connection -------------------------------------------------------

    def connect(self, *, with_database: bool = True) -> Any:
        database = self._settings.seekdb_database if with_database else ""
        connection = pymysql.connect(
            host=self._settings.seekdb_host,
            port=self._settings.seekdb_port,
            user=self._settings.seekdb_user,
            password=self._settings.seekdb_password,
            database=database or None,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=10,
        )
        return connection

    def _execute(self, cursor: Any, sql: str, params: Sequence[Any] | None = None) -> Any:
        if self._settings.log_sql:
            print(f"[seekdb] {sql.strip()}", file=sys.stderr)
            if params:
                print(f"[seekdb] params={_render_params(params)}", file=sys.stderr)
        cursor.execute(sql, params or ())
        return cursor

    def _execute_many(self, cursor: Any, sql: str, params: Sequence[Sequence[Any]]) -> Any:
        if self._settings.log_sql:
            print(f"[seekdb] {sql.strip()} ({len(params)} rows)", file=sys.stderr)
        cursor.executemany(sql, params)
        return cursor

    # -- schema -----------------------------------------------------------

    def ensure_schema(self) -> None:
        """Create the material table and its full-text index."""

        connection = self.connect(with_database=False)
        try:
            with connection.cursor() as cursor:
                self._execute(
                    cursor,
                    f"CREATE DATABASE IF NOT EXISTS `{self._settings.seekdb_database}` "
                    "DEFAULT CHARSET=utf8mb4",
                )
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                self._execute(cursor, _SCHEMA)
            connection.commit()
        finally:
            connection.close()

        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                self._execute(cursor, "SHOW INDEX FROM material WHERE Key_name = 'ft_material'")
                if not cursor.fetchall():
                    self._execute(
                        cursor,
                        "ALTER TABLE material ADD FULLTEXT INDEX ft_material (title, content) WITH PARSER ik",
                    )
            connection.commit()
        finally:
            connection.close()

    # -- writing ----------------------------------------------------------

    def replace_all(self, materials: Iterable[Material]) -> int:
        """Replace the stored material with the given set."""

        rows = list(materials)
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                self._execute(cursor, "DELETE FROM material")
                if rows:
                    self._execute_many(
                        cursor,
                        "INSERT INTO material (material_id, kind, path, title, symbol, language, content, sha256) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        [
                            (
                                row.material_id,
                                row.kind,
                                row.path,
                                row.title,
                                row.symbol,
                                row.language,
                                row.content,
                                row.sha256,
                            )
                            for row in rows
                        ],
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return len(rows)

    # -- reading ----------------------------------------------------------

    def count(self) -> int:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                self._execute(cursor, "SELECT COUNT(*) AS total FROM material")
                row = cursor.fetchone()
                return int(row["total"]) if row else 0
        finally:
            connection.close()

    def get(self, material_id: str) -> Material | None:
        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                self._execute(cursor, "SELECT * FROM material WHERE material_id = %s", (material_id,))
                row = cursor.fetchone()
        finally:
            connection.close()
        if not row:
            return None
        return Material(
            material_id=row["material_id"],
            kind=row["kind"],
            path=row["path"],
            title=row["title"],
            content=row["content"],
            symbol=row["symbol"],
            language=row["language"],
            sha256=row["sha256"],
        )

    def search(self, query: str, *, kind: str | None = None, limit: int = 8) -> list[dict[str, Any]]:
        """Return the material that applies to ``query``.

        The full-text index ranks the rows; when the index cannot answer the
        question (an empty result for a very short or very unusual term) the
        store falls back to a path and title scan so a context pack never
        silently loses its sources.
        """

        terms = [term for term in query.replace("\n", " ").split(" ") if term.strip()]
        if not terms:
            return []

        connection = self.connect()
        try:
            with connection.cursor() as cursor:
                kind_clause = " AND kind = %s" if kind else ""
                params: list[Any] = [query, query]
                if kind:
                    params.append(kind)
                params.append(limit)
                self._execute(
                    cursor,
                    "SELECT material_id, kind, path, title, symbol, language, sha256, "
                    "MATCH(title, content) AGAINST (%s) AS relevance "
                    "FROM material WHERE MATCH(title, content) AGAINST (%s)"
                    f"{kind_clause} ORDER BY relevance DESC, material_id ASC LIMIT %s",
                    params,
                )
                rows = list(cursor.fetchall())
                if rows:
                    return rows
                like_clause = " OR ".join(["title LIKE %s", "path LIKE %s", "content LIKE %s"] * len(terms))
                like_params: list[Any] = []
                for term in terms:
                    like_params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
                if kind:
                    like_params.append(kind)
                self._execute(
                    cursor,
                    "SELECT material_id, kind, path, title, symbol, language, sha256, 0 AS relevance "
                    f"FROM material WHERE ({like_clause}){' AND kind = %s' if kind else ''} "
                    "ORDER BY path ASC LIMIT %s",
                    [*like_params, limit],
                )
                return list(cursor.fetchall())
        finally:
            connection.close()


__all__ = [
    "KINDS",
    "KIND_CODE",
    "KIND_DOC",
    "KIND_SPEC",
    "KIND_TEST",
    "Material",
    "MaterialStore",
]
