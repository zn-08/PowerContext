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

"""Page-level PDF capture so one physical report page stays addressable as one Source."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.artifacts import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_SOURCE_VERSION = "1"
PDF_PAGE_SOURCE_ID_PREFIX = "pdf:"
PDF_PAGE_SOURCE_ID_DIGEST_PREFIX = "pdf-h:"

# The document id is quoted into the Source identity, so a long id would push the
# identity past the shared 256-character bound. The digest fallback keeps the
# identity stable, collision free and bounded for every document id.
_MAX_QUOTED_DOCUMENT_ID = 160
_MAX_PAGE_NUMBER_DIGITS = 20

_TOKEN = re.compile(r"[0-9a-z]+")

# Retrieval tuning. The values are deliberately generic: they rank one page against
# another for one question and never encode a document, a page or an answer.
_SEARCH_HARVEST_LIMIT = 128
_SEARCH_MAX_TERMS = 24
_SEARCH_BM25_K1 = 1.2
_SEARCH_BM25_B = 0.75
_SEARCH_STEM_DISCOUNT = 0.6
_SEARCH_COVERAGE_WEIGHT = 0.25
_SEARCH_PROXIMITY_WEIGHT = 0.35
_SEARCH_PROXIMITY_WINDOW = 8

# Generic English function words carry no retrieval signal.
_STOPWORDS = frozenset(
    """
    a an the and or but for of to in on at by from with as is are was were be been being am
    it its it's our ours your yours their theirs this that these those there here
    what which who whom whose when where why how do does did done
    have has had having can could shall should will would may might must
    i me my we us he him his she her they them their
    not no nor so than then there here also too very just only even still yet
    into onto upon about above below under over between among through during before after
    such same other others another any all both each few more most much many own
    if else because while since until unless whether
    """.split()
)

# Wording that frames how an answer should be produced rather than what it asks.
_FRAME_WORDS = frozenset(
    """
    answer answers respond responds response responses
    question questions ask asks asking
    calculate calculates calculating compute computes computing
    define defines defining defined definition definitions
    assume assumes assuming assumption
    rely relies relying based upon
    provide provides providing given provide
    determine determine determines determine
    identify identifies identifying explain explains explaining
    describe describes describing discuss discusses discussing
    include includes including mention mentions mentioning
    perspective perspectives analyst analysts investor investors
    details detail shown shows show within inside outside
    available available supplied context contexts
    information info data numbers number values value amounts amount
    source sources document documents report reports filing filings
    please kindly help note notes
    """.split()
)

_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("ies", "y"),
    ("sses", "ss"),
    ("ing", ""),
    ("ed", ""),
    ("ly", ""),
    ("al", ""),
    ("ment", ""),
    ("ance", ""),
    ("ence", ""),
    ("s", ""),
)

_STEM_PREFIX = 6
_STEM_MIN_LENGTH = 4


class PDFDocumentInput(BaseModel):
    """One complete report PDF addressed by a document id."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One physical page of a report PDF with the digest of its whole document."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """One captured page that a citation can resolve back to its physical page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSourceAdapter:
    """Resolve and read the page-level Source owned by this Definition."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one captured page as the standard textual evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_SOURCE_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the stable identity of one physical page inside one document revision."""

    _validate_document_id(document_id)
    _validate_document_sha256(document_sha256)
    if page_number < 1:
        raise ValueError("PDF page number must be a positive integer")  # noqa: TRY003
    quoted = quote(document_id, safe="")
    if len(quoted) <= _MAX_QUOTED_DOCUMENT_ID:
        source_id = f"{PDF_PAGE_SOURCE_ID_PREFIX}{quoted}:{document_sha256}:{page_number}"
    else:
        digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"{PDF_PAGE_SOURCE_ID_DIGEST_PREFIX}{digest}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"PDF page source id must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse one complete report PDF into one validated input per physical page."""

    document_id = value.document_id
    _validate_document_id(document_id)
    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    digest = hashlib.sha256(content).hexdigest()
    pages = _extract_pages(content)
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=digest,
            page_number=number,
            text=text,
        )
        for number, text in enumerate(pages, start=1)
    )


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return the pages that best answer one question from an already opened scoped Memory.

    The candidates come from the public Memory search API; the ranking, the morphological
    matching and the proximity check are offline query processing over the retrieved text.
    Every returned citation is an exact Memory entry, so a caller can read the page back.
    """

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    if limit < 1 or not question or not question.strip():
        return ()
    terms = _query_terms(question)
    if not terms:
        return ()
    candidates, ranks = await _harvest_candidates(memory, terms, SearchMemoryRequest)
    if not candidates:
        return ()
    ranked = _rank_candidates(candidates, ranks, terms)
    return tuple(
        MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        for hit in ranked[:limit]
    )


@dataclass(slots=True)
class _Candidate:
    """One retrieved page with the lexical material the ranking needs."""

    hit: Any
    counts: Counter[str] = field(default_factory=Counter)
    stems: Counter[str] = field(default_factory=Counter)
    tokens: tuple[str, ...] = ()
    length: int = 0


async def _harvest_candidates(
    memory: Any,
    terms: tuple[str, ...],
    request_class: Any,
) -> tuple[dict[str, _Candidate], dict[str, dict[str, int]]]:
    """Collect the pages the scoped Memory returns for one question.

    Each informative term is searched on its own so that the shared lexical admission
    floor stays satisfiable, and every retrieved page keeps the rank it reached for
    the terms that matched it.
    """

    candidates: dict[str, _Candidate] = {}
    ranks: dict[str, dict[str, int]] = {}
    for term in terms:
        request = request_class(query=term, limit=_SEARCH_HARVEST_LIMIT, mode="fts")
        page = await memory.search(request)
        hits = getattr(page, "hits", ())
        if not hits:
            continue
        term_ranks = ranks.setdefault(term, {})
        for rank, hit in enumerate(hits, start=1):
            entry_id = hit.entry_id
            term_ranks[entry_id] = rank
            candidate = candidates.get(entry_id)
            if candidate is None:
                candidate = _Candidate(hit=hit)
                candidates[entry_id] = candidate
            candidate.hit = hit
            _index_tokens(candidate, hit.text)
    return candidates, ranks


def _index_tokens(candidate: _Candidate, text: str) -> None:
    """Record the token and stem material of one retrieved page."""

    tokens = tuple(_TOKEN.findall(text.casefold()))
    candidate.tokens = tokens
    candidate.length = len(tokens)
    candidate.counts = Counter(tokens)
    candidate.stems = Counter(_stem(token) for token in tokens)


def _rank_candidates(
    candidates: dict[str, _Candidate],
    ranks: dict[str, dict[str, int]],
    terms: tuple[str, ...],
) -> list[Any]:
    """Order the retrieved pages by how much question evidence they carry."""

    corpus = max(1, len(candidates))
    document_frequency: Counter[str] = Counter()
    for candidate in candidates.values():
        document_frequency.update(candidate.stems.keys())
    weights = {term: _inverse_frequency(document_frequency.get(_stem(term), 0), corpus) for term in terms}
    average_length = max(1.0, sum(candidate.length for candidate in candidates.values()) / corpus)
    scored: list[tuple[float, str, Any]] = []
    for entry_id, candidate in candidates.items():
        matched = _matched_terms(candidate, terms)
        if not matched:
            continue
        score = 0.0
        for term in matched:
            weight = weights.get(term, 0.0)
            if weight <= 0.0:
                continue
            exact = candidate.counts.get(term, 0)
            if exact:
                score += weight * _saturation(exact, candidate.length, average_length)
            else:
                stemmed = candidate.stems.get(_stem(term), 0)
                score += weight * _SEARCH_STEM_DISCOUNT * _saturation(stemmed, candidate.length, average_length)
        score += _SEARCH_COVERAGE_WEIGHT * len(matched)
        score += _SEARCH_PROXIMITY_WEIGHT * _proximity(candidate, matched, weights)
        scored.append((score, entry_id, candidate.hit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [hit for _score, _entry_id, hit in scored]


def _matched_terms(candidate: _Candidate, terms: tuple[str, ...]) -> list[str]:
    """Return the question terms one page carries, exactly or by morphology."""

    matched: list[str] = []
    for term in terms:
        if candidate.counts.get(term) or candidate.stems.get(_stem(term)):
            matched.append(term)
    return matched


def _inverse_frequency(document_frequency: int, corpus: int) -> float:
    """Return how much one term narrows the corpus."""

    if document_frequency <= 0:
        return 0.0
    return math.log(1.0 + corpus / document_frequency)


def _saturation(term_frequency: int, length: int, average_length: float) -> float:
    """Return the saturated frequency contribution of one term in one page."""

    if term_frequency <= 0:
        return 0.0
    normalization = 1.0 - _SEARCH_BM25_B + _SEARCH_BM25_B * (length / average_length)
    return term_frequency * (_SEARCH_BM25_K1 + 1.0) / (term_frequency + _SEARCH_BM25_K1 * normalization)


def _proximity(candidate: _Candidate, matched: list[str], weights: dict[str, float]) -> float:
    """Return how tightly the matched question terms sit together in one page."""

    if len(matched) < 2:
        return 0.0
    positions: dict[str, list[int]] = {}
    for index, token in enumerate(candidate.tokens):
        for term in matched:
            if token == term or _stem(token) == _stem(term):
                positions.setdefault(term, []).append(index)
    total = 0.0
    for left_index, left in enumerate(matched):
        for right in matched[left_index + 1 :]:
            if not _within_window(positions.get(left, ()), positions.get(right, ())):
                continue
            total += min(weights.get(left, 0.0), weights.get(right, 0.0))
    return total


def _within_window(left: list[int], right: list[int], window: int = _SEARCH_PROXIMITY_WINDOW) -> bool:
    """Return whether two term occurrences sit within the proximity window."""

    if not left or not right:
        return False
    right_index = 0
    for position in left:
        while right_index < len(right) and right[right_index] < position - window:
            right_index += 1
        if right_index < len(right) and right[right_index] <= position + window:
            return True
    return False


def _query_terms(question: str) -> tuple[str, ...]:
    """Return the informative terms of one question in the order they appear."""

    terms: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN.findall(question.casefold()):
        if token in _STOPWORDS or token in _FRAME_WORDS:
            continue
        if len(token) == 1 and not token.isdigit():
            continue
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
        if len(terms) >= _SEARCH_MAX_TERMS:
            break
    return tuple(terms)


def _stem(token: str) -> str:
    """Return the morphological stem of one token.

    The stem is a conservative prefix of the token after the common English endings are
    removed, so that a question word and the word the document uses reach the same stem
    ("materially" and "material", "statements" and "statement").
    """

    if len(token) <= _STEM_MIN_LENGTH:
        return token
    for suffix, replacement in _SUFFIX_RULES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _STEM_MIN_LENGTH:
            return token[: -len(suffix) + len(replacement)][:_STEM_PREFIX]
    return token[:_STEM_PREFIX]


def _extract_pages(content: bytes) -> tuple[str, ...]:
    """Return the extractable text of every physical page of one PDF."""

    # pypdf is provided by the shared evaluation environment rather than by the product
    # dependency set, so it is imported only when a document is actually parsed.
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError(f"PDF content is not a readable document: {error}") from error
    if reader.is_encrypted:
        try:
            decrypted = reader.decrypt("")
        except Exception as error:
            raise ValueError(f"PDF content cannot be decrypted: {error}") from error
        if not decrypted:
            raise ValueError("PDF content requires a password")  # noqa: TRY003
    try:
        pages = list(reader.pages)
    except Exception as error:
        raise ValueError(f"PDF content cannot be parsed completely: {error}") from error
    texts: list[str] = []
    for page in pages:
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise ValueError(f"PDF content cannot be parsed completely: {error}") from error
        texts.append(text.strip())
    return tuple(texts)


def _validate_document_id(document_id: str) -> None:
    if not document_id or not document_id.strip():
        raise ValueError("PDF document id must not be empty or blank")  # noqa: TRY003


def _validate_document_sha256(document_sha256: str) -> None:
    if len(document_sha256) != 64 or any(character not in "0123456789abcdef" for character in document_sha256):
        raise ValueError("PDF document digest must be a lowercase SHA-256 hexadecimal string")  # noqa: TRY003


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_ID_DIGEST_PREFIX",
    "PDF_PAGE_SOURCE_ID_PREFIX",
    "PDF_PAGE_SOURCE_NAME",
    "PDF_PAGE_SOURCE_VERSION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
