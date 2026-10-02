"""One conference-year, and where its files live.

Every stage used to hardcode `papers.jsonl` and `facts_abstract.jsonl`, which
silently meant "the focus year". Adding ICML 2024 exposed that: `normalize --year
2024` overwrote the 2026 canonical file. A corpus is now named, and its paths are
derived from the name rather than assumed.

    from .corpus import Corpus
    Corpus("ICML", 2026).papers        # data/processed/papers.jsonl
    Corpus("ICML", 2024).papers        # data/processed/papers_2024.jsonl

The focus year keeps the unsuffixed filenames so nothing downstream breaks.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .common import FOCUS_YEAR, INTERIM, PROCESSED, read_jsonl

# Which conference editions the product actually uses. ICML 2024 was collected
# and extracted, then dropped: three years made the vocabulary drift for no
# gain, and the two most recent editions are what a reader asks about. The
# files stay on disk — a scope decision, not a deletion. NeurIPS 2026 does not
# exist yet (its feed is a placeholder with ~200 rows).
ACTIVE = (
    ("ICML", 2025), ("ICML", 2026),
    ("NeurIPS", 2024), ("NeurIPS", 2025),
    ("ICLR", 2025), ("ICLR", 2026),
)
ACTIVE_YEARS = tuple(sorted({y for _, y in ACTIVE}))

# Every venue the pipeline knows, keyed by the lowercase CLI/file name. The
# display name is the venue's own spelling.
VENUES = {
    "icml": "ICML", "iclr": "ICLR", "neurips": "NeurIPS",
    "cvpr": "CVPR", "iccv": "ICCV", "eccv": "ECCV",
    "aaai": "AAAI", "acl": "ACL", "emnlp": "EMNLP", "corl": "CoRL",
}

# The collection scope for benchmark usage (owner, 2026-10-02): the three most
# recent editions of each venue, two for ICCV and ECCV, which alternate years.
# This is what icml.sources collects. It is NOT the ACTIVE list above: a corpus
# joins ACTIVE only once it is extracted and embedded, and adding one there
# re-keys gid (union.json sorts by corpus key), so that is a desktop step.
EDITIONS = (
    ("ICML", 2024), ("ICML", 2025), ("ICML", 2026),
    ("ICLR", 2024), ("ICLR", 2025), ("ICLR", 2026),
    ("NeurIPS", 2023), ("NeurIPS", 2024), ("NeurIPS", 2025),
    ("CVPR", 2024), ("CVPR", 2025), ("CVPR", 2026),
    ("ICCV", 2023), ("ICCV", 2025),
    ("ECCV", 2024), ("ECCV", 2026),
    ("AAAI", 2024), ("AAAI", 2025), ("AAAI", 2026),
    ("ACL", 2024), ("ACL", 2025), ("ACL", 2026),
    ("EMNLP", 2023), ("EMNLP", 2024), ("EMNLP", 2025),
    ("CoRL", 2023), ("CoRL", 2024), ("CoRL", 2025),
)


@dataclass(frozen=True)
class Corpus:
    venue: str = "ICML"
    year: int = FOCUS_YEAR

    @property
    def key(self) -> str:
        return f"{self.venue.lower()}-{self.year}"

    @property
    def is_focus(self) -> bool:
        return self.venue == "ICML" and self.year == FOCUS_YEAR

    def _p(self, stem: str, ext: str, base: Path) -> Path:
        """ICML keeps its historical names (unsuffixed for the focus year,
        `_2025` for the earlier edition) so nothing on disk moves; every other
        venue is fully qualified — `papers_neurips_2025.jsonl` — because a year
        alone collides across venues."""
        if self.venue == "ICML":
            return base / (f"{stem}{ext}" if self.is_focus else f"{stem}_{self.year}{ext}")
        return base / f"{stem}_{self.venue.lower()}_{self.year}{ext}"

    @property
    def papers(self) -> Path:
        return self._p("papers", ".jsonl", PROCESSED)

    @property
    def facts(self) -> Path:
        """Abstract pass — the census. The only valid basis for anything counted,
        ranked or compared across papers (Guardrail 5)."""
        return self._p("facts_abstract", ".jsonl", INTERIM)

    @property
    def facts_fulltext(self) -> Path:
        """Full-text pass, per corpus: arXiv preprints for the focus year, the
        PMLR camera-ready for published years. Enriches a paper's own card only —
        coverage differs by corpus (and by subfield), so nothing may count on it."""
        return self._p("facts_fulltext", ".jsonl", INTERIM)

    @property
    def topics(self) -> Path:
        """Named by corpus key, not year — the labels come from the shared frozen
        vocabulary, so ICML 2025 and a future NeurIPS 2025 are different files."""
        return PROCESSED / f"topics_{self.key}.json"

    def embeddings(self, n: int, model: str) -> Path:
        return PROCESSED / f"emb_{n}_{model.replace('/', '_')}.npy"

    def exists(self) -> bool:
        # size guard: an aborted extraction leaves a 0-byte facts file, and a
        # corpus must not enter the product on the strength of an empty file
        return (self.papers.exists() and self.facts.exists()
                and self.facts.stat().st_size > 0)

    def read_papers(self, with_abstract: bool = False) -> list[dict]:
        rows = list(read_jsonl(self.papers))
        return [p for p in rows if p.get("abstract")] if with_abstract else rows

    def read_facts(self) -> dict[int, dict]:
        return {r["event_id"]: r["facts"] for r in read_jsonl(self.facts) if r.get("ok")}


def available(venue: str | None = None) -> list[Corpus]:
    """Every ACTIVE corpus with both a papers file and an extraction — i.e. the
    ones far enough through the pipeline to be shown. Ordered by (venue order
    as declared, year), so ICML leads and each venue's editions stay together."""
    out = []
    for v, y in ACTIVE:
        if venue and v != venue:
            continue
        c = Corpus(v, y)
        if c.exists():
            out.append(c)
    return out
