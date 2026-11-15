"""One disposable process per upstream RSIFlow validation attempt.

The controller never imports generated code. The child validates a private copy,
with only scratch writable and no provider credentials. Repairs remain in the
controller's existing production loop; this worker never generates a candidate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace


class ValidationResult(SimpleNamespace):
    def to_dict(self):
        return dict(vars(self))


def _bwrap_command(scratch, copied, project, report_path, env, timeout):
    from rsiflow.task_meta.bwrap_backend import BubblewrapBackend, BwrapLimits, BwrapMount, BwrapRequest
    from rsiflow.task_meta.rsiflow_production import UPSTREAM_ROOT

    script = Path(__file__).resolve()
    runtime = script.parents[2]
    mounts = [
        BwrapMount(Path(sys.executable).resolve(), "/usr/bin/python3", "ro"),
        BwrapMount(runtime, str(runtime), "ro"),
        BwrapMount(UPSTREAM_ROOT, str(UPSTREAM_ROOT), "ro"),
        BwrapMount(scratch, str(scratch), "rw"),
    ]
    for value in (
        Path(sys.prefix).resolve(), "/lib", "/lib64", "/usr/lib", "/usr/lib64",
        "/etc/ld.so.cache", "/etc/localtime", "/dev/null", "/dev/urandom", "/dev/random",
    ):
        path = Path(value)
        if path.exists():
            resolved = path.resolve()
            mode = "dev" if resolved.parent == Path("/dev") else "ro"
            mounts.append(BwrapMount(resolved, str(path), mode))
    request = BwrapRequest(
        profile="candidate",
        argv=(
            "/usr/bin/python3", "-I", "-B", "-X", "faulthandler", str(script),
            str(copied), str(project), str(report_path),
        ),
        cwd=str(scratch),
        env={
            **{key: value for key, value in env.items() if key != "TOKENIZERS_PARALLELISM"},
            "RSIFLOW_SANDBOX_BACKEND": "linux_bwrap_seccomp_v1",
        },
        limits=BwrapLimits(
            wall_seconds=timeout, cpu_seconds=2400, memory_bytes=8 * 1024**3,
            output_bytes=8192, file_bytes=8 * 1024 * 1024, processes=1, file_descriptors=32,
        ),
        mounts=tuple(mounts),
    )
    backend = BubblewrapBackend(script.parents[4], allowed_write_roots=(scratch,))
    return backend.command(request)


def validate_candidate(candidate_dir: Path, *, timeout: float = 300) -> ValidationResult:
    from rsiflow.task_meta.rsiflow_manifest import HarnessBundleManifest

    candidate_dir = Path(candidate_dir).resolve()
    manifest = HarnessBundleManifest.from_directory(candidate_dir)
    # Keep diagnostics with the workflow, not in the home directory. Each attempt
    # gets a fresh interpreter and copy, including after a failed-build repair.
    with tempfile.TemporaryDirectory(prefix=".validation-", dir=candidate_dir.parent) as raw:
        scratch = Path(raw)
        project = scratch / "project"
        # The host attempt directory may contain dots/spaces. It is storage,
        # not a Python package name; only the isolated layout is imported.
        copied = project / "generated_harnesses" / "rounds" / "validation_round" / candidate_dir.name
        manifest.materialize(copied)
        for package in (copied.parent, copied.parent.parent, copied.parent.parent.parent):
            (package / "__init__.py").touch()
        report_path = scratch / "report.json"
        env = {
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "LANG": "C.UTF-8", "HOME": str(scratch), "TMPDIR": str(scratch),
            "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1", "TOKENIZERS_PARALLELISM": "false",
            "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1",
        }
        backend = os.environ.get("RSIFLOW_SANDBOX_BACKEND", "linux_landlock_seccomp")
        if backend == "linux_bwrap_seccomp_v1":
            command = _bwrap_command(scratch, copied, project, report_path, env, timeout)
        elif backend == "linux_landlock_seccomp":
            command = [
                sys.executable, "-I", "-B", "-X", "faulthandler", str(Path(__file__).resolve()),
                str(copied), str(project), str(report_path),
            ]
        else:
            from rsiflow.task_meta.sandbox import SandboxUnavailable
            raise SandboxUnavailable(f"unknown sandbox backend: {backend}")
        with (scratch / "stdout.log").open("wb") as out, (scratch / "stderr.log").open("wb") as err:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=scratch, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                    close_fds=True, start_new_session=True, pass_fds=getattr(command, "pass_fds", ()),
                )
            finally:
                close = getattr(command, "close", None)
                if close:
                    close()
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
            finally:
                # Also remove descendants if a candidate left background work.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
        if timed_out:
            return ValidationResult(
                candidate_name=candidate_dir.name, candidate_path=str(candidate_dir),
                import_name="", verdict="failed_build", checks=[],
                errors=[f"Candidate validation exceeded {timeout:g} seconds"], traceback="",
            )
        if process.returncode != 0 or not report_path.is_file():
            with (scratch / "stderr.log").open("rb") as stream:
                diagnostic = stream.read(8192).decode("utf-8", "replace")
            if (scratch / "ready").is_file():
                return ValidationResult(
                    candidate_name=candidate_dir.name, candidate_path=str(candidate_dir),
                    import_name="", verdict="failed_build", checks=[],
                    errors=[f"Candidate validation worker exited without a report (exit={process.returncode})"],
                    traceback=diagnostic,
                )
            raise RuntimeError(
                f"RSIFlow validation worker failed (exit={process.returncode}): {diagnostic}"
            )
        if report_path.stat().st_size > 8 * 1024 * 1024:
            raise RuntimeError("RSIFlow validation report exceeds 8 MiB")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if "worker_error" in payload:
            raise RuntimeError("RSIFlow validation isolation failed: " + payload["worker_error"])
        if payload.get("verdict") not in {
            "passed", "failed_static", "failed_import", "failed_build", "failed_environment"
        } or not isinstance(payload.get("errors"), list) or not isinstance(payload.get("checks"), list):
            raise RuntimeError("Malformed upstream validation report")
        # Reports must refer to the durable candidate, not a deleted scratch copy.
        encoded = json.dumps(payload, ensure_ascii=False).replace(str(copied), str(candidate_dir))
        return ValidationResult(**json.loads(encoded))


def _worker(candidate: Path, project: Path, report_path: Path) -> None:
    import resource

    runtime = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(runtime))
    from rsiflow.task_meta.rsiflow_production import UPSTREAM_ROOT, upstream_validation_module
    from rsiflow.task_meta.rsiflow_runtime import _install_json_repair_fallback
    from rsiflow.task_meta.sandbox import _restrict_filesystem, _restrict_syscalls

    scratch = report_path.parent
    readable = [runtime, UPSTREAM_ROOT, Path(sys.prefix).resolve()]
    readable.extend(Path(p).resolve() for p in (
        "/lib", "/lib64", "/usr/lib", "/usr/lib64", "/etc/ld.so.cache",
        "/etc/localtime", "/dev/null", "/dev/urandom", "/dev/random",
        "/proc/self", "/proc/cpuinfo", "/proc/meminfo", "/sys/devices/system/cpu",
    ) if Path(p).exists())
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (2400, 2401))
    resource.setrlimit(resource.RLIMIT_AS, (8 * 1024**3, 8 * 1024**3))
    if os.environ.get("RSIFLOW_SANDBOX_BACKEND") != "linux_bwrap_seccomp_v1":
        # The legacy worker installs Landlock before importing generated code.
        try:
            _restrict_filesystem(readable, scratch, write_paths=(Path("/dev/null"),))
            _restrict_syscalls(allow_threads=True)
        except Exception as exc:
            report_path.write_text(json.dumps({"worker_error": f"{type(exc).__name__}: {exc}"}))
            return
    for source in (UPSTREAM_ROOT, UPSTREAM_ROOT / "harness", UPSTREAM_ROOT / "harness_factory"):
        sys.path.insert(0, str(source))
    _install_json_repair_fallback()
    upstream = upstream_validation_module()
    (scratch / "ready").touch()
    report = upstream.validate_once(candidate, project, "generated_harnesses")
    report_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    _worker(*(Path(value).resolve() for value in sys.argv[1:]))
