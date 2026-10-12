from __future__ import annotations

from pathlib import Path

from .tree_sitter_parser import CodeEntity

# Fallback chunking for files the AST pass found nothing in: docs, config, and
# code living outside any function/class (a file of bare consts, for instance).
WINDOW_LINES = 60
MIN_TEXT_CHARS = 20
# Every chunk must stay small. A window of 60 *long* lines (a minified file, a
# one-line SVG) otherwise pads the embedding batch to a huge sequence and the
# model tries to allocate an attention matrix proportional to length^2 — a
# multi-GB allocation / hard crash on a real repo.
MAX_CHUNK_CHARS = 2000
# shortcut: a flat size cap keeps a lock file or build artifact from flooding the
# index with hundreds of windows. A genuine doc above this is dropped — raise the
# cap, or switch to a generated-file name list, if that ever bites.
MAX_FALLBACK_BYTES = 64 * 1024


class AstSemanticChunker:
    def chunk_entities(self, entities: list[CodeEntity]) -> list[str]:
        chunks: list[str] = []
        for e in entities:
            header = self._header(e.file_path, e.type, e.name, e.start_line, e.end_line)
            chunks.extend(self._body(header, e.code_snippet))
        return chunks

    def chunk_file(self, path: Path, entities: list[CodeEntity]) -> list[str]:
        """Chunks for one file: AST entities when there are any, else text windows."""
        if entities:
            return self.chunk_entities(entities)
        return self._fallback(path)

    @staticmethod
    def _body(header: str, text: str) -> list[str]:
        """Header + body, split so no chunk exceeds MAX_CHUNK_CHARS."""
        if len(text) <= MAX_CHUNK_CHARS:
            return [header + text]
        return [header + piece for piece in AstSemanticChunker._split(text)]

    @staticmethod
    def _header(file_path: str, e_type: str, name: str, start: int, end: int) -> str:
        # HEADER_RE splits the header on '|', so a field containing one would fail to
        # parse and silently drop that chunk's metadata.
        def clean(value: str) -> str:
            return str(value).replace("|", "/").replace("\n", " ").strip()

        return (
            f"# File: {clean(file_path)} | Type: {clean(e_type)}"
            f" | Name: {clean(name)} | Lines: {start}-{end}\n"
        )

    @classmethod
    def _fallback(cls, path: Path) -> list[str]:
        try:
            if path.stat().st_size > MAX_FALLBACK_BYTES:
                return []
            # Strict decoding is the cheapest reliable binary test, and these files
            # already produced no entities.
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        if len(text.strip()) < MIN_TEXT_CHARS:
            return []

        lines = text.splitlines()
        chunks: list[str] = []
        for start in range(0, len(lines), WINDOW_LINES):
            window = lines[start : start + WINDOW_LINES]
            body = "\n".join(window)
            if not body.strip():
                continue
            end = start + len(window)
            header = cls._header(
                str(path), "text", f"lines {start + 1}-{end}", start + 1, end
            )
            chunks.extend(cls._body(header, body))
        return chunks

    @staticmethod
    def _split(text: str, limit: int = 1500) -> list[str]:
        pieces: list[str] = []
        buf = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:
                pieces.append(buf + line[:limit])
                buf = ""
                line = line[limit:]
            if len(buf) + len(line) > limit and buf:
                pieces.append(buf)
                buf = ""
            buf += line
        if buf:
            pieces.append(buf)
        return pieces or [text]
