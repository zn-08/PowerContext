# Reading Notes on Hybrid Retrieval

Hybrid retrieval combines a lexical branch with a semantic branch and fuses the two rankings. Reciprocal Rank Fusion is the usual choice because it only consumes ranks, not raw scores, so the two branches do not need calibration.

Full text search with an inverted index is unbeatable for rare identifiers such as error codes or author names. Dense vectors are weak on those but strong on paraphrases. That asymmetry is exactly why hybrid retrieval beats either branch alone.

Latency matters: the lexical branch usually costs single digit milliseconds while an approximate nearest neighbour scan over millions of vectors costs tens of milliseconds. Running both in parallel keeps the tail latency close to the slower branch.

Relevance feedback is cheap to implement and surprisingly effective. Remembering which documents a user opened after a query lets the next query boost the same cluster of documents.
