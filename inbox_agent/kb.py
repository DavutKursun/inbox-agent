"""Knowledge base: markdown pages in data/kb/, split by heading, searched with BM25.

A deliberately small version of the docs-chat retriever (github.com/DavutKursun/docs-chat): the KB is
ten pages, so lexical search with section-level chunks is enough and keeps the Space CPU-only.
Each chunk carries a page id (file stem) and heading so drafts can cite "hours#saturday".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import KB_DIR

_TOKEN = re.compile(r"[a-z0-9çğıöşü]+")
# Stopwords include the clinic name: "… Nova Dental Clinic" in a query must not pull the address page to the top.
_STOP = set("a an the and or of to in on at for by with is are be do does how much many what when where which who can i my me you your we our it this that there please nova dental clinic".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower().replace("İ", "i").replace("I", "ı")) if t not in _STOP]


@dataclass
class KBChunk:
    chunk_id: str  # "<page>#<heading-slug>"
    page: str
    heading: str
    text: str


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "top"


def split_markdown(page: str, text: str) -> list[KBChunk]:
    chunks: list[KBChunk] = []
    heading, buf = "top", []
    for line in text.splitlines():
        if line.startswith("#"):
            if "".join(buf).strip():
                chunks.append(KBChunk(f"{page}#{_slug(heading)}", page, heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("#").strip(), []
        else:
            buf.append(line)
    if "".join(buf).strip():
        chunks.append(KBChunk(f"{page}#{_slug(heading)}", page, heading, "\n".join(buf).strip()))
    return chunks


class KnowledgeBase:
    def __init__(self, kb_dir: Path | str = KB_DIR) -> None:
        from rank_bm25 import BM25Plus

        self.chunks: list[KBChunk] = []
        for p in sorted(Path(kb_dir).glob("*.md")):
            self.chunks += split_markdown(p.stem, p.read_text(encoding="utf-8"))
        if not self.chunks:
            raise FileNotFoundError(f"no markdown pages in {kb_dir}")
        self._bm25 = BM25Plus([tokenize(f"{c.page} {c.heading} {c.heading} {c.text}") for c in self.chunks])

    def search(self, query: str, k: int = 3) -> list[dict]:
        scores = self._bm25.get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [{"chunk_id": self.chunks[i].chunk_id, "heading": self.chunks[i].heading, "text": self.chunks[i].text, "score": round(float(scores[i]), 2)}
                for i in order if scores[i] > 0]

    def chunk_ids(self) -> set[str]:
        return {c.chunk_id for c in self.chunks}
