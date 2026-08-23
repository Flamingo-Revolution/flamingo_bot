"""Semantic, token-aware, deterministic chunking."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from flamingo_bot.models import ChunkRecord, SourceDocument
from flamingo_bot.parsers import normalize_text

CHUNKER_VERSION = "1.1.0"
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÇË0-9])")
_TOKEN_PIECE_RE = re.compile(r"\S+\s*")


class LocalTokenEstimator:
    """Deterministic approximation that never downloads tokenizer assets.

    Azure enforces the authoritative model limit. Chunking uses a conservative
    3.4 UTF-8 bytes-per-token estimate, which is intentionally biased toward
    smaller chunks for Albanian text.
    """

    bytes_per_token = 3.4
    version = "utf8-byte-estimator-1.0.0"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text.encode("utf-8")) / self.bytes_per_token))

    def split_to_budget(self, text: str, budget: int) -> list[str]:
        pieces = _TOKEN_PIECE_RE.findall(text)
        segments: list[str] = []
        current = ""
        for piece in pieces:
            if self.count(piece) > budget:
                if current:
                    segments.append(normalize_text(current))
                    current = ""
                hard_segment = ""
                for character in piece:
                    candidate = hard_segment + character
                    if hard_segment and self.count(candidate) > budget:
                        segments.append(normalize_text(hard_segment))
                        hard_segment = character
                    else:
                        hard_segment = candidate
                current = hard_segment
                continue
            candidate = current + piece
            if current and self.count(candidate) > budget:
                segments.append(normalize_text(current))
                current = piece
            else:
                current = candidate
        if current:
            segments.append(normalize_text(current))
        return segments

    def prefix(self, text: str, budget: int) -> str:
        parts = self.split_to_budget(text, budget)
        return parts[0] if parts else ""

    def tail(self, text: str, budget: int) -> str:
        pieces = _TOKEN_PIECE_RE.findall(text)
        selected: list[str] = []
        for piece in reversed(pieces):
            if not selected and self.count(piece) > budget:
                suffix = ""
                for character in reversed(piece):
                    candidate = character + suffix
                    if suffix and self.count(candidate) > budget:
                        break
                    suffix = candidate
                selected.append(suffix)
                break
            candidate = piece + "".join(selected)
            if selected and self.count(candidate) > budget:
                break
            selected.insert(0, piece)
        return normalize_text("".join(selected))


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 600
    max_tokens: int = 700
    overlap_tokens: int = 80

    def __post_init__(self) -> None:
        if not 0 <= self.overlap_tokens < self.target_tokens <= self.max_tokens:
            raise ValueError("chunk token limits must satisfy overlap < target <= max")


class SemanticChunker:
    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()
        self.estimator = LocalTokenEstimator()

    def token_count(self, text: str) -> int:
        return self.estimator.count(text)

    def _split_oversized(self, paragraph: str, budget: int) -> list[str]:
        sentences = [
            item.strip() for item in _SENTENCE_BOUNDARY_RE.split(paragraph) if item.strip()
        ]
        if len(sentences) == 1 and self.token_count(paragraph) > budget:
            return self.estimator.split_to_budget(paragraph, budget)

        bounded_sentences: list[str] = []
        for sentence in sentences:
            if self.token_count(sentence) > budget:
                bounded_sentences.extend(self.estimator.split_to_budget(sentence, budget))
            else:
                bounded_sentences.append(sentence)

        pieces: list[str] = []
        current: list[str] = []
        for sentence in bounded_sentences:
            candidate = " ".join([*current, sentence])
            if current and self.token_count(candidate) > budget:
                pieces.append(" ".join(current))
                current = [sentence]
            else:
                current.append(sentence)
        if current:
            pieces.append(" ".join(current))
        return pieces

    def _body_segments(self, document: SourceDocument, body_budget: int) -> list[str]:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", document.text) if item.strip()]
        expanded: list[str] = []
        for paragraph in paragraphs:
            if self.token_count(paragraph) > body_budget:
                expanded.extend(self._split_oversized(paragraph, body_budget))
            else:
                expanded.append(paragraph)

        segments: list[str] = []
        current: list[str] = []
        for paragraph in expanded:
            candidate = "\n\n".join([*current, paragraph])
            if current and self.token_count(candidate) > body_budget:
                segments.append("\n\n".join(current))
                current = [paragraph]
            else:
                current.append(paragraph)
        if current:
            segments.append("\n\n".join(current))
        return segments

    def chunk_document(self, document: SourceDocument) -> list[ChunkRecord]:
        prefix = normalize_text(document.sticky_context)
        prefix_tokens = self.token_count(prefix) if prefix else 0
        body_budget = min(
            self.config.target_tokens - prefix_tokens - 4,
            self.config.max_tokens - prefix_tokens - self.config.overlap_tokens - 4,
        )
        if body_budget < 100:
            raise ValueError(f"Sticky context is too large for document {document.document_id}")

        raw_segments = self._body_segments(document, body_budget)
        chunks: list[ChunkRecord] = []
        previous_body = ""
        for index, segment in enumerate(raw_segments):
            overlap = ""
            if previous_body and self.config.overlap_tokens:
                overlap = self.estimator.tail(previous_body, self.config.overlap_tokens)
            body = normalize_text("\n\n".join(part for part in (overlap, segment) if part))
            text = normalize_text("\n\n".join(part for part in (prefix, body) if part))
            token_count = self.token_count(text)
            if token_count > self.config.max_tokens:
                raise ValueError(
                    f"Chunk budget invariant failed for document {document.document_id}"
                )

            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            chunk_id = hashlib.sha256(
                f"{document.document_id}\x00{index}\x00{content_hash}".encode()
            ).hexdigest()[:32]
            chunks.append(
                ChunkRecord(
                    chunk_id=chunk_id,
                    document_id=document.document_id,
                    source_id=document.source_id,
                    source_label=document.source_label,
                    repository_path=document.repository_path,
                    relative_path=document.relative_path,
                    revision=document.revision,
                    canonical_url=document.canonical_url,
                    language=document.language,
                    content_type=document.content_type,
                    title=document.title,
                    text=text,
                    legal_status=document.legal_status,
                    metadata=document.metadata,
                    content_hash=content_hash,
                    token_count=token_count,
                    chunk_index=index,
                )
            )
            previous_body = segment
        return chunks

    def chunk_documents(self, documents: list[SourceDocument]) -> list[ChunkRecord]:
        chunks = [chunk for document in documents for chunk in self.chunk_document(document)]
        chunks.sort(key=lambda item: (item.source_id, item.document_id, item.chunk_index))
        return chunks
