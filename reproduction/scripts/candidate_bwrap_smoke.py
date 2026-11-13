import argparse
import json
import os
import shutil
import sys
import textwrap
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))

from rsiflow.task_meta.sandbox import LinuxSandbox, SandboxLimits, SandboxUnavailable

BACKEND = "linux_bwrap_seccomp_v1"


def negative_program(marker, checkpoint):
    return textwrap.dedent(f"""
        import sys

        def trace(stage):
            print(f'negative_stage={{stage}}', file=sys.stderr, flush=True)

        trace('import_ctypes')
        import ctypes
        trace('import_errno')
        import errno
        trace('import_json')
        import json
        trace('import_os')
        import os
        trace('import_platform')
        import platform
        trace('import_signal')
        import signal
        trace('import_socket')
        import socket

        trace('runtime_setup')
        marker = {str(marker)!r}
        checkpoint = {str(checkpoint)!r}
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long

        def denied(result):
            return result == -1 and ctypes.get_errno() == errno.EPERM

        def path_absent(path):
            return not os.path.lexists(path)

        def runtime_write_denied():
            try:
                with open('/usr/lib/task2-candidate-write', 'w') as output:
                    output.write('denied')
            except OSError as exc:
                return exc.errno in {{errno.EROFS, errno.EACCES, errno.EPERM, errno.ENOENT}}
            return False

        def scratch_write_succeeds():
            with open('scratch-write.txt', 'w') as output:
                output.write('allowed')
            with open('scratch-write.txt') as source:
                return source.read() == 'allowed'

        def external_connect_denied():
            try:
                socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            except OSError as exc:
                return exc.errno == errno.EPERM
            return False

        def mount_denied():
            ctypes.set_errno(0)
            return denied(libc.mount(b'none', b'/', b'tmpfs', 0, None))

        def setns_denied():
            ctypes.set_errno(0)
            return denied(libc.setns(-1, 0))

        def ptrace_denied():
            ctypes.set_errno(0)
            return denied(libc.ptrace(0, 0, None, None))

        def fork_denied():
            try:
                pid = os.fork()
            except OSError as exc:
                return exc.errno == errno.EPERM
            if pid == 0:
                os._exit(0)
            os.waitpid(pid, 0)
            return False

        def clone_denied():
            number = {{'x86_64': 56, 'aarch64': 220}}[platform.machine()]
            ctypes.set_errno(0)
            pid = libc.syscall(number, signal.SIGCHLD, 0, 0, 0, 0)
            if pid == 0:
                os._exit(0)
            if pid > 0:
                os.waitpid(pid, 0)
                return False
            return denied(pid)

        def credential_changes_denied():
            for change in (os.setuid, os.setgid):
                try:
                    change(0)
                except OSError as exc:
                    if exc.errno != errno.EPERM:
                        return False
                else:
                    return False
            return True

        checks = {{}}
        probes = (
            ('data_absent', lambda: path_absent('/data')),
            ('root_absent', lambda: path_absent('/root')),
            ('checkpoint_absent', lambda: path_absent(checkpoint)),
            ('private_marker_absent', lambda: path_absent(marker)),
            ('procfs_absent', lambda: path_absent('/proc/self')),
            ('runtime_write_denied', runtime_write_denied),
            ('scratch_write_succeeds', scratch_write_succeeds),
            ('external_connect_denied', external_connect_denied),
            ('mount_denied', mount_denied),
            ('setns_denied', setns_denied),
            ('ptrace_denied', ptrace_denied),
            ('fork_denied', fork_denied),
            ('clone_denied', clone_denied),
            ('credential_changes_denied', credential_changes_denied),
        )
        for name, probe in probes:
            trace(name)
            checks[name] = probe()
        trace('serialize')
        print(json.dumps({{'checks': checks, 'environment': dict(os.environ)}}))
    """)


def require_negative_result(result):
    if result.returncode != 0:
        stages = [
            line.removeprefix("negative_stage=")
            for line in result.stderr.splitlines()
            if line.startswith("negative_stage=")
        ]
        stage = stages[-1] if stages else "before_first_trace"
        raise SandboxUnavailable(
            f"negative probe did not complete at {stage}: returncode={result.returncode} "
            f"timed_out={result.timed_out} wall_seconds={result.wall_seconds:.3f}; "
            f"stderr={result.stderr[:512]}"
        )
    return json.loads(result.stdout)


def run_smoke(output):
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    unique = uuid.uuid4().hex
    marker = output.parent / f".task2-private-marker-{unique}"
    scratch_root = output.parent / f".task2-candidate-scratch-{unique}"
    checkpoint = PROJECT / "RSIFlow_4B"
    parent_key = f"task2-parent-only-nonsecret-{unique}"
    report = {
        "schema_version": 1,
        "backend": BACKEND,
        "functional": {},
        "negative": {},
        "timeout": {},
        "session": {},
        "infrastructure_error": None,
        "ok": False,
    }
    try:
        marker.write_text("planted non-secret marker\n", encoding="utf-8")
        assert marker.is_file() and checkpoint.exists()
        os.environ["RSIFLOW_SANDBOX_BACKEND"] = BACKEND
        os.environ["OPENAI_API_KEY"] = parent_key
        runner = LinuxSandbox(
            scratch_root=scratch_root,
            limits=SandboxLimits(wall_seconds=10.0, cpu_seconds=5, output_bytes=4096),
        )
        limited_runner = LinuxSandbox(
            scratch_root=scratch_root,
            limits=SandboxLimits(wall_seconds=10.0, cpu_seconds=5, output_bytes=128),
        )
        timeout_runner = LinuxSandbox(
            scratch_root=scratch_root,
            limits=SandboxLimits(wall_seconds=1.0, cpu_seconds=5, output_bytes=4096),
        )

        normal = runner.run("import sys; print('ok'); print(sys.stdin.read().upper())", stdin="stdin")
        report["functional"].update({
            "normal_stdout": normal.stdout == "ok\nSTDIN\n",
            "normal_returncode": normal.returncode == 0,
            "backend_identity": normal.backend == BACKEND,
            "normal_has_no_infrastructure_error": normal.infrastructure_error is None,
        })
        extra = runner.run(
            "print(open('input.txt').read(), end='')", extra_files={"input.txt": "extra\n"}
        )
        report["functional"]["extra_file"] = extra.stdout == "extra\n" and extra.returncode == 0
        wrong = runner.run("raise ValueError('deliberate candidate wrong answer')")
        report["functional"]["candidate_error_is_not_infrastructure"] = (
            wrong.returncode != 0
            and wrong.infrastructure_error is None
            and "deliberate candidate wrong answer" in wrong.stderr
        )
        truncated = limited_runner.run("print('x' * 10000)")
        report["functional"]["output_truncated"] = (
            truncated.output_truncated and len(truncated.stdout.encode()) == 128
        )

        negative = runner.run(negative_program(marker, checkpoint))
        payload = require_negative_result(negative)
        report["negative"].update(payload["checks"])
        environment = payload["environment"]
        report["negative"].update({
            "private_marker_planted_on_host": marker.is_file(),
            "candidate_key_absent": "OPENAI_API_KEY" not in environment,
            "os_environ_output_excludes_parent_key": parent_key not in negative.stdout,
            "negative_has_no_infrastructure_error": negative.infrastructure_error is None,
        })

        timed = timeout_runner.run("import time; time.sleep(5)")
        report["timeout"].update({
            "timed_out": timed.timed_out,
            "wall_bounded": timed.wall_seconds < 3,
            "process_group_killed": timed.returncode == -9,
            "scratch_cleaned": not scratch_root.exists() or not any(scratch_root.iterdir()),
            "timeout_has_no_infrastructure_error": timed.infrastructure_error is None,
        })

        session_code = (
            "import json, sys\n"
            "count = 0\n"
            "for line in sys.stdin:\n"
            "    count += 1\n"
            "    print(json.dumps({'echo': json.loads(line), 'count': count}), flush=True)\n"
        )
        with runner.session(session_code) as session:
            first = session.request({"value": 3})
            second = session.request({"value": 4})
            session_scratch = session.scratch
        report["session"].update({
            "echo": first == {"echo": {"value": 3}, "count": 1},
            "persistent_state": second == {"echo": {"value": 4}, "count": 2},
            "session_cleanup": not session_scratch.exists(),
        })

        checks = [
            value
            for section in ("functional", "negative", "timeout", "session")
            for value in report[section].values()
            if isinstance(value, bool)
        ]
        report["ok"] = bool(checks) and all(checks)
    except (SandboxUnavailable, OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        report["infrastructure_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        marker.unlink(missing_ok=True)
        shutil.rmtree(scratch_root, ignore_errors=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "ok": report["ok"],
        "infrastructure_error": report["infrastructure_error"],
    }, sort_keys=True))
    return report["ok"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return 0 if run_smoke(args.output) else 1


if __name__ == "__main__":
    raise SystemExit(main())
