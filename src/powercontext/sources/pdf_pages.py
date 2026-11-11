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
_SEARCH_PREFIX_DISCOUNT = 0.45
_SEARCH_PREFIX_MIN_LENGTH = 3
_SEARCH_PREFIX_MAX_LENGTH = 8
_SEARCH_COVERAGE_WEIGHT = 0.25
_SEARCH_PROXIMITY_WEIGHT = 0.35
_SEARCH_PROXIMITY_WINDOW = 8
# The weight of a question term that names the document of a page.  It is
# larger than the weight of one term of the text, because a question is about
# one document and the generic words of a question are repeated by the pages of
# every document.
_SEARCH_DOCUMENT_WEIGHT = 8.0
# The page order is the fusion of three orders: the order that the offline scoring
# gives the pages, the order that the Memory gives the pages when it searches the
# whole question, and the best order that the Memory gives a page for one of the
# terms of the question.  The fusion is a reciprocal rank fusion, so that one
# order can not push a page that the other orders do not know into the result.
_SEARCH_RANK_OFFSET = 5
_SEARCH_OFFLINE_WEIGHT = 1.0
_SEARCH_FULL_QUESTION_WEIGHT = 0.5
_SEARCH_ASPECT_WEIGHT = 1.0
# A question is about one document and it usually names it.  The pages of that
# document are worth more than the pages of the other documents, whatever the
# generic words of the question are worth in them.
_SEARCH_DOCUMENT_WEIGHT = 1.0
# How much the number of documents a term appears in counts for its weight.
_SEARCH_DOCUMENT_RARITY_POWER = 2.0

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
    round rounds decimal places address addressed
    give gives given giving line lines item items
    usd millions quantity directly outlined explicitly
    exclude excludes excluding excluded
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
    candidates, ranks, full_ranks = await _harvest_candidates(memory, terms, question, SearchMemoryRequest)
    if not candidates:
        return ()
    ranked = _rank_candidates(candidates, ranks, terms, full_ranks)
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
    document: str = ""
    header: frozenset[str] = frozenset()
    prefixes: dict[str, int] = field(default_factory=dict)


async def _harvest_candidates(
    memory: Any,
    terms: tuple[str, ...],
    question: str,
    request_class: Any,
) -> tuple[dict[str, _Candidate], dict[str, dict[str, int]], dict[str, int]]:
    """Collect the pages the scoped Memory returns for one question.

    Each informative term is searched on its own so that the shared lexical admission
    floor stays satisfiable, and every retrieved page keeps the rank it reached for
    the terms that matched it.  The whole question is searched as well: that search
    ranks the pages with the statistics of the whole corpus, which the offline
    scoring over the harvested candidates can not reproduce.
    """

    candidates: dict[str, _Candidate] = {}
    ranks: dict[str, dict[str, int]] = {}
    full_ranks: dict[str, int] = {}
    for query in (question, *terms):
        request = request_class(query=query, limit=_SEARCH_HARVEST_LIMIT, mode="fts")
        page = await memory.search(request)
        hits = getattr(page, "hits", ())
        if not hits:
            continue
        query_ranks = full_ranks if query == question else ranks.setdefault(query, {})
        for rank, hit in enumerate(hits, start=1):
            entry_id = hit.entry_id
            query_ranks[entry_id] = rank
            candidate = candidates.get(entry_id)
            if candidate is None:
                candidate = _Candidate(hit=hit)
                candidates[entry_id] = candidate
            candidate.hit = hit
            _index_tokens(candidate, hit.text)
    return candidates, ranks, full_ranks


def _index_tokens(candidate: _Candidate, text: str) -> None:
    """Record the token and stem material of one retrieved page."""

    tokens = tuple(_TOKEN.findall(text.casefold()))
    candidate.tokens = tokens
    candidate.length = len(tokens)
    candidate.counts = Counter(tokens)
    candidate.stems = Counter(_stem(token) for token in tokens)
    header, _, _ = text.partition("\n")
    candidate.document = header.strip()
    candidate.header = frozenset(_TOKEN.findall(candidate.document.casefold()))
    # the number of occurrences of every stem that shares a prefix with the
    # stem of a question term is looked up by the first characters of the stem
    prefixes: dict[str, int] = {}
    for stem, count in candidate.stems.items():
        for size in range(_SEARCH_PREFIX_MIN_LENGTH, min(len(stem), _SEARCH_PREFIX_MAX_LENGTH) + 1):
            key = stem[:size]
            if count > prefixes.get(key, 0):
                prefixes[key] = count
    candidate.prefixes = prefixes


def _rank_candidates(
    candidates: dict[str, _Candidate],
    ranks: dict[str, dict[str, int]],
    terms: tuple[str, ...],
    full_ranks: dict[str, int],
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
            count, discount = _term_count(candidate, term)
            if count <= 0:
                continue
            score += weight * discount * _saturation(count, candidate.length, average_length)
        score += _SEARCH_COVERAGE_WEIGHT * len(matched)
        score += _SEARCH_PROXIMITY_WEIGHT * _proximity(candidate, matched, weights)
        scored.append((score, entry_id, candidate.hit))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return _fuse_ranks(
        [hit for _score, _entry_id, hit in scored], ranks, full_ranks, candidates, terms
    )


def _ranks_inside_documents(
    candidates: dict[str, _Candidate], ranks: dict[str, dict[str, int]]
) -> dict[str, int]:
    """Return the best order a page has inside its own document, over all terms.

    The order of a page for one term is only meaningful inside the document of
    the question: the leader of a term in another document is not the leader
    that answers this question.
    """

    result: dict[str, int] = {}
    for term_ranks in ranks.values():
        by_document: dict[str, list[tuple[int, str]]] = {}
        for entry_id, rank in term_ranks.items():
            candidate = candidates.get(entry_id)
            if candidate is None:
                continue
            by_document.setdefault(candidate.document, []).append((rank, entry_id))
        for entries in by_document.values():
            entries.sort()
            for position, (_rank, entry_id) in enumerate(entries, start=1):
                if entry_id not in result or position < result[entry_id]:
                    result[entry_id] = position
    return result


def _fuse_ranks(
    ordered: list[Any],
    ranks: dict[str, dict[str, int]],
    full_ranks: dict[str, int],
    candidates: dict[str, _Candidate],
    terms: tuple[str, ...],
) -> list[Any]:
    """Balance the offline order with the orders that the Memory gives the pages.

    The Memory searches the whole question with the statistics of the whole corpus,
    which is what tells the pages of the document that the question is about apart
    from the pages of the other documents.  The offline order adds the coverage of
    the question terms and their proximity.  The best order of a page for a single
    term keeps a page that answers one aspect of a multi part question in the
    result.  A page of the document that the question names is worth more than a
    page of another document, however well the other document repeats the generic
    words of the question.
    """

    best_ranks: dict[str, int] = {}
    for query_ranks in ranks.values():
        for entry_id, rank in query_ranks.items():
            if entry_id in best_ranks:
                best_ranks[entry_id] = min(best_ranks[entry_id], rank)
            else:
                best_ranks[entry_id] = rank
    # The order of a page for one term is only meaningful inside the document
    # of the question: the leader of a term in another document is not the
    # leader that answers this question.
    document_ranks = _ranks_inside_documents(candidates, ranks)
    for entry_id, rank in document_ranks.items():
        if entry_id in best_ranks:
            best_ranks[entry_id] = min(best_ranks[entry_id], rank)
        else:
            best_ranks[entry_id] = rank
    fused: list[tuple[float, int, Any]] = []
    for rank, hit in enumerate(ordered, start=1):
        score = _SEARCH_OFFLINE_WEIGHT / (_SEARCH_RANK_OFFSET + rank)
        aspect_rank = best_ranks.get(hit.entry_id)
        if aspect_rank is not None:
            score += _SEARCH_ASPECT_WEIGHT / (_SEARCH_RANK_OFFSET + aspect_rank)
        full_rank = full_ranks.get(hit.entry_id)
        if full_rank is not None:
            score += _SEARCH_FULL_QUESTION_WEIGHT / (_SEARCH_RANK_OFFSET + full_rank)
        candidate = candidates.get(hit.entry_id)
        if candidate is not None and _document_match(candidate, terms):
            score += _SEARCH_DOCUMENT_WEIGHT
        fused.append((score, rank, hit))
    fused.sort(key=lambda item: (-item[0], item[1]))
    return [hit for _score, _rank, hit in fused]


def _document_match(candidate: _Candidate, terms: tuple[str, ...]) -> int:
    """Return how many question terms name the document of one page.

    A question is about one document, and the question usually names it.  The
    document is written in front of the text of every page, so a page whose
    document carries one of the question terms is a page of the document that
    the question asks about.  Without this signal the pages of the other
    documents win whenever they repeat the generic words of the question, for
    example "income", "cash" or "statement".
    """

    if not candidate.header:
        return 0
    matched = 0
    for term in terms:
        if term in candidate.header or _stem(term) in candidate.header:
            matched += 1
    return matched


def _matched_terms(candidate: _Candidate, terms: tuple[str, ...]) -> list[str]:
    """Return the question terms one page carries, exactly or by morphology."""

    matched: list[str] = []
    for term in terms:
        count, _discount = _term_count(candidate, term)
        if count:
            matched.append(term)
    return matched


def _term_count(candidate: _Candidate, term: str) -> tuple[int, float]:
    """Return how often a question term occurs in one page and what that counts.

    An exact token counts fully, a token with the same stem counts with the stem
    discount and a token that only shares a prefix with the stem of the term
    counts with the smaller prefix discount.  The suffix rules of the stemmer do
    not catch every morphological pair: "tax" and "taxes", "geography" and
    "geographic" or "margin" and "marginal" are one word for a reader and two
    tokens for a matcher.
    """

    forms = _term_forms(term)
    for form in forms:
        exact = candidate.counts.get(form, 0)
        if exact:
            return exact, 1.0
    for form in forms:
        stemmed = candidate.stems.get(_stem(form), 0)
        if stemmed:
            return stemmed, _SEARCH_STEM_DISCOUNT
    term_stems = [_stem(form) for form in forms]
    loose = 0
    for term_stem in term_stems:
        for size in range(_SEARCH_PREFIX_MIN_LENGTH, min(len(term_stem), _SEARCH_PREFIX_MAX_LENGTH) + 1):
            count = candidate.prefixes.get(term_stem[:size], 0)
            if count > loose:
                loose = count
    if loose:
        return loose, _SEARCH_PREFIX_DISCOUNT
    return 0, 0.0


def _term_forms(term: str) -> tuple[str, ...]:
    """Return the forms of one question term that a page can carry.

    A fiscal year is one token in a question ("fy2022") and a plain year on the
    page ("2022"), so the digits of a term that starts with letters are a form
    of their own.
    """

    forms = [term]
    digits = "".join(character for character in term if character.isdigit())
    if digits and digits != term and term[0].isalpha():
        forms.append(digits)
    return tuple(forms)


def _documents_per_term(
    candidates: dict[str, _Candidate], terms: tuple[str, ...]
) -> Counter[str]:
    """Return how many documents every question term appears in."""

    counts: Counter[str] = Counter()
    for term in terms:
        documents = set()
        for candidate in candidates.values():
            count, _discount = _term_count(candidate, term)
            if count:
                documents.add(candidate.document)
        counts[term] = len(documents)
    return counts


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
