# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Row-level CSV capture so one table record stays addressable as one Source."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
CSV_ROW_SOURCE_VERSION = "1"
CSV_ROW_SOURCE_ID_PREFIX = "csv:"


class CSVDocumentInput(BaseModel):
    """One offline CSV document addressed by a document id and a primary key."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One captured row with its header order and its original field values."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """One captured CSV row that a citation can resolve back to its record."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSourceAdapter:
    """Resolve and read the row-level Source owned by this Definition."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one captured row as the standard textual evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = _row_mapping(source)
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the stable identity of one row inside one document."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    source_id = f"{CSV_ROW_SOURCE_ID_PREFIX}{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row source id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one offline CSV document into validated row inputs."""

    document_id = value.document_id
    primary_key = value.primary_key
    _validate_document_id(document_id)
    if not primary_key:
        raise ValueError("CSV primary key must be a non-empty string")  # noqa: TRY003

    records = _read_records(_decode_document(value.content))
    if not records:
        raise ValueError("CSV document must contain a header record")  # noqa: TRY003

    header = records[0]
    if not header:
        raise ValueError("CSV header record must not be empty")  # noqa: TRY003
    for column in header:
        if not column.strip():
            raise ValueError("CSV header columns must not be blank")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header columns must be unique")  # noqa: TRY003
    if primary_key not in header:
        raise ValueError("CSV primary key must be an exact header column")  # noqa: TRY003

    width = len(header)
    key_index = header.index(primary_key)
    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError(f"CSV row must contain {width} fields, found {len(record)}")  # noqa: TRY003
        primary_key_value = record[key_index]
        if not primary_key_value.strip():
            raise ValueError("CSV primary key value must not be blank")  # noqa: TRY003
        if primary_key_value in seen:
            raise ValueError("CSV primary key values must be unique inside one document")  # noqa: TRY003
        seen.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=tuple(header),
                values=tuple(record),
            )
        )

    # Every returned row must own a formable identity, so an over-long one
    # rejects the whole document instead of an unusable row.
    for row in rows:
        csv_row_source_id(row.document_id, row.primary_key_value)
    return tuple(rows)


def _decode_document(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error
    return text.removeprefix("\ufeff")


def _row_mapping(source: CSVRowSource) -> dict[str, str]:
    """Return one captured row as an ordered field mapping."""

    return dict(zip(source.columns, source.values, strict=True))


def _read_records(text: str) -> list[list[str]]:
    reader = csv.reader(
        io.StringIO(text, newline=""),
        delimiter=",",
        quotechar='"',
        doublequote=True,
        skipinitialspace=False,
        strict=True,
    )
    try:
        return list(reader)
    except csv.Error as error:
        raise ValueError(f"CSV content is malformed: {error}") from error


def _validate_document_id(document_id: str) -> None:
    if not document_id:
        raise ValueError("CSV document id must be a non-empty string")  # noqa: TRY003
    if document_id != document_id.strip():
        raise ValueError("CSV document id must not contain leading or trailing whitespace")  # noqa: TRY003


def _validate_primary_key_value(primary_key_value: str) -> None:
    if not primary_key_value.strip():
        raise ValueError("CSV primary key value must not be blank")  # noqa: TRY003


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_ID_PREFIX",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
