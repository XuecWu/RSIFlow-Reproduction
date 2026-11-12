import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path

EXPECTED_SHA256 = "d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca"
EXPECTED_VERSION = "0.6.1"


def _regular_file(path):
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise RuntimeError(f"bwrap is not a regular file: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError(f"bwrap is not a regular file: {path}")


def _reject_symlink_parents(path):
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f"bwrap installation path contains a symlink: {current}")


def _version(executable):
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        )
    except OSError as exc:
        raise RuntimeError(f"cannot execute bwrap --version: {exc}") from exc
    version = result.stdout.strip()
    if result.returncode != 0 or version != f"bubblewrap {EXPECTED_VERSION}":
        raise RuntimeError(f"unexpected bwrap version: {version!r}")
    return EXPECTED_VERSION


def install(source, root):
    source = Path(source).absolute()
    root = Path(root).absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise RuntimeError(f"bwrap source must be a non-symlink regular file: {source}") from exc
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise RuntimeError(f"bwrap source must be a regular file: {source}")
        with os.fdopen(source_fd, "rb", closefd=False) as source_file:
            payload = source_file.read()
    finally:
        os.close(source_fd)
    source_hash = hashlib.sha256(payload).hexdigest()
    if source_hash != EXPECTED_SHA256:
        raise RuntimeError(f"bwrap source hash mismatch: {source_hash}")

    binary_root = root / "bin"
    binary_root.mkdir(parents=True, exist_ok=True)
    _reject_symlink_parents(root)
    _reject_symlink_parents(binary_root)
    destination = binary_root / "bwrap"
    if destination.exists() or destination.is_symlink():
        _regular_file(destination)
    temporary_fd, temporary_name = tempfile.mkstemp(prefix=".bwrap-", dir=binary_root)
    temporary = Path(temporary_name)
    receipt = None
    try:
        with os.fdopen(temporary_fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.chmod(0o755)
        _regular_file(temporary)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != source_hash:
            raise RuntimeError("bwrap temporary destination hash mismatch")
        version = _version(temporary)
        os.replace(temporary, destination)
        _regular_file(destination)
        destination_hash = hashlib.sha256(destination.read_bytes()).hexdigest()
        if destination_hash != source_hash:
            raise RuntimeError("bwrap destination hash mismatch")
        receipt = {
            "source_sha256": source_hash,
            "destination_sha256": destination_hash,
            "version": version,
        }
        receipt_fd, receipt_name = tempfile.mkstemp(prefix=".receipt-", dir=root)
        try:
            with os.fdopen(receipt_fd, "w", encoding="utf-8") as output:
                json.dump(receipt, output, sort_keys=True, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(receipt_name, root / "install-receipt.json")
        except BaseException:
            Path(receipt_name).unlink(missing_ok=True)
            raise
    finally:
        temporary.unlink(missing_ok=True)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    install(args.source, args.root)


if __name__ == "__main__":
    main()
