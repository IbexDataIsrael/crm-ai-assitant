"""Split markdown documents into small chunks for retrieval.

Each chunk keeps the filename and the heading it sits under. Chunk size is
measured in the embedding model's own tokens (including special tokens), so
a chunk is never longer than the model can read. If something cannot be made
to fit, we raise an error instead of letting the model silently cut it off.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger("uvicorn.error")

# How many lines a new chunk repeats from the end of the previous chunk in the
# same section, and how many words when one long line has to be split.
OVERLAP_LINES = 1
OVERLAP_WORDS = 8


@dataclass(frozen=True)
class Chunk:
    filename: str
    heading: str
    text: str

    @property
    def embed_text(self) -> str:
        """The text that is actually turned into a vector (heading gives context)."""
        return f"{self.heading}\n{self.text}"


def split_sections(markdown: str, filename: str) -> list[tuple[str, list[str]]]:
    """Return (heading, lines) pairs. Text before any heading uses the filename."""
    sections = []
    heading = filename
    lines: list[str] = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            if lines:
                sections.append((heading, lines))
            heading = line.lstrip("#").strip()
            lines = []
        elif line:
            lines.append(line)
    if lines:
        sections.append((heading, lines))
    return sections


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
    for heading, lines in split_sections(markdown, filename):

        def fits(text: str) -> bool:
            return count_tokens(Chunk(filename, heading, text).embed_text) <= max_tokens

        units = []
        for line in lines:
            units.extend([line] if fits(line) else _split_long_line(line, fits))

        current: list[str] = []
        for unit in units:
            if not current or fits("\n".join(current + [unit])):
                current.append(unit)
                continue
            chunks.append(Chunk(filename, heading, "\n".join(current)))
            overlap = current[-OVERLAP_LINES:]
            current = overlap + [unit] if fits("\n".join(overlap + [unit])) else [unit]
        if current:
            chunks.append(Chunk(filename, heading, "\n".join(current)))

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
