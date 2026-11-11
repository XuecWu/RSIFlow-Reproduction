import argparse
import re
from pathlib import Path

from auditlib import allocate_sequence, append_jsonl, sanitize, sha256_file, utc_now


REVIEW_STATUSES = {"unreviewed", "agent_reported", "main_verified", "changes_required", "not_verified"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--metric", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--verification-command", required=True)
    parser.add_argument("--evidence", action="append", required=True)
    parser.add_argument("--review-status", default="unreviewed", choices=sorted(REVIEW_STATUSES))
    args = parser.parse_args()
    hashes = []
    evidence_paths = []
    for name in args.evidence:
        path = Path(name).resolve()
        if not path.is_file():
            parser.error(f"evidence file does not exist: {path}")
        evidence_paths.append(str(path))
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
        "evidence_paths": evidence_paths,
        "evidence_sha256": hashes,
        "review_status": args.review_status,
    }
    append_jsonl(args.audit_root / "key_results.jsonl", result)
    print(result["result_id"])


if __name__ == "__main__":
    raise SystemExit(main())
