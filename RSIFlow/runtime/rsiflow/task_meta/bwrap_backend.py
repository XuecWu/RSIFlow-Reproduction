from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .sandbox import SandboxUnavailable

EXPECTED_PROFILES = {
    "candidate": {"seccomp": "candidate", "allow_threads": False, "allow_processes": False},
    "candidate_session": {"seccomp": "candidate", "allow_threads": False, "allow_processes": False},
    "native_benchmark": {"seccomp": "native", "allow_threads": True, "allow_processes": False},
    "persistent_meta": {"seccomp": "meta", "allow_threads": True, "allow_processes": True},
}


@dataclass(frozen=True)
class BwrapLimits:
    wall_seconds: float
    cpu_seconds: int
    memory_bytes: int
    output_bytes: int
    file_bytes: int
    processes: int
    file_descriptors: int = 32

    def __post_init__(self):
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0
               for value in asdict(self).values()):
            raise ValueError("All bwrap limits must be finite and positive")
        integer_values = (
            self.cpu_seconds, self.memory_bytes, self.output_bytes, self.file_bytes, self.processes,
            self.file_descriptors,
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in integer_values):
            raise ValueError("Bwrap integer limits must use integer values")


@dataclass(frozen=True)
class BwrapMount:
    source: Path
    target: str
    mode: str = "ro"


@dataclass(frozen=True)
class BwrapRequest:
    profile: str
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    limits: BwrapLimits
    mounts: tuple[BwrapMount, ...]
    preserve_fds: tuple[int, ...] = ()


class BubblewrapBackend:
    def __init__(self, project, *, manifest_path=None, allowed_write_roots=()):
        self.project = Path(project).absolute()
        self.manifest_path = Path(manifest_path).absolute() if manifest_path else (
            self.project / "RSIFlow/configs/bwrap_isolation.json"
        )
        self.allowed_write_roots = tuple(Path(path).absolute() for path in allowed_write_roots)
        self.launcher = self.project / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py"
        self.binary = self.project / "assets/isolation/bin/bwrap"
        self.manifest = None

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _lstat(path):
        try:
            return os.lstat(path)
        except OSError as exc:
            raise SandboxUnavailable(f"bwrap path unavailable: {path}") from exc

    @classmethod
    def _reject_symlinks(cls, path):
        path = Path(path)
        current = Path(path.anchor)
        info = None
        for part in path.parts[1:]:
            current = current / part
            info = cls._lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise SandboxUnavailable(f"bwrap path contains a symlink: {current}")
        return info

    @classmethod
    def _regular_file(cls, path, description):
        info = cls._reject_symlinks(path)
        if info is None or not stat.S_ISREG(info.st_mode):
            raise SandboxUnavailable(f"bwrap {description} is not a regular file: {path}")

    def _load_manifest(self):
        self._regular_file(self.manifest_path, "manifest")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SandboxUnavailable(f"invalid bwrap manifest: {exc}") from exc
        if manifest.get("schema_version") != 1 or manifest.get("backend") != "linux_bwrap_seccomp_v1":
            raise SandboxUnavailable("unsupported bwrap manifest identity")
        binary = manifest.get("binary", {})
        relative = binary.get("path", "")
        target = PurePosixPath(relative)
        if not relative or target.is_absolute() or ".." in target.parts or str(target) != relative:
            raise SandboxUnavailable(f"invalid bwrap binary path: {relative!r}")
        if manifest.get("profiles") != EXPECTED_PROFILES:
            raise SandboxUnavailable("unsupported bwrap profiles")
        self.manifest = manifest
        self.binary = self.project.joinpath(*target.parts)

    def _namespaces_available(self):
        command = [str(self.binary), "--unshare-all", "--new-session", "--die-with-parent"]
        for path in ("/usr", "/lib", "/lib64"):
            if Path(path).exists():
                command.extend(["--ro-bind", path, path])
        command.extend(["/usr/bin/python3", "-I", "-B", "-c", "pass"])
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0

    def validate(self):
        self._load_manifest()
        binary = self.manifest["binary"]
        self._regular_file(self.binary, "binary")
        actual_hash = self._sha256(self.binary)
        if actual_hash != binary.get("sha256"):
            raise SandboxUnavailable("bwrap binary hash mismatch")
        try:
            result = subprocess.run(
                [str(self.binary), "--version"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
                env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            )
        except OSError as exc:
            raise SandboxUnavailable(f"cannot execute bwrap: {exc}") from exc
        version = result.stdout.strip()
        if result.returncode != 0 or version != f"bubblewrap {binary.get('version')}":
            raise SandboxUnavailable(f"bwrap version mismatch: {version!r}")
        self._regular_file(self.launcher, "launcher")
        if not self._namespaces_available():
            raise SandboxUnavailable("bwrap namespace support unavailable")
        return {
            "backend": "linux_bwrap_seccomp_v1",
            "binary_sha256": actual_hash,
            "binary_version": binary["version"],
            "launcher_sha256": self._sha256(self.launcher),
            "profile_sha256": hashlib.sha256(
                json.dumps(self.manifest["profiles"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "limits_sha256": hashlib.sha256(inspect.getsource(BwrapLimits).encode()).hexdigest(),
            "namespaces": True,
        }

    @staticmethod
    def _target(value):
        target = PurePosixPath(value)
        if not value or not target.is_absolute() or ".." in value.split("/"):
            raise ValueError(f"bwrap target must be absolute and normalized: {value!r}")
        if str(target) == "/":
            raise ValueError("bwrap cannot replace the namespace root")
        return str(target)

    def _mount_source(self, mount):
        source = Path(mount.source)
        if not source.is_absolute():
            raise ValueError("bwrap mount source must be absolute")
        broad = {Path("/"), Path("/data"), Path("/home"), Path("/root"), Path("/proc"), Path("/sys")}
        if source in broad:
            raise ValueError(f"broad bwrap source mount denied: {source}")
        info = self._reject_symlinks(source)
        if info is None:
            raise SandboxUnavailable(f"bwrap mount source unavailable: {source}")
        if mount.mode == "dev":
            if source.parent != Path("/dev") or not (stat.S_ISCHR(info.st_mode) or stat.S_ISBLK(info.st_mode)):
                raise ValueError("dev mounts must name one device directly under /dev")
        elif not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise ValueError("bwrap mounts require a regular file or directory")
        if mount.mode != "rw":
            return source
        if source == self.project or source in self.project.parents:
            raise ValueError("repository-root or broader bwrap writes are denied")
        protected = tuple(
            self.project / name for name in ("assets/private", "assets/models", "assets/raw", "data", "checkpoints")
        )
        if any(source == path or path in source.parents for path in protected):
            raise ValueError("private, checkpoint, and raw-data bwrap writes are denied")
        if source in {Path("/tmp"), Path("/dev")}:
            raise ValueError(f"broad writable bwrap mount denied: {source}")
        for root in self.allowed_write_roots:
            if root in broad or root in {Path("/tmp"), Path("/dev")} or root == self.project or root in self.project.parents:
                raise ValueError(f"broad repository or host allowed_write_roots are denied: {root}")
            self._reject_symlinks(root)
            if source != root and root in source.parents:
                return source
        raise ValueError(f"writable bwrap output is outside allowed_write_roots: {source}")

    @staticmethod
    def _validate_environment(env):
        if not isinstance(env, dict) or any(not isinstance(key, str) or not isinstance(value, str)
                                           for key, value in env.items()):
            raise ValueError("bwrap environment must contain only string keys and values")
        for key, value in env.items():
            upper = key.upper()
            if upper == "RSIFLOW_RELAY_KEY":
                if value != "pipe-only":
                    raise ValueError("RSIFLOW_RELAY_KEY must be the literal pipe-only placeholder")
            elif upper == "OPENAI_API_KEY" or upper.endswith("_API_KEY") or "SECRET" in upper or "TOKEN" in upper:
                raise ValueError(f"credential environment variable denied: {key}")

    @staticmethod
    def _validate_descriptors(profile, preserve_fds):
        valid = (
            profile == "native_benchmark"
            and len(preserve_fds) == 2
            and len(set(preserve_fds)) == 2
            and all(isinstance(fd, int) and not isinstance(fd, bool) and fd > 2 for fd in preserve_fds)
        )
        if preserve_fds and not valid:
            raise ValueError("only two unique native RPC descriptors may be preserved")
        if profile == "native_benchmark" and not valid:
            raise ValueError("native benchmark requires exactly two RPC descriptors")

    def command(self, request):
        self.validate()
        if request.profile not in EXPECTED_PROFILES:
            raise ValueError(f"unknown bwrap profile: {request.profile}")
        if not isinstance(request.limits, BwrapLimits):
            raise ValueError("request limits must be BwrapLimits")
        if not request.argv or any(not isinstance(value, str) or not value or "\0" in value for value in request.argv):
            raise ValueError("bwrap argv must contain non-empty strings")
        if not PurePosixPath(request.argv[0]).is_absolute():
            raise ValueError("bwrap executable must be absolute")
        cwd = PurePosixPath(request.cwd)
        if not cwd.is_absolute() or ".." in request.cwd.split("/"):
            raise ValueError("bwrap cwd must be absolute and normalized")
        self._validate_environment(request.env)
        self._validate_descriptors(request.profile, request.preserve_fds)
        command = [str(self.binary), "--unshare-all", "--new-session", "--die-with-parent", "--clearenv"]
        targets = set()
        for mount in request.mounts:
            if mount.mode not in {"ro", "rw", "dev"}:
                raise ValueError(f"unknown bwrap mount mode: {mount.mode}")
            target = self._target(mount.target)
            if target in targets:
                raise ValueError(f"duplicate bwrap target: {target}")
            targets.add(target)
            source = self._mount_source(mount)
            flag = {"ro": "--ro-bind", "rw": "--bind", "dev": "--dev-bind"}[mount.mode]
            command.extend([flag, str(source), target])
        launcher_target = "/opt/rsiflow/bwrap_launcher.py"
        if launcher_target in targets:
            raise ValueError("trusted launcher target cannot be replaced")
        command.extend(["--ro-bind", str(self.launcher), launcher_target])
        command.extend([
            "/usr/bin/python3", "-I", "-B", launcher_target,
            "--profile", request.profile,
            "--limits-json", json.dumps(asdict(request.limits), sort_keys=True, separators=(",", ":")),
            "--env-json", json.dumps(request.env, sort_keys=True, separators=(",", ":")),
            "--cwd", request.cwd,
            "--preserve-fds", ",".join(str(fd) for fd in request.preserve_fds),
            "--",
            *request.argv,
        ])
        return command
