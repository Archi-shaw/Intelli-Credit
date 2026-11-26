"""Split page text into overlapping, paragraph-aware chunks that keep their page reference."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    index: int
    page_number: int | None
    text: str


def chunk_pages(pages: list[tuple[int | None, str]], max_chars: int = 900, overlap: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    for page_no, text in pages:
        text = re.sub(r"[ \t]+", " ", text or "").strip()
        if not text:
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n|\n(?=[A-Z0-9(])", text) if p.strip()]
        buf = ""
        for para in paras:
            while len(para) > max_chars:
                head, para = para[:max_chars], para[max_chars - overlap:]
                if buf:
                    chunks.append(Chunk(len(chunks), page_no, buf))
                    buf = ""
                chunks.append(Chunk(len(chunks), page_no, head))
            if len(buf) + len(para) + 1 > max_chars and buf:
                chunks.append(Chunk(len(chunks), page_no, buf))
                buf = buf[-overlap:] + "\n" + para if overlap else para
            else:
                buf = (buf + "\n" + para) if buf else para
        if buf.strip():
            chunks.append(Chunk(len(chunks), page_no, buf))
    return chunks
