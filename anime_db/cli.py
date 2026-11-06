"""Run bounded API tests and publish complete read-only SQLite artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anime_db.builder import build_from_state
from anime_db.crawler import crawl
from anime_db.identity import IdentityRegistry
from anime_db.mapping import read_candidates
from anime_db.search import search


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="anime-db")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Bounded API ingest plus real-data test build")
    run.add_argument("--mapping-db", type=Path, required=True)
    run.add_argument("--state-dir", type=Path, default=Path(".state"))
    run.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    run.add_argument("--max-requests", type=int, default=10)
    run.add_argument("--candidates", type=int, default=80)
    build = sub.add_parser("build", help="Build databases from saved snapshots offline")
    build.add_argument("--state-dir", type=Path, default=Path(".state"))
    build.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    lookup = sub.add_parser("search", help="Search a released database offline")
    lookup.add_argument("query", help="ADB/AMDB/provider ID or anime title")
    lookup.add_argument("--db", type=Path, default=Path("artifacts/anime.db"))
    lookup.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    if args.command == "search":
        print(json.dumps(search(args.db, args.query, limit=args.limit), ensure_ascii=False, indent=2))
        return 0
    if args.command == "run":
        if not 1 <= args.max_requests <= 10:
            parser.error("Test phase permits only 1–10 requests per source")
        if not 1 <= args.candidates <= 200:
            parser.error("--candidates must be between 1 and 200")
        registry = IdentityRegistry.load(args.state_dir / "registry.json")
        candidates = read_candidates(args.mapping_db, args.candidates)
        report = crawl(
            candidates, args.state_dir, registry=registry,
            max_requests=args.max_requests,
        )
        print(json.dumps({"crawl": report["sources"]}, ensure_ascii=False))
    manifest = build_from_state(args.state_dir, args.output_dir)
    print(json.dumps({"build": manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
