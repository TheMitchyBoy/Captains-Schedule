#!/usr/bin/env python3
"""
CLI tool to clean and repair raw dispatch XML.

Usage:
  python repair_xml.py input.xml                    # print cleaned XML to stdout
  python repair_xml.py input.xml -o cleaned.xml     # write cleaned XML to file
  python repair_xml.py input.xml --report           # print repair report as JSON
  cat raw.xml | python repair_xml.py -              # read from stdin

Repairs:
  - Re-parses XML (with regex/AI fallback for malformed files)
  - Converts checkin_time and return_time to 24-hour HH:MM
  - Moves leaked 15am:/30am:/15pm:/30pm: prefixes from boat_codes into checkin_time
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.xml_cleaner import clean_xml_content


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean and repair dispatch schedule XML")
    parser.add_argument("input", help="Input XML file path, or '-' for stdin")
    parser.add_argument("-o", "--output", help="Write cleaned XML to this file")
    parser.add_argument("--report", action="store_true", help="Print JSON repair report to stderr")
    args = parser.parse_args()

    if args.input == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(args.input).read_bytes()

    result = clean_xml_content(raw)

    if args.report:
        report = {
            "entries_processed": result.analysis.entries_found,
            "times_normalized": result.analysis.times_normalized,
            "boat_fields_repaired": result.analysis.boat_fields_repaired,
            "parse_method": result.analysis.parse_method,
            "ai_assisted": result.analysis.ai_assisted,
            "hour_distribution": result.analysis.hour_distribution,
            "repairs": [
                {
                    "entry_index": r.entry_index,
                    "field": r.field,
                    "issue": r.issue,
                    "before": r.before,
                    "after": r.after,
                    "confidence": r.confidence,
                }
                for r in result.repairs
            ],
            "warnings": result.analysis.warnings,
            "errors": result.errors,
        }
        print(json.dumps(report, indent=2), file=sys.stderr)

    if not result.cleaned_xml:
        print("Error: no cleaned XML produced", file=sys.stderr)
        for err in result.errors:
            print(err, file=sys.stderr)
        return 1

    if args.output:
        Path(args.output).write_text(result.cleaned_xml, encoding="utf-8")
        print(f"Wrote cleaned XML to {args.output}", file=sys.stderr)
    else:
        print(result.cleaned_xml)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
