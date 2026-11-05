"""Text similarity by shared content words (pure, no I/O).

A neutral primitive for anything that must tell near-duplicate texts apart from different ones:
the news event grouping and the evaluation's market clusters both use it, so that neither has to
depend on the other. Two texts are close when the Jaccard overlap of their content words reaches
a threshold.
"""

import re
from collections import defaultdict
from collections.abc import Sequence

DEFAULT_THRESHOLD = 0.6
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "a", "an", "the", "of", "to", "in", "on", "at", "for", "by", "with", "and", "or", "is",
        "are", "was", "were", "be", "been", "will", "would", "after", "before", "over", "as",
        "from", "into", "its", "it", "this", "that", "new", "says", "say", "said",
    }
)  # fmt: skip


def content_tokens(text: str) -> frozenset[str]:
    return frozenset(w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_texts(texts: Sequence[str], threshold: float = DEFAULT_THRESHOLD) -> list[int]:
    """The cluster of each text, as the index of the first text of its cluster.

    Single linkage: a text joins every earlier text it is close to, so chains are followed. The
    result depends only on the texts and their order.
    """
    tokens = [content_tokens(t) for t in texts]
    parent = list(range(len(texts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    index: dict[str, list[int]] = defaultdict(list)
    for i, toks in enumerate(tokens):
        for j in {j for t in toks for j in index[t]}:
            if jaccard(toks, tokens[j]) >= threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
        for t in toks:
            index[t].append(i)
    return [find(i) for i in range(len(texts))]
