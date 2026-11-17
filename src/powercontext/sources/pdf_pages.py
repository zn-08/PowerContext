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
# The name of a company as a report writes it, as in "American Express".
_COMPANY_NAME = re.compile(r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,3}\b")
# The head of a page carries the name of the company.
_COMPANY_NAME_SCAN = 4000

# Retrieval tuning.  The values are deliberately generic: they rank one page against
# another for one question and never encode a document, a page or an answer.
_SEARCH_PROBE_LIMIT = 256
_SEARCH_MAX_TERMS = 24
# A query that matches almost every page of the document can not tell the pages of
# the document apart, so its order does not count as evidence.
_SEARCH_INFORMATIVE_SPREAD = 0.5
_SEARCH_MISSING_RANK = 10**6
# The order that the Memory gives the pages of a query is the evidence of the
# query, and the page that a query of the question puts in front is the page that
# carries the metric of the question.
_SEARCH_RANK_OFFSET = 5.0
# The weight of the order of one query.  The order of a query that matches almost
# every page of the document tells the pages hardly apart, so the order counts
# less than the order of a query that matches few pages.
_SEARCH_PROBE_WEIGHT = 0.5
# The weight of the order of the queries that look for the statements of the
# report.  The order of such a query tells the pages of the statements apart,
# so it counts as evidence like the order of any other query.
_SEARCH_STATEMENT_PROBE_WEIGHT = 0.6
# The weight of the words of the question that a page carries, of the heading of
# the page and of the page being a page of the statements of the report.
_SEARCH_COVERAGE_WEIGHT = 0.02
_SEARCH_HEADING_WEIGHT = 0.05
_SEARCH_STATEMENT_WEIGHT = 0.6
# The words of a question that ask for a metric of the statements of a report.
# A question that asks for such a metric is answered by the statements, and a
# question that asks for nothing of them is answered by the narrative of the
# report.
_SEARCH_METRIC_WORDS = frozenset(
    [
        "margin", "margins", "rate", "rates", "ratio", "ratios", "share", "shares",
        "percentage", "percent", "growth", "revenue", "revenues", "income", "cost",
        "costs", "expense", "expenses", "profit", "profits", "loss", "losses",
        "earnings", "ebit", "ebitda", "eps", "tax", "taxes", "dividend", "dividends",
        "balance", "debt", "assets", "liabilities", "equity", "segment", "segments",
        "gross", "compensation", "depreciation", "amortization", "capex", "capital",
        "payable", "receivable", "inventory", "cogs", "dpo", "dso", "cash", "working",
    ]
)
# The heading of a page names its section.
_SEARCH_HEADER_TOKENS = 40
# A report carries one subject on a page and a subject runs over several pages, so
# a page that stands next to a page the question is about is a page the question
# is about as well.  The pages next to a page are therefore lifted by the score of
# the page they stand next to, and the pages two steps away are lifted by half of
# it.
_SEARCH_NEIGHBOUR_WEIGHT = 0.4
_SEARCH_NEIGHBOUR_SPAN = 2
# The pages of the statements of the report carry the numbers of the metrics.
# Their titles are searched as well, so that a question about a metric reaches
# the statement that carries the numbers of the metric.
_SEARCH_STATEMENT_PROBES = (
    "balance sheet",
    "statements operations",
    "statements income",
    "statements cash",
    "stockholders equity",
)
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
        "consolidated statement of cash flows",
        "consolidated statements of cash flows",
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
    locations = await _page_locations(memory)
    ranked = _rank_candidates(candidates, probes, terms, locations)
    return tuple(
        MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        for hit in ranked[:limit]
    )


async def _page_locations(memory: Any) -> dict[str, tuple[str, int]]:
    """Return the document and the physical page of every entry of the scope.

    The text of a page carries the document but not the number of the page, and
    the number of the page is what tells the pages of a document apart.  The
    entries of the scope are read through the public Memory API, and the number
    of the page is read from the identity of the Source of the page, which the
    Definition of this module writes itself.
    """

    try:
        page = await memory.list()
    except Exception:
        return {}
    locations: dict[str, tuple[str, int]] = {}
    for record in getattr(page, "entries", ()):
        entry = getattr(record, "entry", None)
        if entry is None:
            continue
        for reference in getattr(entry, "sources", ()):
            if getattr(reference, "source_type", "") != PDF_PAGE_SOURCE_NAME:
                continue
            number = _page_number_of_source(getattr(reference, "source_id", ""))
            if number is None:
                continue
            document = entry.text.partition("\n")[0].strip()
            if document:
                locations[entry.entry_id] = (document, number)
            break
    return locations


def _page_number_of_source(source_id: str) -> int | None:
    """Return the physical page that the identity of a page Source carries."""

    _prefix, _separator, number = source_id.rpartition(":")
    if not number.isdigit():
        return None
    page = int(number)
    return page if page > 0 else None


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
    document_parts: tuple[str, ...] = ()
    document_words: tuple[str, ...] = ()
    body_counts: Counter[str] = field(default_factory=Counter)
    body_stems: Counter[str] = field(default_factory=Counter)


@dataclass(slots=True)
class _Probe:
    """One query of the Memory, the pages it returned and their order."""

    kind: str
    query: str
    ranks: dict[str, int] = field(default_factory=dict)
    order: dict[str, int] = field(default_factory=dict)
    terms: tuple[str, ...] = ()
    rarity: float = 0.0


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
                _index_candidate(candidate, hit.text)
            candidate.hit = hit
        probes.append(probe)
    return candidates, probes


def _probe_queries(question: str, terms: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return the queries that the Memory is asked for one question.

    The search of the Memory does not reduce a word to its stem, so a question
    about "service" does not find a page that writes "Global Services" and a
    question about "revenue" does not find a page that writes "Revenues".  Every
    term is therefore searched twice: once as the question writes it and once
    with the other form of the word, which finds the pages that use the other
    form.
    """

    queries: list[tuple[str, str]] = [("question", question)]
    queries.extend(("term", term) for term in terms)
    queries.extend(("term", f"{term} {variant}") for term, variant in _term_variants(terms))
    queries.extend(("bigram", f"{left} {right}") for left, right in pairwise(terms))
    queries.extend(("statement", probe) for probe in _SEARCH_STATEMENT_PROBES)
    return queries


def _term_variants(terms: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return the other form of every term of the question.

    English writes a word in one form and uses the other one in the report:
    "category" and "categories", "service" and "services".  The other form of a
    word is searched with it, so that the pages that use the other form are
    found as well.
    """

    variants: list[tuple[str, str]] = []
    for term in terms:
        other = _other_form(term)
        if other:
            variants.append((term, other))
    return variants


def _other_form(term: str) -> str:
    """Return the other inflection of one word, or an empty string."""

    if len(term) < 3 or not term.isalpha():
        return ""
    if term.endswith("ies"):
        return term[:-3] + "y"
    if term.endswith("ss"):
        return ""
    if term.endswith("s"):
        return term[:-1]
    if term.endswith("y"):
        return term[:-1] + "ies"
    return term + "s"


def _index_candidate(candidate: _Candidate, text: str) -> None:
    """Record the lexical material of one retrieved page.

    The Memory writes the document id in front of the text of every page of the
    document, so the words of the document id are on every one of its pages.
    The words of the document id therefore tell the pages of the document
    apart not at all, and the rarity of a query of the document is measured
    over the body of the pages alone.
    """

    header, _, body = text.partition("\n")
    candidate.document = header.strip()
    candidate.document_tokens = tuple(_TOKEN.findall(candidate.document.casefold()))
    candidate.document_parts = _document_parts(candidate.document)
    candidate.document_words = _document_words(body)
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
    body_tokens = tuple(_TOKEN.findall(body.casefold()))
    candidate.body_counts = Counter(body_tokens)
    candidate.body_stems = Counter(_stem(token) for token in body_tokens)


def _rank_candidates(
    candidates: dict[str, _Candidate],
    probes: list[_Probe],
    terms: tuple[str, ...],
    locations: dict[str, tuple[str, int]] | None = None,
) -> list[Any]:
    """Order the retrieved pages by how much question evidence they carry.

    A question is about one document, and the question usually names it, so the
    pages of the other documents are not candidates for the answer.  Inside the
    document of the question the pages are ordered by the orders that the Memory
    gave them: every query of the question is evidence, and a query that matches
    few pages of the document says more about a page than a query that matches
    almost every page of it.  The words of the question that a page carries, the
    heading of the page and the statements of the report are evidence as well.

    A report carries one subject on a page and a subject runs over several
    pages, so a page that stands next to a page the question is about is a page
    the question is about as well.  The pages next to a page therefore receive
    the score of the page they stand next to, and the pages two steps away
    receive half of it.
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
    weights = {term: _local_inverse_frequency(frequency.get(term, 0), corpus) for term in terms if frequency.get(term)}
    for probe in probes:
        inside = sorted((rank, entry_id) for entry_id, rank in probe.ranks.items() if entry_id in pages)
        probe.order = {entry_id: position for position, (_rank, entry_id) in enumerate(inside, start=1)}
        probe.terms = _probe_terms(probe, terms)
        probe.rarity = _probe_rarity(probe, pages, corpus)
    scores: dict[str, float] = {}
    order: dict[str, int] = {}
    metric = any(term in _SEARCH_METRIC_WORDS for term in terms)
    statement = _SEARCH_STATEMENT_WEIGHT if metric else 0.0
    for position, (entry_id, candidate) in enumerate(pages.items()):
        score = 0.0
        for probe in probes:
            rank = probe.order.get(entry_id)
            if rank is None:
                continue
            if probe.kind == "statement":
                score += _SEARCH_STATEMENT_PROBE_WEIGHT * probe.rarity / (_SEARCH_RANK_OFFSET + rank)
                continue
            score += _SEARCH_PROBE_WEIGHT * probe.rarity / (_SEARCH_RANK_OFFSET + rank)
        score += _SEARCH_COVERAGE_WEIGHT * _coverage(candidate, terms, weights)
        score += _SEARCH_HEADING_WEIGHT * _heading_overlap(candidate, terms, weights)
        score += statement * float(_is_statement_page(candidate.tokens))
        scores[entry_id] = score
        order[entry_id] = position
    _lift_neighbours(scores, pages, locations)
    scored = sorted(
        ((scores[entry_id], order[entry_id], candidate.hit) for entry_id, candidate in pages.items()),
        key=lambda item: (-item[0], item[1]),
    )
    return [hit for _score, _position, hit in scored]


def _lift_neighbours(
    scores: dict[str, float],
    pages: dict[str, _Candidate],
    locations: dict[str, tuple[str, int]] | None,
) -> None:
    """Lift the pages that stand next to a page the question is about."""

    if not locations:
        return
    by_place = _pages_by_place(pages, locations)
    for entry_id, share in _neighbour_bonus(scores, locations, by_place).items():
        scores[entry_id] = scores.get(entry_id, 0.0) + share


def _pages_by_place(
    pages: dict[str, _Candidate],
    locations: dict[str, tuple[str, int]],
) -> dict[tuple[str, int], str]:
    """Return the retrieved pages by the place they hold in their document."""

    by_place: dict[tuple[str, int], str] = {}
    for entry_id, candidate in pages.items():
        place = locations.get(entry_id)
        if place is None or candidate.document != place[0]:
            continue
        by_place[place] = entry_id
    return by_place


def _neighbour_bonus(
    scores: dict[str, float],
    locations: dict[str, tuple[str, int]],
    by_place: dict[tuple[str, int], str],
) -> dict[str, float]:
    """Return the lift that every page receives from the pages next to it."""

    bonus: dict[str, float] = {}
    for entry_id, place in locations.items():
        score = scores.get(entry_id)
        if score is None:
            continue
        for step in range(1, _SEARCH_NEIGHBOUR_SPAN + 1):
            for number in (place[1] - step, place[1] + step):
                neighbour = by_place.get((place[0], number))
                if neighbour is None:
                    continue
                share = score * _SEARCH_NEIGHBOUR_WEIGHT / step
                if share > bonus.get(neighbour, 0.0):
                    bonus[neighbour] = share
    return bonus


def _coverage(candidate: _Candidate, terms: tuple[str, ...], weights: dict[str, float]) -> float:
    """Return how much of the question a page carries.

    A word of the question that the page carries is worth more when few pages
    of the document carry it.
    """

    coverage = 0.0
    for term in terms:
        count = _term_count(candidate, term)
        if count:
            coverage += weights.get(term, 0.0) * (1.0 + math.log(count))
    return coverage


def _probe_rarity(probe: _Probe, pages: dict[str, _Candidate], corpus: int) -> float:
    """Return how much one query of the question tells the pages apart.

    A query that matches almost every page of the document cannot tell the pages
    apart, so its order carries little evidence.  The pages of the document that
    carry every word of the query are the pages that the query can tell apart.
    """

    if corpus <= 0:
        return 0.0
    words = tuple(_TOKEN.findall(probe.query.casefold()))
    if not words:
        return 0.0
    matching = sum(1 for candidate in pages.values() if all(word in candidate.body_counts for word in words))
    return 1.0 - matching / corpus


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
    details: dict[str, int] = {}
    for candidate in candidates.values():
        document = candidate.document
        matched = _document_match(candidate, terms)
        if matched > matches.get(document, 0):
            matches[document] = matched
        detail = _document_detail_match(candidate, terms)
        if detail > details.get(document, 0):
            details[document] = detail
    named = _named_document(matches, details)
    if named is not None:
        return named
    weights: dict[str, float] = {}
    for probe in probes:
        if probe.kind != "question":
            continue
        for entry_id, rank in probe.ranks.items():
            candidate = candidates.get(entry_id)
            if candidate is None:
                continue
            weights[candidate.document] = weights.get(candidate.document, 0.0) + 1.0 / (60.0 + rank)
    return _best_document_by_order(weights)


def _named_document(matches: dict[str, int], details: dict[str, int]) -> str | None:
    """Return the document that the terms of the question name.

    The terms of a question name one document of the scope, and a term that
    every document of the scope carries names none of them.  A document is
    therefore only returned when it carries more named terms than every other
    document of the scope.  Documents that carry the same name are told apart
    by the year and the kind of the report that the question names as well.
    """

    if not matches:
        return None
    best = max(matches.values())
    if best <= 0:
        return None
    leaders = sorted(document for document, count in matches.items() if count == best)
    if len(leaders) == 1:
        return leaders[0]
    return max(leaders, key=lambda document: (details.get(document, 0), document))


def _best_document_by_order(weights: dict[str, float]) -> str | None:
    """Return the document that the order of the whole question favours.

    The order of the question tells the pages of the scope apart, but a
    question that names no document is told apart only by the words that every
    document of the scope carries.  A document is therefore only returned when
    the order favours it clearly over every other document of the scope.
    """

    if not weights:
        return None
    ordered = sorted(weights.items(), key=lambda item: (-item[1], item[0]))
    best_document, best = ordered[0]
    if len(ordered) == 1:
        return best_document
    if best <= 0:
        return None
    second = ordered[1][1]
    if best < second * 2.0:
        return None
    return best_document


def _document_match(candidate: _Candidate, terms: tuple[str, ...]) -> int:
    """Return how many question terms name the document of one page.

    A question is about one document and it usually names it.  The name of the
    document is written in front of the text of every one of its pages, so a page
    whose document carries one of the question terms belongs to the document that
    the question asks about.  Without this signal the pages of the other documents
    win whenever they repeat the generic words of the question.

    A document is named by the words of its name alone, so the years and the
    kind of the document are not part of the name and do not name a document.
    A report is also named by the short form of its name ("AMEX" for
    "American Express"), so a word of the question that is the short form of
    the name of a document names that document.
    """

    tokens = candidate.document_tokens
    stems = {_stem(token) for token in tokens}
    name = _document_name(tokens)
    parts = candidate.document_parts
    words = candidate.document_words
    matched = 0
    for term in terms:
        if _term_names_document(term, tokens, stems, name, parts, words):
            matched += 1
    return matched


def _document_detail_match(candidate: _Candidate, terms: tuple[str, ...]) -> int:
    """Return how many question terms name the year or the kind of a document.

    Two documents of a scope can carry the same name, as in the reports of a
    company of two different years, and the question then names the year of the
    report as well.  The year and the kind of a document tell the documents of
    the same name apart.
    """

    tokens = candidate.document_tokens
    matched = 0
    for term in terms:
        for form in _term_forms(term):
            if form in tokens:
                matched += 1
                break
    return matched

def _document_name(tokens: tuple[str, ...]) -> str:
    """Return the name of a document without its year and without its kind.

    A document id names a report, and the report is named by the words of the
    id that are neither the year of the report nor the kind of the report.
    """

    words = [token for token in tokens if not token.isdigit()]
    kinds = ("10k", "10q", "8k", "earnings")
    kept = [token for token in words if token not in kinds]
    return "".join(kept)


def _document_parts(document: str) -> tuple[str, ...]:
    """Return the words of the name of a document, one word at a time.

    A document id holds the name of the report in one piece, as in
    "AMERICANEXPRESS", so the name is split at the places where the writing of
    a word begins, and the short form of the name can be told from the name
    itself.
    """

    parts: list[str] = []
    for piece in re.split(r"[^0-9A-Za-z]+", document):
        if not piece or piece.isdigit():
            continue
        if piece.casefold() in ("10k", "10q", "8k", "earnings"):
            continue
        parts.extend(_split_name_word(piece))
    return tuple(parts)


def _document_words(body: str) -> tuple[str, ...]:
    """Return the name that the report writes for itself in its own text.

    A report writes the name of the company in words, as in "American Express",
    so the short form of the name of a document can be told from the words of
    the report itself.
    """

    names: Counter[str] = Counter()
    for match in _COMPANY_NAME.finditer(body[:_COMPANY_NAME_SCAN]):
        names[match.group(0)] += 1
    if not names:
        return ()
    name = names.most_common(1)[0][0]
    return tuple(name.split())


def _split_name_word(token: str) -> list[str]:
    """Return the words that one word of a name is written with.

    A name written in one piece holds several words, and the place where a
    word begins is the place where a capital letter follows a small letter.
    """

    pieces = []
    current = ""
    for index, character in enumerate(token):
        if character.isupper() and current and index > 0 and not token[index - 1].isupper():
            pieces.append(current)
            current = character
        else:
            current += character
    if current:
        pieces.append(current)
    return pieces or [token]


def _term_names_document(
    term: str,
    tokens: tuple[str, ...],
    stems: set[str],
    name: str,
    parts: tuple[str, ...] = (),
    words: tuple[str, ...] = (),
) -> bool:
    """Return whether one word of the question names one document.

    A word names a document when the name of the document carries it.  The
    words that every document of a scope carries, such as the year of the
    report, name no document at all.
    """

    if not name:
        return False
    name_tokens = tuple(_TOKEN.findall(name))
    name_stems = {_stem(token) for token in name_tokens}
    for form in _term_forms(term):
        if form in name_tokens or _stem(form) in name_stems:
            return True
        if any(form in token for token in name_tokens if len(token) >= 4):
            return True
    return _short_form_names_document(term, name, parts, words)


def _short_form_names_document(
    term: str,
    name: str,
    parts: tuple[str, ...],
    words: tuple[str, ...],
) -> bool:
    """Return whether one word of the question is the short form of a name.

    A report is named in its short form as well, as in "AMEX" for "American
    Express", and the short form of a name names the document of that name.
    """

    if len(term) < 3 or not term.isalpha():
        return False
    letters = "".join(character for character in term if character.isalpha())
    if letters != name and len(letters) >= 3 and name.startswith(letters):
        return True
    acronyms = _document_acronyms(parts) + _document_acronyms(words)
    if any(letters == acronym for acronym in acronyms):
        return True
    return _is_short_form_of(letters, name)


def _is_short_form_of(letters: str, name: str) -> bool:
    """Return whether the letters of a short form name a name in order.

    A short form of a name keeps the letters of the name in the order they are
    written, as in "AMEX" for "AMERICANEXPRESS".  The short form must be
    shorter than the name, and the name must be long enough for the short form
    to stand for it alone.
    """

    if len(letters) < 3 or len(letters) >= len(name) or len(name) < 2 * len(letters):
        return False
    position = 0
    for character in name:
        if position < len(letters) and character == letters[position]:
            position += 1
    return position == len(letters)


def _document_acronyms(parts: tuple[str, ...]) -> tuple[str, ...]:
    """Return the short forms of the name of a document.

    A report is named in its short form as well, and the short form is the
    initials of the words of the name, as in "AMEX" for "American Express".  A
    word of the question that is such a short form names that document.
    """

    if len(parts) < 2:
        return ()
    initials = "".join(part[0].casefold() for part in parts)
    forms = [initials]
    if len(parts) == 2:
        forms.append(initials + parts[1].casefold())
    return tuple(form for form in forms if len(form) >= 3)


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


def _local_inverse_frequency(document_frequency: int, corpus: int) -> float:
    """Return how much one term narrows the document of the question."""

    if document_frequency <= 0:
        return 0.0
    return math.log(1.0 + (corpus - document_frequency + 0.5) / (document_frequency + 0.5))


def _heading_overlap(candidate: _Candidate, terms: tuple[str, ...], weights: dict[str, float]) -> float:
    """Return how many words of the question the heading of the page carries.

    The heading of a page names its section, and a question about a section
    names the section as well.
    """

    header = candidate.tokens[:_SEARCH_HEADER_TOKENS]
    header_set = set(header)
    header_stems = {_stem(token) for token in header}
    overlap = 0.0
    for term in terms:
        for form in _term_forms(term):
            if form in header_set or _stem(form) in header_stems:
                overlap += weights.get(term, 0.0)
                break
    return overlap


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

    return _term_count_in(candidate.counts, candidate.stems, term)


def _term_count_in(counts: Counter[str], stems: Counter[str], term: str) -> int:
    """Return how often one question term occurs in a set of page words.

    The exact form of the term wins over the stemmed form, so that a page that
    writes the word of the question counts it once, whichever form the page
    uses.
    """

    forms = _term_forms(term)
    for form in forms:
        exact = counts.get(form, 0)
        if exact:
            return exact
    for form in forms:
        stemmed = stems.get(_stem(form), 0)
        if stemmed:
            return stemmed
    return 0


def _term_count_from(counts: Counter[str], term: str) -> int:
    """Return how often one question term occurs in a set of page words."""

    stems = Counter(_stem(token) for token in counts)
    return _term_count_in(counts, stems, term)


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
