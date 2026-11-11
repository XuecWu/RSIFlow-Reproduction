import fcntl
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?key[_-]?secret|authtoken|authorization|password)\s*[=:]\s*(?:'[^']*'|\"[^\"]*\"|\S+)"),
    re.compile(r"(?i)(cpolar\s+authtoken\s+)\S+"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~-]+"),
]


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize(value):
    text = str(value)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: (match.group(1) if match.lastindex else "") + "REDACTED", text)
    return text


def allocate_sequence(audit_root):
    root = Path(audit_root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".sequence.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = root / "sequence.state"
        current = int(state.read_text()) if state.exists() else 0
        operations = root / "operations.jsonl"
        if operations.exists():
            for line in operations.read_text().splitlines():
                if line.strip():
                    current = max(current, int(json.loads(line)["sequence"]))
        sequence = current + 1
        state.write_text(str(sequence))
        return sequence


def append_jsonl(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def current_git_commit(cwd=None):
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd or Path.cwd(),
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def operation_record(audit_root, **values):
    sequence = values.pop("sequence", None) or allocate_sequence(audit_root)
    now = utc_now()
    cwd = values.pop("cwd", None) or str(Path.cwd())
    record = {
        "sequence": sequence,
        "start_timestamp": values.pop("start_timestamp", now),
        "end_timestamp": values.pop("end_timestamp", now),
        "actor": values.pop("actor", "main-kimi"),
        "tool_kind": values.pop("tool_kind"),
        "phase": values.pop("phase"),
        "action": values.pop("action"),
        "sanitized_input": sanitize(values.pop("sanitized_input", "")),
        "cwd": cwd,
        "status": values.pop("status", "success"),
        "exit_code": values.pop("exit_code", 0),
        "duration_seconds": values.pop("duration_seconds", None),
        "error_type": values.pop("error_type", None),
        "evidence_paths": values.pop("evidence_paths", []),
        "git_commit": values.pop("git_commit", None) or current_git_commit(cwd),
        "sco_job_id": values.pop("sco_job_id", None),
        **values,
    }
    append_jsonl(Path(audit_root) / "operations.jsonl", record)
    return record


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
