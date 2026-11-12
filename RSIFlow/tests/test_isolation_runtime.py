import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from rsiflow.task_meta.meta_backends import isolation_runtime


def test_stdlib_is_discovered_from_selected_system_python():
    discover = getattr(isolation_runtime, "python_stdlib_path", None)
    assert discover is not None, "selected Python must provide its own stdlib path"
    python = Path("/usr/bin/python3")
    expected = Path(subprocess.check_output([
        str(python), "-I", "-B", "-c", "import sysconfig; print(sysconfig.get_path('stdlib'))"
    ], text=True).strip())
    assert discover(python) == expected


def test_python_versioned_etc_symlink_is_allowed(tmp_path):
    python = Path("/usr/bin/python3")
    version = isolation_runtime.python_stdlib_path(python).name
    source = tmp_path / "sitecustomize.py"
    source.symlink_to(Path("/etc") / version / "sitecustomize.py")
    instance = object.__new__(isolation_runtime.IsolationRuntime)
    instance.python = python
    instance._copy_one(source, tmp_path / "runtime", set())
    destination = tmp_path / "runtime" / source.relative_to("/")
    assert destination.is_symlink()
