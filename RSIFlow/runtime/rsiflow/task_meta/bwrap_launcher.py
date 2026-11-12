from __future__ import annotations

import argparse
import ctypes
import errno
import json
import math
import os
import resource
from pathlib import PurePosixPath

PROFILES = {
    "candidate": "candidate",
    "candidate_session": "candidate",
    "native_benchmark": "native",
    "persistent_meta": "meta",
}
LIMIT_FIELDS = {
    "wall_seconds", "cpu_seconds", "memory_bytes", "output_bytes", "file_bytes", "processes", "file_descriptors"
}
NAMESPACE_CREATION_FLAGS = (
    0x00000080,  # CLONE_NEWTIME
    0x00020000,  # CLONE_NEWNS
    0x02000000,  # CLONE_NEWCGROUP
    0x04000000,  # CLONE_NEWUTS
    0x08000000,  # CLONE_NEWIPC
    0x10000000,  # CLONE_NEWUSER
    0x20000000,  # CLONE_NEWPID
    0x40000000,  # CLONE_NEWNET
)


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


class _SeccompArgCmp(ctypes.Structure):
    _fields_ = [
        ("arg", ctypes.c_uint),
        ("op", ctypes.c_int),
        ("datum_a", ctypes.c_uint64),
        ("datum_b", ctypes.c_uint64),
    ]


def _denied_syscalls(profile):
    if profile not in {"candidate", "native", "meta"}:
        raise ValueError(f"unknown seccomp profile: {profile}")
    denied = [
        "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
        "ptrace", "process_vm_readv", "process_vm_writev", "mount", "umount2", "pivot_root", "chroot",
        "unshare", "setns", "bpf", "keyctl", "add_key", "request_key", "kexec_load", "kexec_file_load",
        "reboot", "swapon", "swapoff", "open_by_handle_at", "name_to_handle_at", "fanotify_init",
        "perf_event_open", "userfaultfd", "io_uring_setup", "io_uring_enter", "io_uring_register",
        "clone", "clone3", "fork", "vfork", "kill", "tkill", "tgkill", "pidfd_open", "pidfd_getfd",
        "truncate", "truncate64", "chmod", "fchmod", "fchmodat", "chown", "fchown", "lchown", "fchownat",
        "link", "linkat", "symlink", "symlinkat", "rename", "renameat", "renameat2", "setxattr",
        "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr", "utime", "utimes",
        "futimesat", "utimensat", "setuid", "setgid", "setreuid", "setregid", "setresuid", "setresgid",
        "setgroups", "capset", "quotactl", "acct", "lookup_dcookie", "syslog", "iopl", "ioperm",
        "modify_ldt", "memfd_create", "init_module", "finit_module", "delete_module",
    ]
    if profile == "native":
        denied = [name for name in denied if name not in {"clone", "clone3", "rename", "renameat", "renameat2"}]
        denied.extend(["setsid", "setpgid"])
    elif profile == "meta":
        permitted = {
            "socket", "socketpair", "connect", "bind", "listen", "accept", "accept4",
            "clone", "clone3", "fork", "vfork",
        }
        denied = [name for name in denied if name not in permitted]
    return denied


def _drop_credentials():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.restype = ctypes.c_int
    for capability in range(64):
        if libc.prctl(24, capability, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            if error != errno.EINVAL:
                raise OSError(error, "PR_CAPBSET_DROP failed")
    header = _CapHeader(0x20080522, 0)
    data = (_CapData * 2)()
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "capset failed")
    if libc.prctl(38, 1, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, "PR_SET_NO_NEW_PRIVS failed")


def _apply_limits(limits):
    if not isinstance(limits, dict) or set(limits) != LIMIT_FIELDS:
        raise ValueError("limits-json must contain exactly the fixed bwrap limits")
    for name, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid bwrap limit: {name}")
    for name in ("cpu_seconds", "memory_bytes", "output_bytes", "file_bytes", "processes", "file_descriptors"):
        if not isinstance(limits[name], int):
            raise ValueError(f"bwrap limit must be an integer: {name}")
    values = (
        (resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"] + 1)),
        (resource.RLIMIT_AS, (limits["memory_bytes"], limits["memory_bytes"])),
        (resource.RLIMIT_FSIZE, (limits["file_bytes"], limits["file_bytes"])),
        (resource.RLIMIT_NPROC, (limits["processes"], limits["processes"])),
        (resource.RLIMIT_NOFILE, (limits["file_descriptors"], limits["file_descriptors"])),
        (resource.RLIMIT_CORE, (0, 0)),
    )
    for name, value in values:
        resource.setrlimit(name, value)


def _parse_preserve_fds(value):
    if not value:
        return ()
    try:
        descriptors = tuple(int(item) for item in value.split(","))
    except ValueError as exc:
        raise ValueError("preserve-fds must be a comma-separated list of integers") from exc
    return descriptors


def _validate_descriptor_policy(profile, preserve_fds):
    valid = (
        profile == "native_benchmark"
        and len(preserve_fds) == 2
        and len(set(preserve_fds)) == 2
        and all(fd > 2 for fd in preserve_fds)
    )
    if preserve_fds and not valid:
        raise ValueError("only two unique native RPC descriptors may be preserved")
    if profile == "native_benchmark" and not valid:
        raise ValueError("native benchmark requires exactly two RPC descriptors")


def _close_inherited_descriptors(preserve_fds, maximum):
    maximum = min(maximum, 1024 * 1024)
    if hasattr(os, "close_range"):
        start = 3
        for descriptor in sorted(preserve_fds):
            if start < descriptor:
                os.close_range(start, descriptor - 1)
            start = descriptor + 1
        if start <= maximum:
            os.close_range(start, maximum)
        return
    preserved = set(preserve_fds)
    for descriptor in range(3, maximum + 1):
        if descriptor not in preserved:
            try:
                os.close(descriptor)
            except OSError as exc:
                if exc.errno != errno.EBADF:
                    raise


def _restrict_syscalls(profile, library="libseccomp.so.2"):
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    seccomp.seccomp_load.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    context = seccomp.seccomp_init(0x7FFF0000)
    if not context:
        raise RuntimeError("seccomp_init failed")
    action = 0x00050000 | errno.EPERM
    try:
        for name in _denied_syscalls(profile):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, action, number, 0) != 0:
                raise RuntimeError(f"cannot deny syscall {name}")
        if profile in {"native", "meta"}:
            number = seccomp.seccomp_syscall_resolve_name(b"clone3")
            if number >= 0 and seccomp.seccomp_rule_add(
                context, 0x00050000 | errno.ENOSYS, number, 0
            ) != 0:
                raise RuntimeError("cannot force clone3 fallback to clone")
        if profile == "native":
            number = seccomp.seccomp_syscall_resolve_name(b"clone")
            comparator = _SeccompArgCmp(0, 7, 0x00000100, 0)
            if number >= 0 and seccomp.seccomp_rule_add(context, action, number, 1, comparator) != 0:
                raise RuntimeError("cannot restrict clone to CLONE_THREAD")
        elif profile == "meta":
            number = seccomp.seccomp_syscall_resolve_name(b"clone")
            for flag in NAMESPACE_CREATION_FLAGS:
                comparator = _SeccompArgCmp(0, 7, flag, flag)
                if number >= 0 and seccomp.seccomp_rule_add(context, action, number, 1, comparator) != 0:
                    raise RuntimeError("cannot deny clone namespace creation")
        if seccomp.seccomp_load(context) != 0:
            raise RuntimeError("seccomp_load failed")
    finally:
        seccomp.seccomp_release(context)


def _environment(value):
    try:
        env = json.loads(value)
    except ValueError as exc:
        raise ValueError("env-json must be valid JSON") from exc
    if not isinstance(env, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in env.items()):
        raise ValueError("env-json must be an object of strings")
    for key, item in env.items():
        upper = key.upper()
        if upper == "RSIFLOW_RELAY_KEY":
            if item != "pipe-only":
                raise ValueError("RSIFLOW_RELAY_KEY must be the literal pipe-only placeholder")
        elif upper == "OPENAI_API_KEY" or upper.endswith("_API_KEY") or "SECRET" in upper or "TOKEN" in upper:
            raise ValueError(f"credential environment variable denied: {key}")
    return env


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, choices=sorted(PROFILES))
    parser.add_argument("--limits-json", required=True)
    parser.add_argument("--env-json", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--preserve-fds", required=True)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.argv)
    if command and command[0] == "--":
        command = command[1:]
    if not command or not PurePosixPath(command[0]).is_absolute():
        raise ValueError("an absolute target command is required")
    cwd = PurePosixPath(args.cwd)
    if not cwd.is_absolute() or ".." in args.cwd.split("/"):
        raise ValueError("cwd must be absolute and normalized")
    preserve_fds = _parse_preserve_fds(args.preserve_fds)
    _validate_descriptor_policy(args.profile, preserve_fds)
    env = _environment(args.env_json)
    try:
        limits = json.loads(args.limits_json)
    except ValueError as exc:
        raise ValueError("limits-json must be valid JSON") from exc
    if not isinstance(limits, dict) or set(limits) != LIMIT_FIELDS:
        raise ValueError("limits-json must contain exactly the fixed bwrap limits")
    for name, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"invalid bwrap limit: {name}")
    maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
    if maximum == resource.RLIM_INFINITY:
        maximum = 1024 * 1024
    os.umask(0o077)
    _drop_credentials()
    _apply_limits(limits)
    _close_inherited_descriptors(preserve_fds, maximum)
    os.chdir(args.cwd)
    _restrict_syscalls(PROFILES[args.profile])
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
