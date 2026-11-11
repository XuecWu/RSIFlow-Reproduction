import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

from auditlib import allocate_sequence, operation_record, sanitize, utc_now


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--actor", default="main-kimi")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--cwd", default=str(Path.cwd()))
    parser.add_argument("--sco-job-id")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("a command is required after --")
    args.audit_root.mkdir(parents=True, exist_ok=True)
    logs = args.audit_root / "logs"
    logs.mkdir(exist_ok=True)
    sequence = allocate_sequence(args.audit_root)
    started_at = utc_now()
    started = time.monotonic()
    error_type = None
    try:
        result = subprocess.run(command, cwd=args.cwd, capture_output=True, text=True, check=False)
        returncode = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except OSError as exc:
        returncode = 127
        stdout, stderr = "", f"{type(exc).__name__}: {exc}"
        error_type = type(exc).__name__
    stdout_path = logs / f"{sequence:08d}-stdout.log"
    stderr_path = logs / f"{sequence:08d}-stderr.log"
    stdout_path.write_text(sanitize(stdout), encoding="utf-8")
    stderr_path.write_text(sanitize(stderr), encoding="utf-8")
    if stdout:
        print(sanitize(stdout), end="")
    if stderr:
        print(sanitize(stderr), end="", file=sys.stderr)
    status = "success" if returncode == 0 else "failed"
    operation_record(
        args.audit_root,
        sequence=sequence,
        start_timestamp=started_at,
        end_timestamp=utc_now(),
        actor=args.actor,
        tool_kind="bash",
        phase=args.phase,
        action=command[0],
        sanitized_input=shlex.join(command),
        cwd=args.cwd,
        status=status,
        exit_code=returncode,
        duration_seconds=time.monotonic() - started,
        error_type=error_type or (None if returncode == 0 else "command_failed"),
        evidence_paths=[str(stdout_path), str(stderr_path)],
        sco_job_id=args.sco_job_id,
    )
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
