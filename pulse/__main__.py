"""python -m pulse --data data/sample_311.csv.gz --asof 2025-12-31 --out brief.html"""
import argparse
import sys

from .engine import build_brief, load
from .report import render
from .writer import write_brief


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pulse", description="Weekly 311 operations brief")
    p.add_argument("--data", default="data/sample_311.csv.gz", help="cleaned 311 CSV (.csv or .csv.gz)")
    p.add_argument("--asof", help="end date of the week to brief, YYYY-MM-DD (default: latest date in the data)")
    p.add_argument("--out", default="brief.html", help="HTML file to write")
    args = p.parse_args(argv)

    df = load(args.data)
    b = build_brief(df, args.asof)
    text, source = write_brief(b)
    print(text, f"\n\n[{source}]")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(b, text, source, df))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
