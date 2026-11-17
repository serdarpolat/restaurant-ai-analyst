"""Command line: build the demo database and run guarded SQL by hand."""

from __future__ import annotations

import argparse
import json
import sys

from analyst.builddb import build_database, ensure_database, read_meta
from analyst.config import DB_PATH
from analyst.errors import QueryRejected
from analyst.runner import SafeSQL


def _print_table(columns: list[str], rows: list[tuple], truncated: bool) -> None:
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(columns)]
    print("  ".join(c.ljust(w) for c, w in zip(columns, widths, strict=True)))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True)))
    print(f"({len(rows)} rows{', truncated' if truncated else ''})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="restaurant-analyst")
    parser.add_argument("--db", default=str(DB_PATH), help=f"database file (default {DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build-db", help="(re)build the demo database for today")
    sub.add_parser("info", help="show what the demo database contains")
    sql = sub.add_parser("sql", help="run one SELECT through the guard")
    sql.add_argument("query")
    args = parser.parse_args(argv)

    if args.command == "build-db":
        path = build_database(args.db)
        print(f"Built {path}")
        print(read_meta(path).get("scenario", ""))
        return 0

    path, state = ensure_database(args.db)
    if args.command == "info":
        print(f"{path} ({state})")
        print(json.dumps(read_meta(path), ensure_ascii=False, indent=2))
        return 0

    with SafeSQL(path) as runner:
        try:
            result = runner.run(args.query)
        except QueryRejected as exc:
            print(json.dumps(exc.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
            return 2
    _print_table(result.columns, result.rows, result.truncated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
