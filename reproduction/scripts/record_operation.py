import argparse
from pathlib import Path

from auditlib import operation_record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--actor", default="main-kimi")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--tool-kind", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--input", dest="sanitized_input", default="")
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--status", default="success")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--error-type")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--git-commit")
    parser.add_argument("--sco-job-id")
    parser.add_argument("--note")
    args = parser.parse_args()
    values = vars(args).copy()
    audit_root = values.pop("audit_root")
    values["evidence_paths"] = values.pop("evidence")
    if values.get("note") is None:
        values.pop("note")
    record = operation_record(audit_root, **values)
    print(record["sequence"])


if __name__ == "__main__":
    raise SystemExit(main())
