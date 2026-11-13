from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from .bwrap_backend import BubblewrapBackend, BwrapLimits, BwrapMount, BwrapRequest

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def validate():
    return BubblewrapBackend(PROJECT_ROOT).validate()


def create_scratch(runner, code, extra_files, *, session=False):
    if runner.scratch_root:
        runner.scratch_root.mkdir(parents=True, exist_ok=True)
    prefix = "rsi-environment-" if session else "rsi-candidate-"
    temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=runner.scratch_root or "/tmp")
    scratch = Path(temporary.name)
    reserved = {"candidate.py", "stderr"} if session else {"candidate.py", "stdout", "stderr"}
    try:
        (scratch / "candidate.py").write_text(code, encoding="utf-8")
        for name, text in (extra_files or {}).items():
            if Path(name).name != name or name in reserved:
                raise ValueError("Invalid sandbox extra file name")
            (scratch / name).write_text(text, encoding="utf-8")
    except BaseException:
        temporary.cleanup()
        raise
    return temporary, scratch


def command(runner, scratch, *, profile, seed):
    if profile not in {"candidate", "candidate_session"}:
        raise ValueError(f"invalid candidate bwrap profile: {profile}")
    scratch = Path(scratch).absolute()
    mounts = [BwrapMount(runner.python, "/usr/bin/python3", "ro")]
    for alias in ("/lib", "/lib64"):
        path = Path(alias)
        if path.exists():
            mounts.append(BwrapMount(path.resolve(), alias, "ro"))
    mounted = set()
    for source in runner.runtime_read_paths:
        if source in mounted:
            continue
        mounted.add(source)
        mode = "dev" if source.parent == Path("/dev") else "ro"
        mounts.append(BwrapMount(source, str(source), mode))
    mounts.append(BwrapMount(scratch, "/workspace", "rw"))
    argv = ("/usr/bin/python3", "-s", "-B", "candidate.py")
    if profile == "candidate_session":
        argv = ("/usr/bin/python3", "-s", "-B", "-u", "candidate.py")
    request = BwrapRequest(
        profile=profile,
        argv=argv,
        cwd="/workspace",
        env=runner._environment(Path("/workspace"), seed),
        limits=BwrapLimits(**asdict(runner.limits), file_descriptors=32),
        mounts=tuple(mounts),
    )
    backend = BubblewrapBackend(PROJECT_ROOT, allowed_write_roots=(scratch,))
    return backend.command(request)
