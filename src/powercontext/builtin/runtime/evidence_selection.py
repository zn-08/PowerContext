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

"""Select query-relevant fragments from the original text of Memory candidates.

Retrieval decides *which* memories are candidates; this module decides *which part*
of each candidate is worth delivering. Every fragment is a contiguous slice of the
candidate's original text, so a caller can verify the fragment against the entry it
came from and cite the exact character range.

The selector is pure and offline: it never calls a model, never rewrites the source
text, and never invents evidence. When the caller's output budget cannot hold a
useful fragment, it returns nothing rather than an abbreviated quote.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from powercontext.builtin.artifacts.search import analyze_text

#: Query words that carry no evidence signal on their own.
_FUNCTION_WORDS = frozenset([
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "both",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "don",
    "each",
    "few",
    "for",
    "from",
    "had",
    "has",
    "have",
    "here",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "many",
    "may",
    "might",
    "more",
    "most",
    "must",
    "my",
    "no",
    "nor",
    "not",
    "now",
    "of",
    "on",
    "or",
    "other",
    "our",
    "own",
    "same",
    "shall",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "too",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
])
#: Suffixes stripped when two terms should count as the same word.
_SUFFIXES = ("ations", "ation", "ingly", "edly", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s")
#: A term shorter than this never counts as a prefix match for a longer one.
_MIN_PREFIX_LENGTH = 5
#: Longest prefix difference two terms may have and still count as one word.
_MAX_PREFIX_DISTANCE = 3
#: Score a near-miss term keeps relative to an exact hit.
_STEM_WEIGHT = 0.6
#: Score a shared word prefix keeps relative to an exact hit.
_PREFIX_WEIGHT = 0.3
#: Extra score one shared number adds; numbers separate otherwise similar abstracts.
_NUMBER_WEIGHT = 1.2
#: How strongly a sentence is discounted for being longer than the average one.
_LENGTH_PENALTY = 0.25
#: How strongly a sentence is preferred for sitting near the end of its candidate.
_POSITION_WEIGHT = 2.0
#: How strongly a long sentence run is discounted relative to a short one.
_WINDOW_LENGTH_PENALTY = 0.15
#: How strongly one candidate's own best sentence lifts all of its fragments.
_DOCUMENT_BIAS = 1.5
#: How much a candidate's leading sentence counts as evidence rather than as a title.
_TITLE_WEIGHT = 0.3
#: Longest fragment the selector proposes on its own, in UTF-8 bytes.
_MAX_WINDOW_BYTES = 1200
#: Most fragments one candidate may contribute to the proposal list.
_MAX_WINDOWS_PER_CANDIDATE = 1024
#: Longest fragment list one build may deliver.
_MAX_FRAGMENTS = 4


@dataclass(frozen=True)
class EvidenceCandidate:
    """One candidate Memory entry the selector may cut fragments from."""

    key: str
    text: str


@dataclass(frozen=True)
class EvidenceSpan:
    """One contiguous fragment of original text, addressed on its source candidate."""

    key: str
    start: int
    end: int
    text: str


@dataclass
class _Sentence:
    """One sentence of a candidate, with the character range it occupies.

    This is the selector's working record: ``score`` is filled in while the candidates
    are scored against the query.
    """

    index: int
    start: int
    end: int
    text: str
    terms: tuple[str, ...]
    term_set: frozenset[str]
    stems: frozenset[str]
    numbers: frozenset[str]
    score: float = 0.0
    evidence_score: float = 0.0


@dataclass(frozen=True)
class _Window:
    """One contiguous run of sentences proposed as a single fragment."""

    key: str
    start: int
    end: int
    text: str
    score: float


def select_evidence_spans(
    query: str,
    candidates: Sequence[EvidenceCandidate],
    fits: Callable[[Sequence[EvidenceSpan]], bool],
) -> tuple[EvidenceSpan, ...]:
    """Choose query-relevant fragments that fit the caller's output budget.

    The selector proposes contiguous fragments of the candidates' original text,
    ranked by how well they answer ``query``, and keeps the highest ranked ones
    that ``fits`` accepts. ``fits`` receives the candidate fragment list and
    reports whether the caller's complete output still fits its budget, so the
    byte accounting stays with whoever renders the output.

    Fragments never overlap: a later proposal that covers an already chosen range
    is skipped, because repeating characters would not add evidence.
    """

    if not candidates:
        return ()
    documents = [_document(candidate) for candidate in candidates]
    documents = [document for document in documents if document.sentences]
    if not documents:
        return ()
    query_terms = _query_terms(query)
    if not query_terms:
        return ()
    _score_documents(documents, query_terms)
    proposals = _propose(documents)
    chosen: list[EvidenceSpan] = []
    for window in proposals:
        if any(
            span.key == window.key and window.start < span.end and span.start < window.end
            for span in chosen
        ):
            continue
        candidate = EvidenceSpan(key=window.key, start=window.start, end=window.end, text=window.text)
        if not fits([*chosen, candidate]):
            continue
        chosen.append(candidate)
        if len(chosen) >= _MAX_FRAGMENTS:
            break
    return tuple(chosen)


def _query_terms(query: str) -> tuple[str, ...]:
    """Return the distinct evidence terms of one query."""

    terms = {
        term
        for term in analyze_text(query).split()
        if term and not (term.isalpha() and term in _FUNCTION_WORDS)
    }
    return tuple(sorted(terms))


def _document(candidate: EvidenceCandidate) -> _Document:
    """Split one candidate into scored sentences."""

    return _Document(key=candidate.key, text=candidate.text, sentences=_sentences(candidate.text))


@dataclass(frozen=True)
class _Document:
    """One candidate and the sentences cut from it."""

    key: str
    text: str
    sentences: tuple[_Sentence, ...]


def _sentences(text: str) -> tuple[_Sentence, ...]:
    """Cut one text into sentences without breaking inside a number or an abbreviation."""

    sentences: list[_Sentence] = []
    for index, (start, end) in enumerate(_sentence_ranges(text)):
        body = text[start:end]
        terms = tuple(analyze_text(body).split())
        sentences.append(
            _Sentence(
                index=index,
                start=start,
                end=end,
                text=body,
                terms=terms,
                term_set=frozenset(terms),
                stems=frozenset(_stem(term) for term in terms),
                numbers=frozenset(term for term in terms if any(char.isdigit() for char in term)),
            )
        )
    return tuple(sentences)


def _sentence_ranges(text: str) -> list[tuple[int, int]]:
    """Return the character ranges of the sentences in one text.

    A boundary is a sentence terminator, whitespace, and a word that starts a new
    sentence. A terminator that is not followed by whitespace belongs to a number
    such as ``1.5`` or to an abbreviation such as ``e.g``, so it never ends a
    sentence. A lowercase word does not start one either, because ``et al.`` and
    ``Fig. 2`` continue the same sentence.
    """

    ranges: list[tuple[int, int]] = []
    length = len(text)
    start = 0
    index = 0
    while index < length:
        character = text[index]
        if character in ".!?":
            look = index + 1
            while look < length and text[look] == " ":
                look += 1
            if look < length and _starts_sentence(text[look]):
                chunk = text[start:index]
                lead = len(chunk) - len(chunk.lstrip())
                if chunk.strip():
                    ranges.append((start + lead, index + 1))
                start = look
                index = look
                continue
        index += 1
    chunk = text[start:]
    lead = len(chunk) - len(chunk.lstrip())
    if chunk.strip():
        ranges.append((start + lead, start + len(chunk.rstrip())))
    return ranges


def _starts_sentence(character: str) -> bool:
    """Return whether one character opens a new sentence."""

    if not character.isalpha():
        return False
    return character.isupper() or not character.isascii()


def _stem(term: str) -> str:
    """Return the crude stem of one term, or the term itself."""

    if not term.isalpha() or len(term) <= 4:
        return term
    for suffix in _SUFFIXES:
        if len(term) > len(suffix) + 3 and term.endswith(suffix):
            base = term[: -len(suffix)]
            if suffix == "ies":
                return f"{base}y"
            if suffix in {"ations", "ation"}:
                return f"{base}e"
            return base
    return term


def _score_documents(documents: Sequence[_Document], query_terms: Sequence[str]) -> None:
    """Score every sentence of every document against the query terms."""

    sentences = [sentence for document in documents for sentence in document.sentences]
    total = len(sentences) or 1
    frequency: Counter[str] = Counter()
    for sentence in sentences:
        frequency.update(sentence.term_set)
    average = statistics.mean(len(sentence.terms) for sentence in sentences) if sentences else 1.0
    numbers = {term for term in query_terms if any(char.isdigit() for char in term)}
    for document in documents:
        count = len(document.sentences)
        for sentence in document.sentences:
            sentence.score = _sentence_score(sentence, query_terms, frequency, total, numbers, average, count)
            # A candidate often opens with its title. The title identifies the candidate
            # well but is not evidence, so it counts less inside a delivered fragment.
            sentence.evidence_score = sentence.score * (_TITLE_WEIGHT if sentence.index == 0 else 1.0)


def _sentence_score(
    sentence: _Sentence,
    query_terms: Sequence[str],
    frequency: Counter[str],
    total: int,
    numbers: frozenset[str],
    average: float,
    count: int,
) -> float:
    """Return the relevance of one sentence to the query."""

    score = 0.0
    for term in set(query_terms):
        weight = math.log(1.0 + total / (1.0 + frequency.get(term, 0)))
        if term in sentence.term_set:
            score += weight
        elif _stem(term) in sentence.stems:
            score += weight * _STEM_WEIGHT
        elif _shares_prefix(term, sentence.term_set):
            score += weight * _PREFIX_WEIGHT
    shared = numbers & sentence.numbers
    score += _NUMBER_WEIGHT * len(shared)
    score /= 1.0 + _LENGTH_PENALTY * (len(sentence.terms) / max(1.0, average))
    # A claim is written from a paper's own conclusion, so the closing sentences
    # of a candidate carry more of its evidence than its opening ones.
    score += _POSITION_WEIGHT / (1.0 + (count - 1 - sentence.index))
    return score


def _shares_prefix(term: str, others: Iterable[str]) -> bool:
    """Return whether one term shares a word prefix with a term of the sentence."""

    if len(term) < _MIN_PREFIX_LENGTH:
        return False
    return any(
        (term.startswith(other) or other.startswith(term)) and abs(len(term) - len(other)) <= _MAX_PREFIX_DISTANCE
        for other in others
        if len(other) >= _MIN_PREFIX_LENGTH
    )


def _propose(documents: Sequence[_Document]) -> list[_Window]:
    """Return the ranked fragments the selector may deliver."""

    proposals: list[_Window] = []
    for document in documents:
        best = max(document.sentences, key=lambda sentence: sentence.score)
        for window in _windows(document):
            proposals.append(
                _Window(
                    key=document.key,
                    start=window.start,
                    end=window.end,
                    text=window.text,
                    score=best.score * _DOCUMENT_BIAS + window.score,
                )
            )
    proposals.sort(key=lambda window: (-window.score, window.end - window.start))
    return proposals


def _windows(document: _Document) -> list[_Window]:
    """Return the contiguous sentence runs worth delivering from one document.

    A very long candidate would produce a quadratic number of runs, so only the
    best-scoring runs are kept. The bound is far above the number of runs a normal
    document produces, so it never narrows the choice for ordinary candidates.
    """

    windows: list[_Window] = []
    sentences = document.sentences
    for first in range(len(sentences)):
        size = 0
        total = 0.0
        for last in range(first, len(sentences)):
            size += len(sentences[last].text.encode("utf-8"))
            if size > _MAX_WINDOW_BYTES:
                break
            total += sentences[last].evidence_score
            windows.append(
                _Window(
                    key=document.key,
                    start=sentences[first].start,
                    end=sentences[last].end,
                    text=document.text[sentences[first].start : sentences[last].end],
                    score=total / (1.0 + _WINDOW_LENGTH_PENALTY * (last - first)),
                )
            )
    if len(windows) > _MAX_WINDOWS_PER_CANDIDATE:
        windows.sort(key=lambda window: (-window.score, window.end - window.start))
        windows = windows[:_MAX_WINDOWS_PER_CANDIDATE]
    return windows


__all__ = [
    "EvidenceCandidate",
    "EvidenceSpan",
    "select_evidence_spans",
]
