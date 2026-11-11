import argparse
import json
from pathlib import Path

from build_cohort import inspect_source, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    report = {"sources": [inspect_source(path) for path in args.paths]}
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
