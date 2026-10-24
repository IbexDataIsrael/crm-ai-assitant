"""Split markdown documents into small chunks for retrieval.

Each chunk keeps the filename, the document title and the section heading it
sits under; title and heading are embedded with the text, so a passage like
"A bilingual CRM ..." still knows it is "Jewish Points CRM - User Guide >
1. What this system is". Headings and titles are context only, never a chunk
of their own. Chunk size is measured in the embedding model's own tokens
(including special tokens and that context), so a chunk is never longer than
the model can read. If something cannot be made to fit, we raise an error
instead of letting the model silently cut it off.

Headings: markdown `#` lines. A document with no `#` lines (the user guides)
uses short numbered lines such as "1. What this system is" instead, and a
short first line before the first section is its title. In a document that
has `#` headings, numbered lines are ordinary steps (the test fixtures).
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("uvicorn.error")

# How many lines a new chunk repeats from the end of the previous chunk in the
# same section, and how many words when one long line has to be split.
OVERLAP_LINES = 1
OVERLAP_WORDS = 8

# A numbered section line: "3. Who can do what (roles)". Short, and not a
# sentence ending in "." - so "1. Count the cash with a second volunteer." is a step.
NUMBERED_SECTION = re.compile(r"^\d{1,2}\.\s+\S")
MAX_SECTION_WORDS = 8
MAX_TITLE_WORDS = 12


@dataclass(frozen=True)
class Chunk:
    filename: str
    heading: str
    text: str
    title: str = ""

    @property
    def context(self) -> str:
        """Where the text sits: "title > heading", or just the heading."""
        if self.title and self.title != self.heading:
            return f"{self.title} > {self.heading}"
        return self.heading

    @property
    def embed_text(self) -> str:
        """The text that is actually turned into a vector (title and heading give context)."""
        return f"{self.context}\n{self.text}"


def is_numbered_section(line: str) -> bool:
    return bool(NUMBERED_SECTION.match(line)) and len(line.split()) <= MAX_SECTION_WORDS and not line.endswith(".")


def split_document(markdown: str, filename: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Return the document title and its (heading, lines) sections.

    Text before any heading uses the filename as heading. A section with no
    body lines produces nothing, so a heading is never a passage by itself.
    """
    lines = [raw.strip() for raw in markdown.splitlines() if raw.strip()]
    has_markdown_headings = any(line.startswith("#") for line in lines)

    def is_heading(line: str) -> bool:
        return line.startswith("#") if has_markdown_headings else is_numbered_section(line)

    title = ""
    if lines and lines[0].startswith("#"):
        title = lines[0].lstrip("#").strip()
    elif len(lines) > 1 and not has_markdown_headings and is_heading(lines[1]) and len(lines[0].split()) <= MAX_TITLE_WORDS:
        title, lines = lines[0], lines[1:]

    sections = []
    heading = filename
    body: list[str] = []
    for line in lines:
        if is_heading(line):
            if body:
                sections.append((heading, body))
            heading = line.lstrip("#").strip()
            body = []
        else:
            body.append(line)
    if body:
        sections.append((heading, body))
    return title, sections


def _split_long_line(line: str, fits: Callable[[str], bool]) -> list[str]:
    """Split one line that is too long into word windows, with a small overlap."""
    words = line.split()
    pieces = []
    start = 0
    while start < len(words):
        end = start + 1
        if not fits(" ".join(words[start:end])):
            raise ValueError(f"A single word is too long for the model: {words[start][:40]!r}")
        while end < len(words) and fits(" ".join(words[start : end + 1])):
            end += 1
        pieces.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = max(end - OVERLAP_WORDS, start + 1)
    return pieces


def chunk_document(
    filename: str,
    markdown: str,
    count_tokens: Callable[[str], int],
    max_tokens: int,
) -> list[Chunk]:
    """Split one document into chunks of at most max_tokens model tokens."""
    chunks = []
    title, sections = split_document(markdown, filename)
    for heading, lines in sections:

        def fits(text: str) -> bool:
            return count_tokens(Chunk(filename, heading, text, title).embed_text) <= max_tokens

        units = []
        for line in lines:
            units.extend([line] if fits(line) else _split_long_line(line, fits))

        current: list[str] = []
        for unit in units:
            if not current or fits("\n".join(current + [unit])):
                current.append(unit)
                continue
            chunks.append(Chunk(filename, heading, "\n".join(current), title))
            overlap = current[-OVERLAP_LINES:]
            current = overlap + [unit] if fits("\n".join(overlap + [unit])) else [unit]
        if current:
            chunks.append(Chunk(filename, heading, "\n".join(current), title))

    # Final check: never hand the model a chunk it would have to truncate.
    for chunk in chunks:
        size = count_tokens(chunk.embed_text)
        if size > max_tokens:
            raise ValueError(
                f"Chunk from {chunk.filename} ({chunk.heading}) has {size} tokens, "
                f"limit is {max_tokens}"
            )
    return chunks


def chunk_folder(
    folder: Path,
    count_tokens: Callable[[str], int],
    max_tokens: int,
    filenames: tuple[str, ...] | None = None,
) -> list[Chunk]:
    """Chunk documents in a folder, in filename order.

    Without filenames, every .md file is read. With filenames, exactly those
    files are read: a listed file that is missing raises an error, and any
    other file in the folder is skipped (and the skip is logged), so a
    document dropped into the folder by mistake never becomes searchable.
    """
    if filenames is None:
        paths = sorted(folder.glob("*.md"))
    else:
        missing = [name for name in filenames if not (folder / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Listed knowledge document(s) not found in {folder}: {', '.join(missing)}")
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.name not in filenames:
                log.warning("Not indexed: %s is not in the active document list", path.name)
        paths = [folder / name for name in sorted(filenames)]
    chunks = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        chunks.extend(chunk_document(path.name, text, count_tokens, max_tokens))
    return chunks
