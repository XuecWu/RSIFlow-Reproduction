import argparse
import re
from pathlib import Path

from auditlib import allocate_sequence, append_jsonl, sanitize, sha256_file, utc_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--verification-command", required=True)
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--review-status", default="main_verified")
    args = parser.parse_args()
    hashes = []
    for name in args.evidence:
        path = Path(name)
        if not path.is_file():
            parser.error(f"evidence file does not exist: {path}")
        hashes.append(sha256_file(path))
    sequence = allocate_sequence(args.audit_root)
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", args.metric).strip("-")
    result = {
        "result_id": f"{sequence:08d}-{slug}",
        "timestamp": utc_now(),
        "phase": args.phase,
        "metric": args.metric,
        "value": args.value,
        "verification_command": sanitize(args.verification_command),
        "evidence_paths": args.evidence,
        "evidence_sha256": hashes,
        "review_status": args.review_status,
    }
    append_jsonl(args.audit_root / "key_results.jsonl", result)
    print(result["result_id"])


if __name__ == "__main__":
    raise SystemExit(main())
