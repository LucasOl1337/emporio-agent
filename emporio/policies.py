"""Split the store policy manual into numbered sections.

The manual is eight pages with a clear "N. Title" / "N.N Title" structure,
so section-level retrieval is enough: the model asks for a section by
topic and gets the exact text, which it can quote. No embeddings needed
at this size (see README, "Retrieval").
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from .store import normalize

HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-ZÁÉÍÓÚÂÊÔÃÕÇ][^\n]{2,80})$")
NOISE = re.compile(r"^(Página \d+|Empório da Música Manual de Políticas e Procedimentos.*)$")


@dataclass
class Section:
    number: str
    title: str
    text: str

    @property
    def top(self) -> str:
        return self.number.split(".")[0]


def load_sections(pdf_path: Path) -> list[Section]:
    reader = PdfReader(str(pdf_path))
    lines: list[str] = []
    for page in reader.pages:
        # Layout mode: the default mode splits this PDF one word per line.
        for line in (page.extract_text(extraction_mode="layout") or "").splitlines():
            line = re.sub(r"\s+", " ", line.replace("\u200b", "")).strip()
            if line and not NOISE.match(line):
                lines.append(line)

    sections: list[Section] = []
    current: Section | None = None
    for line in lines:
        m = HEADING.match(line)
        if m and (current is None or _is_next(current.number, m.group(1))):
            current = Section(m.group(1), m.group(2).strip(), "")
            sections.append(current)
        elif current is not None:
            current.text += line + "\n"
    return sections


def _is_next(prev: str, cand: str) -> bool:
    """Accept a heading only if it plausibly follows the previous one.

    Guards against list items like "1. Saudação:" inside section 7.2.
    """
    p = [int(x) for x in prev.split(".")]
    c = [int(x) for x in cand.split(".")]
    if len(c) == 1:
        return c[0] == p[0] + 1
    return (c[0] == p[0] and (len(p) == 1 and c[1] == 1 or len(p) == 2 and c[1] == p[1] + 1)) or (
        c[0] == p[0] + 1 and c[1] == 1
    )


class PolicyIndex:
    def __init__(self, sections: list[Section]):
        self.sections = sections

    def table_of_contents(self) -> list[str]:
        return [f"{s.number} {s.title}" for s in self.sections]

    def get(self, ref: str) -> list[Section]:
        """Return a section and its subsections, by number ("4", "4.1") or title words."""
        ref = ref.strip()
        if re.fullmatch(r"\d{1,2}(\.\d{1,2})?", ref):
            if "." in ref:
                return [s for s in self.sections if s.number == ref]
            return [s for s in self.sections if s.top == ref]
        words = [w for w in normalize(ref).split() if len(w) > 2]
        scored = []
        for s in self.sections:
            hay = normalize(s.title + " " + s.text)
            title = normalize(s.title)
            score = sum(3 if w in title else hay.count(w) > 0 for w in words)
            if score:
                scored.append((score, s))
        scored.sort(key=lambda t: -t[0])
        return [s for _, s in scored[:3]]
