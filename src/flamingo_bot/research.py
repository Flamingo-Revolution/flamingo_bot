"""Read attributed reporting summaries from the author's protest research indexes."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class ResearchEvidence:
    title: str
    text: str
    url: str
    revision: str
    metadata: dict[str, Any]


def _text(value: object) -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if isinstance(item, str))
    return ""


def _access_status(record: dict[str, Any]) -> str | None:
    access = " ".join(_text(record.get(key)) for key in ("access", "access_status", "access_note"))
    access = access.lower().replace("_", " ").replace("-", " ")
    # Reading an indexed full body is distinct from seeing only a search snippet.
    if re.search(r"(?:body|text|statement).{0,30}(?:opened|read)", access):
        return "indexed_body_read" if "indexed" in access else "body_read"
    if any(
        word in access for word in ("indexed only", "snippet", "excerpt", "failed", "search result")
    ):
        return None
    return "not_recorded"


def read_research_evidence(path: Path) -> list[ResearchEvidence]:
    """Only explicit public-report fields enter the corpus; operational data stays local."""
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise ValueError("A protest research index exceeds 2 MB")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("A protest research index must be an object")
    day = data.get("day")
    filename = re.fullmatch(r"day_(\d+)_sources\.json", path.name)
    if not isinstance(day, int) or isinstance(day, bool) or day < 1:
        raise ValueError("A protest research index needs a positive day number")
    if filename is None or int(filename.group(1)) != day:
        raise ValueError("Research filename and protest day disagree")
    calendar_date = data.get("calendar_date")
    if not isinstance(calendar_date, str):
        raise ValueError("A protest research index needs a calendar_date")
    date.fromisoformat(calendar_date)
    records = data.get("sources")
    if not isinstance(records, list) or not records:
        raise ValueError("A protest research index needs a nonempty sources list")
    revision = hashlib.sha256(raw).hexdigest()
    evidence = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("A research source must be an object")
        url = _text(record.get("url"))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("A research source needs a public HTTP(S) URL")
        # The tracker already cites the livestream; local video-analysis notes
        # are not independent reporting or measurements of total attendance.
        if parsed.hostname in {"youtube.com", "www.youtube.com", "youtu.be"}:
            continue
        access = _access_status(record)
        if access is None:
            continue
        outlet = _text(record.get("outlet"))
        title = _text(record.get("title") or record.get("title_excerpt")) or outlet
        summary = _text(record.get("summary") or record.get("use"))
        if not outlet or not title or not summary:
            raise ValueError("A research source needs outlet, title, and summary/use fields")
        publication = next(
            (
                _text(record[key])
                for key in (
                    "published_display",
                    "published_or_stream_date",
                    "published_at",
                    "published_date",
                )
                if record.get(key)
            ),
            "Not recorded",
        )
        lineage = _text(record.get("reporting_lineage") or record.get("independence"))
        caveats = _text(record.get("uncertainties") or record.get("source_text_caveat"))
        event_date = _text(
            record.get("event_date")
            or record.get("event_date_referenced")
            or record.get("event_dates")
        )
        parts = [
            "Flamingo Revolution / Revolucioni Flamingo: author's source-linked protest research.",
            f"Publisher: {outlet}. Article: {title}.",
            f"Publication as recorded in research: {publication}.",
            (
                f"Event date(s) explicitly recorded for this source: {event_date}."
                if event_date
                else "Incident date: not specified in this source record. Do not infer it "
                "from the publication date or the research file's filing date."
            ),
            f"Author's summary of the report: {summary}",
        ]
        if lineage:
            parts.append(f"Reporting lineage / independence: {lineage}")
        if caveats:
            parts.append(f"Source limitations: {caveats}")
        parts.append(f"Public source: {url}")
        evidence.append(
            ResearchEvidence(
                title=f"{outlet}: {title}",
                text="\n\n".join(parts),
                url=url,
                revision=revision,
                metadata={
                    "record_id": "research-source:" + hashlib.sha256(url.encode()).hexdigest()[:24],
                    "research_day": day,
                    "research_date": calendar_date,
                    "reported_event_date": event_date,
                    "source_kind": "protest-research",
                    "parser_format": "protest_research/2",
                    "source_urls": [url],
                    "publisher": outlet,
                    "evidence_access": access,
                    "research_cutoff": _text(
                        data.get("research_cutoff_utc") or data.get("research_cutoff")
                    ),
                    "source_revision": revision,
                },
            )
        )
    if not evidence:
        raise ValueError("A research day has no usable public reporting summaries")
    return evidence
