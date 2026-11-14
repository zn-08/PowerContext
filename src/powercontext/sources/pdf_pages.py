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
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import pairwise
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
_NUMBER = re.compile(r"[0-9][0-9.,]*")

# Retrieval tuning.  The values are deliberately generic: they rank one page against
# another for one question and never encode a document, a page or an answer.
_SEARCH_PROBE_LIMIT = 128
_SEARCH_MAX_TERMS = 24
# A query that matches almost every page of the document can not tell the pages of
# the document apart, so its order does not count as evidence.
_SEARCH_INFORMATIVE_SPREAD = 0.5
_SEARCH_MISSING_RANK = 10**6
# The order that the Memory gives the pages of a query is the evidence of the
# query, and the page that a query of the question puts in front is the page that
# carries the metric of the question.
_SEARCH_RANK_OFFSET = 5.0
# A page of the statements of the report carries the numbers of the metrics, and
# there are only a handful of such pages in a report.
_SEARCH_STATEMENT_WEIGHT = 0.2
# The heading of a page names its section.
_SEARCH_HEADER_TOKENS = 40
# The weights of the evidence features of one page, in the order of the features
# that the ranking computes.

# Generic English function words carry no retrieval signal.
_STOPWORDS = frozenset([
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "am",
    "it",
    "its",
    "it's",
    "our",
    "ours",
    "your",
    "yours",
    "their",
    "theirs",
    "this",
    "that",
    "these",
    "those",
    "there",
    "here",
    "what",
    "which",
    "who",
    "whom",
    "whose",
    "when",
    "where",
    "why",
    "how",
    "do",
    "does",
    "did",
    "done",
    "have",
    "has",
    "had",
    "having",
    "can",
    "could",
    "shall",
    "should",
    "will",
    "would",
    "may",
    "might",
    "must",
    "i",
    "me",
    "my",
    "we",
    "us",
    "he",
    "him",
    "his",
    "she",
    "her",
    "they",
    "them",
    "their",
    "not",
    "no",
    "nor",
    "so",
    "than",
    "then",
    "there",
    "here",
    "also",
    "too",
    "very",
    "just",
    "only",
    "even",
    "still",
    "yet",
    "into",
    "onto",
    "upon",
    "about",
    "above",
    "below",
    "under",
    "over",
    "between",
    "among",
    "through",
    "during",
    "before",
    "after",
    "such",
    "same",
    "other",
    "others",
    "another",
    "any",
    "all",
    "both",
    "each",
    "few",
    "more",
    "most",
    "much",
    "many",
    "own",
    "if",
    "else",
    "because",
    "while",
    "since",
    "until",
    "unless",
    "whether",
])

# Wording that frames how an answer should be produced rather than what it asks.
_FRAME_WORDS = frozenset([
    "answer",
    "answers",
    "respond",
    "responds",
    "response",
    "responses",
    "question",
    "questions",
    "ask",
    "asks",
    "asking",
    "calculate",
    "calculates",
    "calculating",
    "compute",
    "computes",
    "computing",
    "define",
    "defines",
    "defining",
    "defined",
    "definition",
    "definitions",
    "assume",
    "assumes",
    "assuming",
    "assumption",
    "rely",
    "relies",
    "relying",
    "based",
    "upon",
    "provide",
    "provides",
    "providing",
    "given",
    "provide",
    "determine",
    "determine",
    "determines",
    "determine",
    "identify",
    "identifies",
    "identifying",
    "explain",
    "explains",
    "explaining",
    "describe",
    "describes",
    "describing",
    "discuss",
    "discusses",
    "discussing",
    "include",
    "includes",
    "including",
    "mention",
    "mentions",
    "mentioning",
    "perspective",
    "perspectives",
    "analyst",
    "analysts",
    "investor",
    "investors",
    "details",
    "detail",
    "shown",
    "shows",
    "show",
    "within",
    "inside",
    "outside",
    "available",
    "available",
    "supplied",
    "context",
    "contexts",
    "information",
    "info",
    "data",
    "numbers",
    "number",
    "values",
    "value",
    "amounts",
    "amount",
    "source",
    "sources",
    "document",
    "documents",
    "report",
    "reports",
    "filing",
    "filings",
    "please",
    "kindly",
    "help",
    "note",
    "notes",
    "round",
    "rounds",
    "decimal",
    "places",
    "address",
    "addressed",
    "give",
    "gives",
    "given",
    "giving",
    "line",
    "lines",
    "item",
    "items",
    "usd",
    "millions",
    "quantity",
    "directly",
    "outlined",
    "explicitly",
    "exclude",
    "excludes",
    "excluding",
    "excluded",
])

# The generic titles of the statements of a financial report.  A question about a
# ratio, a margin or a rate is answered by the statements of the report, and the
# statements carry their title in the heading of the page.
_STATEMENT_TITLES = tuple(
    tuple(title.split())
    for title in (
        "consolidated balance sheet",
        "consolidated balance sheets",
        "consolidated statement of operations",
        "consolidated statements of operations",
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statement of cash flow",
        "consolidated statements of cash flow",
        "consolidated statement of comprehensive income",
        "consolidated statements of comprehensive income",
        "consolidated statement of stockholders",
        "consolidated statements of stockholders",
        "consolidated statement of equity",
        "consolidated statements of equity",
    )
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

# One captured page becomes one Memory entry, and one entry body may not be
# longer than 8192 UTF-8 bytes.  A page whose text is longer than the budget
# of one entry keeps its beginning; the cut prefers a line boundary.
_MEMORY_ENTRY_TEXT_LIMIT = 8192
_PAGE_TEXT_CUT_WINDOW = 256


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
    budget = _page_text_budget(document_id)
    return tuple(
        PDFPageInput(
            document_id=document_id,
            document_sha256=digest,
            page_number=number,
            text=_fit_page_text(text, budget),
        )
        for number, text in enumerate(pages, start=1)
    )


def _page_text_budget(document_id: str) -> int:
    """Return how many bytes of page text fit into one stored Memory entry.

    A captured page becomes one Memory entry, and one entry body may not be
    longer than 8192 UTF-8 bytes.  The entry body of a page is the document id
    and the text of the page, so the text of a page has to leave room for the
    document id.
    """

    used = len(document_id.encode("utf-8")) + 1
    return max(1, _MEMORY_ENTRY_TEXT_LIMIT - used)


def _fit_page_text(text: str, budget: int) -> str:
    """Return the text of one page, cut to the budget of one stored entry.

    A page whose text is longer than the budget keeps its beginning, which is
    the part that carries the title of the page and the beginning of its
    content.  The cut happens at a line boundary whenever the page has one
    close to the budget, so that the last line of the page stays readable.
    """

    if len(text.encode("utf-8")) <= budget:
        return text
    encoded = text.encode("utf-8")[:budget]
    cut = encoded.decode("utf-8", errors="ignore")
    boundary = cut.rfind("\n")
    if boundary > 0 and len(cut) - boundary <= _PAGE_TEXT_CUT_WINDOW:
        cut = cut[:boundary]
    return cut.strip()


async def search_pdf_pages(memory: Any, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Return the pages that best answer one question from an already opened scoped Memory.

    The question is searched as a whole, every informative term of the question is
    searched on its own and every pair of neighbouring terms is searched as a phrase,
    so that the Memory returns the pages that carry the metric of the question.  The
    document that the question is about is told apart from the other documents of the
    scope, and the pages of that document are ordered by how much question evidence
    they carry.  The ordering, the morphological matching and the proximity check are
    offline query processing over the retrieved text.  Every returned citation is an
    exact Memory entry, so a caller can read the page back.
    """

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    if limit < 1 or not question or not question.strip():
        return ()
    terms = _query_terms(question)
    if not terms:
        return ()
    candidates, probes = await _harvest_candidates(memory, question, terms, SearchMemoryRequest)
    if not candidates:
        return ()
    ranked = _rank_candidates(candidates, probes, terms)
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
    positions: dict[str, list[int]] = field(default_factory=dict)
    length: int = 0
    numbers: int = 0
    document: str = ""
    document_tokens: tuple[str, ...] = ()


@dataclass(slots=True)
class _Probe:
    """One query of the Memory, the pages it returned and their order."""

    kind: str
    query: str
    ranks: dict[str, int] = field(default_factory=dict)
    order: dict[str, int] = field(default_factory=dict)
    terms: tuple[str, ...] = ()
    spread: float = 1.0


async def _harvest_candidates(
    memory: Any,
    question: str,
    terms: tuple[str, ...],
    request_class: Any,
) -> tuple[dict[str, _Candidate], list[_Probe]]:
    """Collect the pages the scoped Memory returns for one question.

    The whole question is searched, every informative term of the question is
    searched on its own, and every pair of neighbouring terms is searched as a
    phrase.  A search of two terms admits every page that carries one of them
    and orders the pages that carry both of them first, so the phrases find the
    pages that carry the metric of the question.
    """

    candidates: dict[str, _Candidate] = {}
    probes: list[_Probe] = []
    for kind, query in _probe_queries(question, terms):
        request = request_class(query=query, limit=_SEARCH_PROBE_LIMIT, mode="fts")
        page = await memory.search(request)
        hits = getattr(page, "hits", ())
        probe = _Probe(kind=kind, query=query)
        for rank, hit in enumerate(hits, start=1):
            probe.ranks[hit.entry_id] = rank
            candidate = candidates.get(hit.entry_id)
            if candidate is None:
                candidate = _Candidate(hit=hit)
                candidates[hit.entry_id] = candidate
            candidate.hit = hit
            _index_candidate(candidate, hit.text)
        probes.append(probe)
    return candidates, probes


def _probe_queries(question: str, terms: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return the queries that the Memory is asked for one question."""

    queries: list[tuple[str, str]] = [("question", question)]
    queries.extend(("term", term) for term in terms)
    queries.extend(("bigram", f"{left} {right}") for left, right in pairwise(terms))
    return queries


def _index_candidate(candidate: _Candidate, text: str) -> None:
    """Record the lexical material of one retrieved page."""

    tokens = tuple(_TOKEN.findall(text.casefold()))
    candidate.tokens = tokens
    candidate.length = len(tokens)
    candidate.counts = Counter(tokens)
    candidate.stems = Counter(_stem(token) for token in tokens)
    positions: dict[str, list[int]] = {}
    for index, token in enumerate(tokens):
        positions.setdefault(token, []).append(index)
    candidate.positions = positions
    candidate.numbers = len(_NUMBER.findall(text))
    header, _, _ = text.partition("\n")
    candidate.document = header.strip()
    candidate.document_tokens = tuple(_TOKEN.findall(candidate.document.casefold()))


def _rank_candidates(
    candidates: dict[str, _Candidate],
    probes: list[_Probe],
    terms: tuple[str, ...],
) -> list[Any]:
    """Order the retrieved pages by how much question evidence they carry.

    A question is about one document, and the question usually names it, so the
    pages of the other documents are not candidates for the answer.  Inside the
    document of the question the pages are ordered by the orders that the Memory
    gave them: the page that a query of the question puts in front carries the
    metric of the question, and the pages of the statements of the report carry
    the numbers of the metrics.
    """

    target = _target_document(candidates, probes, terms)
    pages = {
        entry_id: candidate
        for entry_id, candidate in candidates.items()
        if target is None or candidate.document == target
    } or candidates
    corpus = len(pages)
    frequency: Counter[str] = Counter()
    for candidate in pages.values():
        for term in terms:
            if _term_count(candidate, term):
                frequency[term] += 1
    for probe in probes:
        inside = sorted((rank, entry_id) for entry_id, rank in probe.ranks.items() if entry_id in pages)
        probe.order = {entry_id: position for position, (_rank, entry_id) in enumerate(inside, start=1)}
        probe.terms = _probe_terms(probe, terms)
        probe.spread = max((frequency.get(term, 0) for term in probe.terms), default=0) / corpus if corpus else 1.0
    scored: list[tuple[float, int, str, Any]] = []
    for entry_id, candidate in pages.items():
        best = _SEARCH_MISSING_RANK
        best_informative = _SEARCH_MISSING_RANK
        for probe in probes:
            rank = probe.order.get(entry_id)
            if rank is None:
                continue
            if rank < best:
                best = rank
            if probe.spread <= _SEARCH_INFORMATIVE_SPREAD and rank < best_informative:
                best_informative = rank
        score = 1.0 / (_SEARCH_RANK_OFFSET + best_informative)
        score += _SEARCH_STATEMENT_WEIGHT * float(_is_statement_page(candidate.tokens))
        scored.append((score, best, entry_id, candidate.hit))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    return [hit for _score, _best, _entry_id, hit in scored]


def _target_document(
    candidates: dict[str, _Candidate],
    probes: list[_Probe],
    terms: tuple[str, ...],
) -> str | None:
    """Return the document that the question is about.

    A question names its document in most cases, and the name of the document is
    written in front of the text of every one of its pages.  When no term of the
    question names a document, the order of the whole question decides: the
    document whose pages the Memory returns first is the document of the question.
    """

    matches: dict[str, int] = {}
    for candidate in candidates.values():
        document = candidate.document
        matched = _document_match(candidate, terms)
        if matched > matches.get(document, 0):
            matches[document] = matched
    if matches:
        best = max(matches.items(), key=lambda item: (item[1], item[0]))
        if best[1] > 0:
            return best[0]
    weights: dict[str, float] = {}
    for probe in probes:
        if probe.kind != "question":
            continue
        for entry_id, rank in probe.ranks.items():
            candidate = candidates.get(entry_id)
            if candidate is None:
                continue
            weights[candidate.document] = weights.get(candidate.document, 0.0) + 1.0 / (60.0 + rank)
    if weights:
        return max(weights.items(), key=lambda item: (item[1], item[0]))[0]
    return None


def _document_match(candidate: _Candidate, terms: tuple[str, ...]) -> int:
    """Return how many question terms name the document of one page.

    A question is about one document and it usually names it.  The name of the
    document is written in front of the text of every one of its pages, so a page
    whose document carries one of the question terms belongs to the document that
    the question asks about.  Without this signal the pages of the other documents
    win whenever they repeat the generic words of the question.
    """

    tokens = candidate.document_tokens
    stems = {_stem(token) for token in tokens}
    matched = 0
    for term in terms:
        for form in _term_forms(term):
            if form in tokens or _stem(form) in stems:
                matched += 1
                break
            if any(form in token for token in tokens if len(token) >= 4):
                matched += 1
                break
    return matched


def _probe_terms(probe: _Probe, terms: tuple[str, ...]) -> tuple[str, ...]:
    """Return the question terms that one query of the Memory carries."""

    query_tokens = set(_TOKEN.findall(probe.query.casefold()))
    carried: list[str] = []
    for term in terms:
        for form in _term_forms(term):
            if form in query_tokens:
                carried.append(term)
                break
    return tuple(carried)


def _is_statement_page(tokens: tuple[str, ...]) -> bool:
    """Return whether one page carries the title of a statement of the report.

    A question about a ratio, a margin or a rate is answered by the statements
    of the report, and the statements carry their title in the heading of the
    page.  The titles are the generic titles of the statements of a financial
    report; a report that uses other titles simply has no such page.
    """

    header = tokens[:_SEARCH_HEADER_TOKENS]
    for title in _STATEMENT_TITLES:
        size = len(title)
        for index in range(0, max(0, len(header) - size) + 1):
            if header[index : index + size] == title:
                return True
    return False


def _term_count(candidate: _Candidate, term: str) -> int:
    """Return how often one question term occurs in one page."""

    forms = _term_forms(term)
    for form in forms:
        exact = candidate.counts.get(form, 0)
        if exact:
            return exact
    for form in forms:
        stemmed = candidate.stems.get(_stem(form), 0)
        if stemmed:
            return stemmed
    return 0


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


def _within_window(left: list[int], right: list[int], window: int) -> bool:
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
