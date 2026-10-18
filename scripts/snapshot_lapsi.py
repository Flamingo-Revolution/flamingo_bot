"""Collect protest-related Lapsi references locally; never publish to Firestore."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.error import URLError

from flamingo_bot.config import PROJECT_ROOT, Settings
from flamingo_bot.lapsi import run_snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Index protest-related Lapsi references from 31 May 2026 on the first run, "
            "then check new/updated articles since the last successful scan. "
            "Stores publisher metadata and a short excerpt, not full articles or AI summaries."
        )
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview without changing local files")
    mode.add_argument("--write", action="store_true", help="Save local references and checkpoint")
    parser.add_argument("--output", type=Path, help="Override FLAMINGO_LAPSI_DIR / .local/lapsi")
    parser.add_argument(
        "--url", action="append", default=[], help="Check a hand-picked article; repeatable"
    )
    parser.add_argument(
        "--full-rescan", action="store_true", help="Recheck all candidates since 31 May"
    )
    parser.add_argument(
        "--max-articles", type=int, default=2000, help="Maximum article fetches (default 2000)"
    )
    parser.add_argument(
        "--max-sitemaps", type=int, default=60, help="Maximum monthly sitemaps (default 60)"
    )
    parser.add_argument(
        "--overlap-hours", type=int, default=48, help="Recheck recent updates (default 48)"
    )
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between requests, minimum 0.5"
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    settings = Settings()
    output = (
        arguments.output or settings.flamingo_lapsi_dir or PROJECT_ROOT / ".local/lapsi"
    ).resolve()
    try:
        report = run_snapshot(
            output,
            dry_run=arguments.dry_run,
            urls=arguments.url,
            full_rescan=arguments.full_rescan,
            max_articles=arguments.max_articles,
            max_sitemaps=arguments.max_sitemaps,
            overlap_hours=arguments.overlap_hours,
            delay=arguments.delay,
            progress=lambda update: print(
                json.dumps({"progress": update}), file=sys.stderr, flush=True
            ),
        )
    except KeyboardInterrupt:
        print(
            json.dumps({"interrupted": True, "message": "Rerun to resume collected metadata."}),
            file=sys.stderr,
        )
        return 130
    except (OSError, URLError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "checkpoint_advanced": False}), file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2 if arguments.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
