"""python -m pulse --data data/sample_311.csv.gz --asof 2025-12-31 --out brief.html"""
import argparse
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .engine import brief_evidence, build_brief, load
from .report import render
from .writer import template_brief, write_brief


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pulse", description="Weekly 311 operations brief")
    p.add_argument("--data", default="data/sample_311.csv.gz", help="cleaned 311 CSV (.csv or .csv.gz)")
    p.add_argument("--asof", help="end date of the week to brief, YYYY-MM-DD (default: latest date in the data)")
    p.add_argument("--out", default="brief.html", help="HTML file to write")
    p.add_argument("--period", choices=("daily", "weekly"), default="weekly")
    p.add_argument("--published", action="store_true", help="include navigation and an evidence archive")
    p.add_argument("--ai", action="store_true", help="use the optional AI wording for a local brief")
    args = p.parse_args(argv)

    df = load(args.data)
    b = build_brief(df, args.asof, days=1 if args.period == "daily" else 7)
    text, source = write_brief(b) if args.ai else (template_brief(b), "Computed summary")
    if args.published:
        path = Path(args.out).with_name(f"{args.period}-evidence.zip")
        write_evidence(path, df, b)
    print(text, f"\n\n[{source}]")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(b, text, source, df, published=args.published))
    print(f"Wrote {args.out}", file=sys.stderr)
    return 0


def write_evidence(path, df, brief):
    sections = [
        ("Request volume", [{}]),
        ("Surges", brief.spikes),
        ("Falling behind", [{"label": "All late open cases"}, *brief.overdue]),
        ("Behind expected", brief.behind),
        ("Repeat hotspots", brief.hotspots),
        ("Data-change alerts", brief.data_watch),
    ]
    index = []
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for section, findings in sections:
            for number, finding in enumerate(findings, 1):
                rows, explanation = brief_evidence(df, brief, section, finding)
                filename = f"{section.lower().replace(' ', '-')}-{number}.csv"
                archive.writestr(filename, rows.to_csv(index=False))
                index.extend([filename, str(finding), explanation, ""])
        archive.writestr("README.txt", f"Boston 311 brief ending {brief.asof}\n\n" + "\n".join(index))


if __name__ == "__main__":
    raise SystemExit(main())
