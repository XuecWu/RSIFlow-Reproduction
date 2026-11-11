import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            rows.append(json.loads(line))
    return rows


def build_status(audit_root):
    audit_root = Path(audit_root)
    operations = read_jsonl(audit_root / "operations.jsonl")
    results = read_jsonl(audit_root / "key_results.jsonl")
    runs = json.loads((audit_root / "runs.json").read_text())["runs"] if (audit_root / "runs.json").is_file() else []
    counts = Counter(row.get("status", "unknown") for row in operations)
    timeline = [
        {
            "sequence": row["sequence"],
            "timestamp": row.get("end_timestamp"),
            "phase": row.get("phase"),
            "action": row.get("action"),
            "tool_kind": row.get("tool_kind"),
            "status": row.get("status"),
            "error_type": row.get("error_type"),
            "evidence_paths": row.get("evidence_paths", []),
        }
        for row in operations[-50:]
    ]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "current_phase": operations[-1].get("phase") if operations else None,
        "operation_counts": dict(counts),
        "operations_total": len(operations),
        "results_total": len(results),
        "timeline": timeline,
        "key_results": results,
        "runs": runs,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build_status(args.audit_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "operations": result["operations_total"], "results": result["results_total"]}))


if __name__ == "__main__":
    main()
