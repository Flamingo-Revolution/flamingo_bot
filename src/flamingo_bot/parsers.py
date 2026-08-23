"""Deterministic parsers for the approved local knowledge sources."""

from __future__ import annotations

import csv
import hashlib
import html
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from flamingo_bot.models import IngestionIssue, SourceDocument
from flamingo_bot.sources import ResolvedSource, SourceRule, discover_rule_files

PARSER_VERSION = "1.1.0"
NORMALIZATION_VERSION = "1.0.0"

_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.I | re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_STRING_LITERAL_RE = re.compile(r"(?P<quote>['\"`])(?P<body>(?:\\.|(?!\1).)*)(?P=quote)", re.S)


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
    value = "\n".join(_WHITESPACE_RE.sub(" ", line).rstrip() for line in value.splitlines())
    return _BLANK_LINES_RE.sub("\n\n", value).strip()


def stable_id(*parts: str) -> str:
    digest = hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()
    return digest[:32]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _split_source_urls(value: str) -> list[str]:
    urls = []
    for candidate in re.split(r"[\n,;]+", value or ""):
        candidate = candidate.strip()
        if _valid_url(candidate) and candidate not in urls:
            urls.append(candidate)
    return urls


def _canonical_url(source: ResolvedSource, relative_path: str) -> str:
    base = source.definition.base_url.rstrip("/") + "/"
    if source.definition.id == "flamingo-revolution":
        if relative_path == "public/llms.txt":
            return base
        blog = re.match(r"src/content/blog/([^/]+)/", relative_path)
        if blog:
            return f"{base}blog/{blog.group(1)}/"
        if relative_path.startswith("src/content/referendum/") or "referendum" in relative_path:
            return f"{base}referendum/"
        route_map = {
            "about": "rreth-nesh/",
            "demands": "kerkesat/",
            "protests": "protestat/",
            "documents": "projektligje/",
            "flamingotimes": "flamingo-times/",
        }
        lower = relative_path.lower()
        for marker, route in route_map.items():
            if marker in lower:
                return f"{base}{route}"
    if source.definition.id == "diaspora-zbarkon":
        if "participation" in relative_path or "live-tracker" in relative_path:
            return f"{base}pulsi/"
    return base


def _base_document(
    source: ResolvedSource,
    relative_path: str,
    title: str,
    text: str,
    content_type: str,
    *,
    canonical_url: str | None = None,
    sticky_context: str = "",
    legal_status: str | None = None,
    metadata: dict[str, Any] | None = None,
    identity_suffix: str = "",
) -> SourceDocument:
    document_id = stable_id(source.definition.id, relative_path, identity_suffix)
    return SourceDocument(
        document_id=document_id,
        source_id=source.definition.id,
        source_label=source.definition.label,
        repository_path=str(source.repository_path),
        relative_path=relative_path,
        revision=source.revision,
        canonical_url=canonical_url or _canonical_url(source, relative_path),
        language=source.definition.language,
        content_type=content_type,
        title=title.strip() or Path(relative_path).stem,
        text=normalize_text(text),
        sticky_context=normalize_text(sticky_context),
        legal_status=legal_status.strip() if legal_status else None,
        metadata=metadata or {},
    )


def _parse_dossier(source: ResolvedSource, path: Path, rule: SourceRule) -> list[SourceDocument]:
    relations_option = rule.options.get("relations_path")
    if not isinstance(relations_option, str) or not relations_option:
        raise ValueError("dossier_csv requires a relations_path option")
    relations_path = source.repository_path / relations_option
    relations: dict[str, list[dict[str, str]]] = defaultdict(list)
    with relations_path.open("r", encoding="utf-8-sig", newline="") as relation_file:
        for relation in csv.DictReader(relation_file):
            relation_id = (relation.get("dosje_id") or "").strip()
            if relation_id:
                relations[relation_id].append(
                    {key: (value or "").strip() for key, value in relation.items()}
                )

    documents: list[SourceDocument] = []
    relative_path = path.relative_to(source.repository_path).as_posix()
    with path.open("r", encoding="utf-8-sig", newline="") as dossier_file:
        for row in csv.DictReader(dossier_file):
            cleaned = {key: (value or "").strip() for key, value in row.items()}
            dossier_id = cleaned.get("id", "")
            title = cleaned.get("titull", dossier_id)
            if not dossier_id or not title:
                continue
            source_urls = _split_source_urls(cleaned.get("burimet", ""))
            legal_status = cleaned.get("statusi") or "Status not specified"
            sticky = "\n".join(
                part
                for part in (
                    f"Dossier: {title}",
                    f"Category: {cleaned.get('kategori', 'Not specified')}",
                    f"Legal or institutional status: {legal_status}",
                    (
                        "This record contains sourced public-interest claims; "
                        "allegations are not final judgments."
                    ),
                )
                if part
            )
            sections = [
                sticky,
                (
                    "Period: "
                    f"{cleaned.get('periudha') or cleaned.get('viti_fillimit') or 'Not specified'}"
                ),
                f"Summary: {cleaned.get('permbledhje', '')}",
                f"Highlighted statement: {cleaned.get('fraza_theksuar', '')}",
                f"Description:\n{cleaned.get('pershkrim', '')}",
                (
                    "Reported cost: "
                    f"{cleaned.get('kosto_tekst') or cleaned.get('kosto_eur') or 'Not specified'}"
                ),
                f"Cost confidence: {cleaned.get('kosto_besueshmeria') or 'Not specified'}",
                f"Editorial and current-status notes:\n{cleaned.get('shenime_editoriale', '')}",
            ]
            linked_entities = []
            for relation in relations.get(dossier_id, []):
                linked_entities.append(
                    " - ".join(
                        value
                        for value in (
                            relation.get("lloji", ""),
                            relation.get("emri", ""),
                            relation.get("roli", ""),
                            relation.get("statusi", ""),
                            relation.get("shenim", ""),
                        )
                        if value
                    )
                )
            if linked_entities:
                sections.append(
                    "Related people, institutions, places, and claims:\n"
                    + "\n".join(linked_entities)
                )
            if source_urls:
                sections.append("Sources:\n" + "\n".join(source_urls))

            documents.append(
                _base_document(
                    source,
                    relative_path,
                    title,
                    "\n\n".join(part for part in sections if part and not part.endswith(": ")),
                    "dossier",
                    sticky_context=sticky,
                    legal_status=legal_status,
                    metadata={
                        "record_id": dossier_id,
                        "category": cleaned.get("kategori"),
                        "source_urls": source_urls,
                        "extracted_claim": cleaned.get("eshte_pretendim_i_nxjerre", "").lower()
                        in {"true", "1", "yes"},
                        "relation_count": len(relations.get(dossier_id, [])),
                        "license": "CC BY 4.0",
                    },
                    identity_suffix=dossier_id,
                )
            )
    return documents


#: Pinned to every participation chunk. These are normalized model estimates,
#: and a visitor must never be able to read one as a count of people.
_PARTICIPATION_STATUS = (
    "Estimated participation index from a crowd-counting model, not an official count"
)


def _js_sub_object(entry: str, key: str) -> str:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*\{{", entry)
    if match is None:
        return ""
    start = match.end() - 1
    return entry[start : _js_balanced_end(entry, start)]


def _participation_sticky(source: ResolvedSource, title: str, anchor: str) -> str:
    return "\n".join(
        (
            f"Source: {source.definition.label}",
            f"Document: {title}",
            f"Measure: an estimated participation index, not a number of people. 100 = {anchor}.",
            "The index comes from a crowd-counting model applied to News24 livestream frames, "
            "with the largest gatherings anchored to on-the-ground geometry estimates. Camera "
            "coverage during the broadcast and model accuracy mean the figures cannot be exact.",
        )
    )


def _parse_participation(
    source: ResolvedSource, path: Path, rule: SourceRule
) -> list[SourceDocument]:
    """Render the protest participation series as readable, estimate-labelled text.

    The generic TypeScript parser keeps only long string literals, which drops
    every figure and date in this file and leaves the day notes with nothing to
    attach them to. Retrieval needs the numbers, so this parser reads the
    structure instead.
    """
    raw = _read_text(path)
    relative_path = path.relative_to(source.repository_path).as_posix()
    methodology = _leading_line_comments(raw)
    code = _remove_js_comments(raw)

    template_match = _YT_HELPER_RE.search(code)
    if template_match is None:
        raise ValueError("Expected a `yt` livestream URL helper")
    livestream_template = template_match.group(1)

    anchor_day = _js_number_field(code, "index100Day")
    anchor_date = _js_string_field(code, "index100Date")
    if anchor_day is None or not anchor_date:
        raise ValueError("Expected a NORMALIZATION anchor with index100Day and index100Date")
    anchor = f"day {int(anchor_day)}, {anchor_date}"

    index_lines: list[str] = []
    day_paragraphs: list[str] = []
    measured: list[tuple[int, str, float]] = []
    for entry in _js_object_literals(_js_array_body(code, "participation")):
        number = _js_number_field(entry, "day")
        iso_date = _js_string_field(entry, "date")
        if number is None or not iso_date:
            raise ValueError("A participation day is missing its day number or date")
        day = int(number)
        peak = _js_number_field(entry, "peak")
        figures = (
            f"peak {_format_index(peak)}, "
            f"mean {_format_index(_js_number_field(entry, 'mean'))}, "
            f"median {_format_index(_js_number_field(entry, 'median'))}"
        )
        weekday = ", e shtunë / Saturday" if _js_flag_field(entry, "saturday") else ""
        heading = f"Day {day}, {_spell_date(iso_date)}{weekday}"
        if peak is not None:
            measured.append((day, iso_date, peak))
        index_lines.append(f"{heading}: {figures}.")

        detail = [f"{heading}.", f"Estimated participation index: {figures}."]
        note = _js_sub_object(entry, "note")
        note_sq = _js_string_field(note, "sq")
        note_en = _js_string_field(note, "en")
        if note_sq:
            detail.append(f"Shënim: {note_sq}")
        if note_en:
            detail.append(f"Note: {note_en}")
        related = _js_string_field(_js_sub_object(entry, "noteLink"), "href")
        if related and _valid_url(related):
            detail.append(f"Related link: {related}")
        stream = _YT_CALL_RE.search(entry)
        if stream:
            detail.append(
                "Livestream: " + _TEMPLATE_SLOT_RE.sub(stream.group(1), livestream_template)
            )
        day_paragraphs.append("\n".join(detail))

    if not index_lines:
        raise ValueError("The participation series is empty")

    event_paragraphs: list[str] = []
    for entry in _js_object_literals(_js_array_body(code, "participationEvents")):
        number = _js_number_field(entry, "day")
        if number is None:
            continue
        label = _js_sub_object(entry, "label")
        sub = _js_sub_object(entry, "sub")
        parts = [f"Day {int(number)} ({_js_string_field(entry, 'tier') or 'event'})"]
        for scope in (label, sub):
            albanian = _js_string_field(scope, "sq")
            english = _js_string_field(scope, "en")
            paired = " / ".join(part for part in (albanian, english) if part)
            if paired:
                parts.append(paired)
        event_paragraphs.append(". ".join(parts) + ".")

    peak_day, peak_date, peak_value = max(measured, key=lambda item: item[2])
    low_day, low_date, low_value = min(measured, key=lambda item: item[2])
    summary = (
        f"{len(index_lines)} protest days recorded, from {measured[0][1]} to {measured[-1][1]}, "
        f"with {len(measured)} of them carrying a published figure. The index is highest on "
        f"day {peak_day} ({peak_date}) at {_format_index(peak_value)} and lowest on day "
        f"{low_day} ({low_date}) at {_format_index(low_value)}."
    )
    shared_metadata: dict[str, Any] = {
        "day_count": len(index_lines),
        "measured_day_count": len(measured),
        "index_100_day": int(anchor_day),
        "index_100_date": anchor_date,
        "first_date": measured[0][1],
        "last_date": measured[-1][1],
    }

    sections = (
        (
            "methodology",
            "Pulsi: metodologjia e indeksit të pjesëmarrjes",
            "\n\n".join(
                part
                for part in (
                    methodology,
                    f"Normalization reference: index 100 = {anchor}.",
                )
                if part
            ),
        ),
        (
            "index",
            "Pulsi: indeksi i pjesëmarrjes ditë pas dite",
            "\n\n".join([summary, *index_lines]),
        ),
        (
            "days",
            "Pulsi: shënimet ditë pas dite",
            "\n\n".join(day_paragraphs),
        ),
        (
            "events",
            "Pulsi: momentet kryesore të protestës",
            "\n\n".join(event_paragraphs),
        ),
    )
    return [
        _base_document(
            source,
            relative_path,
            title,
            text,
            "participation-index",
            sticky_context=_participation_sticky(source, title, anchor),
            legal_status=_PARTICIPATION_STATUS,
            metadata={**shared_metadata, "section": suffix},
            identity_suffix=suffix,
        )
        for suffix, title, text in sections
        if text.strip()
    ]


def _strip_markdown(raw: str) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {}
    body = raw
    if raw.startswith("---\n"):
        _, frontmatter, body = raw.split("---", 2)
        parsed = yaml.safe_load(frontmatter) or {}
        if isinstance(parsed, dict):
            metadata = parsed
    body = _MARKDOWN_IMAGE_RE.sub("", body)
    body = _MARKDOWN_LINK_RE.sub(lambda match: f"{match.group(1)} ({match.group(2)})", body)
    body = re.sub(r"^\s{0,3}#{1,6}\s*", "", body, flags=re.M)
    body = re.sub(r"[*_~]{1,3}", "", body)
    body = re.sub(r"^\s*>\s?", "", body, flags=re.M)
    body = re.sub(r"```.*?```", "", body, flags=re.S)
    body = _SCRIPT_STYLE_RE.sub("", body)
    body = _HTML_COMMENT_RE.sub("", body)
    body = _HTML_TAG_RE.sub(" ", body)
    return normalize_text(html.unescape(body)), metadata


def _decode_js_string(value: str) -> str:
    replacements = {
        r"\n": "\n",
        r"\r": "\n",
        r"\t": " ",
        r"\'": "'",
        r"\"": '"',
        r"\`": "`",
        r"\\": "\\",
    }
    for encoded, decoded in replacements.items():
        value = value.replace(encoded, decoded)
    value = re.sub(r"\$\{[^}]+\}", " ", value)
    return normalize_text(value)


def _remove_js_comments(raw: str) -> str:
    output: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(raw):
        character = raw[index]
        following = raw[index + 1] if index + 1 < len(raw) else ""
        if quote is not None:
            output.append(character)
            if character == "\\" and following:
                output.append(following)
                index += 2
                continue
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"', "`"}:
            quote = character
            output.append(character)
            index += 1
            continue
        if character == "/" and following == "/":
            index += 2
            while index < len(raw) and raw[index] != "\n":
                index += 1
            output.append("\n")
            index += 1
            continue
        if character == "/" and following == "*":
            index += 2
            while index + 1 < len(raw) and not (raw[index] == "*" and raw[index + 1] == "/"):
                if raw[index] == "\n":
                    output.append("\n")
                index += 1
            index = min(index + 2, len(raw))
            continue
        output.append(character)
        index += 1
    return "".join(output)


def _typescript_text(raw: str) -> str:
    strings: list[str] = []
    seen: set[str] = set()
    for match in _STRING_LITERAL_RE.finditer(_remove_js_comments(raw)):
        value = _decode_js_string(match.group("body"))
        lower = value.lower()
        if len(value) < 18 or value.count(" ") < 2:
            continue
        if value.startswith(("@/", "./", "../", "/images/", "/documents/")):
            continue
        if "<svg" in lower or "</svg>" in lower:
            continue
        if lower.endswith((".png", ".jpg", ".jpeg", ".svg", ".webp", ".pdf")):
            continue
        if value not in seen:
            strings.append(value)
            seen.add(value)
    return "\n\n".join(strings)


#: Albanian month names, so a visitor asking about "20 qershor" retrieves the
#: same day as one asking about "2026-06-20".
_ALBANIAN_MONTHS = (
    "janar",
    "shkurt",
    "mars",
    "prill",
    "maj",
    "qershor",
    "korrik",
    "gusht",
    "shtator",
    "tetor",
    "nëntor",
    "dhjetor",
)
_ENGLISH_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_YT_HELPER_RE = re.compile(r"const\s+yt\s*=\s*\([^)]*\)\s*=>\s*`([^`]+)`")
_YT_CALL_RE = re.compile(r"source\s*:\s*yt\(\s*['\"]([^'\"]+)['\"]\s*\)")
_TEMPLATE_SLOT_RE = re.compile(r"\$\{[^}]+\}")


def _js_balanced_end(raw: str, start: int) -> int:
    """Index just past the bracket closing the one at `start`, ignoring strings."""
    opener = raw[start]
    closer = {"[": "]", "{": "}"}[opener]
    depth = 0
    index = start
    quote = ""
    while index < len(raw):
        character = raw[index]
        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = ""
        elif character in "\"'`":
            quote = character
        elif character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ValueError("Unbalanced JavaScript literal")


def _js_array_body(raw: str, name: str) -> str:
    match = re.search(rf"export const {re.escape(name)}\b[^=]*=\s*\[", raw)
    if match is None:
        raise ValueError(f"Expected a `{name}` array export")
    start = match.end() - 1
    return raw[start + 1 : _js_balanced_end(raw, start) - 1]


def _js_object_literals(body: str) -> list[str]:
    literals: list[str] = []
    index = 0
    while index < len(body):
        if body[index] == "{":
            end = _js_balanced_end(body, index)
            literals.append(body[index:end])
            index = end
            continue
        index += 1
    return literals


def _js_string_field(entry: str, key: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(key)}\s*:\s*(?P<quote>['\"`])(?P<body>(?:\\.|(?!(?P=quote)).)*)(?P=quote)",
        entry,
        re.S,
    )
    return _decode_js_string(match.group("body")) if match else None


def _js_number_field(entry: str, key: str) -> float | None:
    match = re.search(rf"\b{re.escape(key)}\s*:\s*(null|-?\d+(?:\.\d+)?)", entry)
    if match is None or match.group(1) == "null":
        return None
    return float(match.group(1))


def _js_flag_field(entry: str, key: str) -> bool:
    return bool(re.search(rf"\b{re.escape(key)}\s*:\s*true\b", entry))


def _leading_line_comments(raw: str) -> str:
    """The file header, unwrapped so hard-wrapped prose reads as sentences."""
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("//"):
            body = stripped[2:].strip()
            if body:
                current.append(body)
                continue
        elif stripped:
            break
        # A bare "//" separator or a blank line closes the paragraph.
        if current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return "\n\n".join(" ".join(parts) for parts in paragraphs)


def _format_index(value: float | None) -> str:
    if value is None:
        return "not published"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _spell_date(iso_date: str) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso_date)
    if match is None:
        return iso_date
    year, month, day = (int(part) for part in match.groups())
    if not 1 <= month <= 12:
        return iso_date
    albanian = f"{day} {_ALBANIAN_MONTHS[month - 1]} {year}"
    english = f"{day} {_ENGLISH_MONTHS[month - 1]} {year}"
    return f"{iso_date} ({albanian} / {english})"


def _title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        candidate = line.strip().lstrip("#").strip()
        if 3 <= len(candidate) <= 140:
            return candidate
    return fallback.replace("-", " ").replace("_", " ").title()


def _parse_generic(source: ResolvedSource, path: Path, parser: str) -> SourceDocument | None:
    relative_path = path.relative_to(source.repository_path).as_posix()
    if parser == "text":
        text = normalize_text(_read_text(path))
        metadata: dict[str, Any] = {}
        content_type = "text"
    elif parser == "markdown":
        text, metadata = _strip_markdown(_read_text(path))
        if metadata.get("draft") is True:
            return None
        content_type = "markdown"
    elif parser == "typescript_strings":
        text = _typescript_text(_read_text(path))
        metadata = {}
        content_type = "structured-content"
    elif parser == "pdf":
        reader = PdfReader(path)
        text = normalize_text("\n\n".join(page.extract_text() or "" for page in reader.pages))
        metadata = {"page_count": len(reader.pages)}
        content_type = "pdf"
    else:
        raise ValueError(f"Unsupported parser: {parser}")

    if len(text) < 40:
        return None
    title = str(metadata.get("title") or _title_from_text(text, path.stem))
    sticky = f"Source: {source.definition.label}\nDocument: {title}"
    return _base_document(
        source,
        relative_path,
        title,
        text,
        content_type,
        sticky_context=sticky,
        metadata=metadata,
    )


def parse_sources(
    sources: Iterable[ResolvedSource],
) -> tuple[list[SourceDocument], list[IngestionIssue], int]:
    documents: list[SourceDocument] = []
    issues: list[IngestionIssue] = []
    discovered_files = 0
    for source in sources:
        for rule in source.definition.rules:
            files = discover_rule_files(source.repository_path, rule)
            discovered_files += len(files)
            for path in files:
                relative_path = path.relative_to(source.repository_path).as_posix()
                try:
                    if rule.parser == "dossier_csv":
                        documents.extend(_parse_dossier(source, path, rule))
                    elif rule.parser == "participation_ts":
                        documents.extend(_parse_participation(source, path, rule))
                    else:
                        document = _parse_generic(source, path, rule.parser)
                        if document is None:
                            issues.append(
                                IngestionIssue(
                                    source_id=source.definition.id,
                                    relative_path=relative_path,
                                    reason="No meaningful public text was extracted",
                                )
                            )
                        else:
                            documents.append(document)
                except (OSError, csv.Error, ValueError, yaml.YAMLError, PdfReadError) as exc:
                    issues.append(
                        IngestionIssue(
                            source_id=source.definition.id,
                            relative_path=relative_path,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
    documents.sort(key=lambda item: (item.source_id, item.relative_path, item.document_id))
    return documents, issues, discovered_files
