"""Capture the public informational pages of referendum21.org once.

The resulting Markdown is reviewed and ingested as a pinned source snapshot;
routine ingestion does not revisit this site or its live signature counters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from flamingo_bot.config import PROJECT_ROOT
from flamingo_bot.web_content import extract_main_text, fetch_public_html

BASE = "https://referendum21.org"
TRANSIENT_LINES = {
    "Duke u sinkronizuar...",
    "Po ngarkohen turnet e nënshkrimit…",
    "--",
    "-- nënshkrime deri te pragu kushtetues prej 50.000 qytetarësh.",
}
PATHS = (
    "/",
    "/dosja",
    "/pyetje-te-shpeshta",
    "/rreth-nesh",
    "/vullnetare",
    "/skeptik",
    "/dosja/referendumi/deklarata",
    "/dosja/ligji/permbledhje",
    "/dosja/referendumi/te-dhenat",
    "/dosja/referendumi/pyetje",
    "/dosja/ligji/arsyeja-1",
    "/dosja/ligji/arsyeja-2",
    "/dosja/ligji/arsyeja-3",
    "/dosja/ligji/arsyeja-4",
    "/dosja/nisma/si-nisi",
    "/dosja/nisma/12-cilesite",
    "/dosja/nisma/deklaratat",
)


def main() -> None:
    captured_at = datetime.now(UTC).date().isoformat()
    snapshots: list[tuple[Path, str]] = []
    for path in PATHS:
        url = BASE + path
        try:
            title, text = extract_main_text(fetch_public_html(url))
        except ValueError as exc:
            raise ValueError(f"Could not capture {url}: {exc}") from exc
        text = "\n".join(line for line in text.splitlines() if line not in TRANSIENT_LINES)
        if path == "/dosja/referendumi/deklarata":
            text = (
                "Shënim historik: kjo deklaratë përmban përshkrimin e fazës së "
                "hershme të nismës. Për fazën aktuale të firmosjes shihni "
                "faqen kryesore të referendum21.org.\n\n" + text
            )
        filename = "home.md" if path == "/" else path.strip("/").replace("/", "__") + ".md"
        frontmatter = yaml.safe_dump(
            {
                "title": title,
                "canonical_url": url,
                "source_label": "Referendum 21/2024",
                "snapshot_group": "referendum21",
                "snapshot_date": captured_at,
            },
            allow_unicode=True,
            sort_keys=False,
        )
        snapshots.append((Path(filename), f"---\n{frontmatter}---\n\n{text}\n"))
    target = PROJECT_ROOT / "content" / "snapshots" / "referendum21"
    target.mkdir(parents=True, exist_ok=True)
    for snapshot_path, content in snapshots:
        (target / snapshot_path).write_text(content, encoding="utf-8")
    print(f"Captured {len(snapshots)} public pages from {BASE} into {target}")


if __name__ == "__main__":
    main()
