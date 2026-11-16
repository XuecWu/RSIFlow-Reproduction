import json
from collections import Counter
from pathlib import Path

from evolution_protocol import assert_disjoint, fingerprint, read
from evaluation_manifest import select_specs
from rsiflow.task_meta.round_evolution import RoundStore


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "reproduction/configs/train_4b.json"


def test_machine_config_names_actual_release_and_backbone_sockets():
    config = read(CONFIG)
    assert config["data_release"] == "rounds_3000_5x600_v4"
    assert Path(config["frozen_data_dir"]).name == config["data_release"]
    assert Path(config["validation_manifest"]).parent.name == "validation_744_v4"
    assert "4b" in Path(config["meta_worker_socket"]).name
    assert "4b" in Path(config["meta_relay_socket"]).name
    assert config["run_name"].endswith("eval694")


def test_effective_runtime_config_accepts_custom_provider(tmp_path):
    from task_adapter import TaskAdapter

    adapter = TaskAdapter(ROOT / "RSIFlow", tmp_path / "receipts")
    runtime = adapter._config({"config_path": str(CONFIG)})
    assert runtime.meta.provider == "OpenAI"
    assert runtime.meta.api_key_env == "OPENAI_API_KEY"
    assert runtime.meta.model == "gpt-6-astra"
    assert runtime.meta.base_url == "http://token.acerobotics.com/v1"
    assert Path(runtime.data_dir) == Path(read(CONFIG)["frozen_data_dir"])
    assert [row["base_url"] for row in runtime.task_replicas] == [f"http://127.0.0.1:{port}/v1" for port in (8271, 8272, 8273, 8274)]
    assert not any("/path/to/" in value for value in (runtime.trainer_python, runtime.task_checkpoint, runtime.meta.codex_executable))


def _pinned_commit(repository_root):
    """Resolve the real checkout commit; never trust a directory-name string.

    `git rev-parse` is preferred. When git refuses the checkout (e.g. cross-uid ownership),
    the same commit is read directly from the repository's HEAD/ref files, which is equivalent
    evidence and does not require changing any git trust configuration.
    """
    import subprocess

    root = Path(repository_root).resolve()
    candidates = [root, *root.parents]
    git_dir = next((path / ".git" for path in candidates if (path / ".git").exists()), None)
    assert git_dir is not None, f"No git checkout found above {root}"
    repo = git_dir.parent
    completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=False)
    if completed.returncode == 0:
        return completed.stdout.strip()
    if git_dir.is_file():
        target = git_dir.read_text().strip().removeprefix("gitdir:").strip()
        git_dir = (repo / target).resolve() if not Path(target).is_absolute() else Path(target)
    head = (git_dir / "HEAD").read_text().strip()
    if not head.startswith("ref: "):
        return head
    ref = head[5:]
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text().strip()
    for line in (git_dir / "packed-refs").read_text().splitlines():
        if line.endswith(" " + ref):
            return line.split()[0]
    raise AssertionError(f"Cannot resolve {ref} in {git_dir}")


def test_bfcl_evaluator_uses_pinned_source_and_isolated_python():
    from tool_validation import native_layout

    settings = read(ROOT / "reproduction/configs/validation_744.json")
    spec = settings["tool_benchmarks"]["bfcl_v3"]
    assert spec["official_commit"] == "ea13468e4423454d0c213704fb87cf7cb3990433"
    assert spec["python_executable"].endswith("rsiflow-bfcl/bin/python")
    assert Path(spec["python_executable"]).is_file()
    assert spec["evaluator_archive_sha256"] == "c6c3b8ff8708eb93ed1fe236d55b5edf1e817530af53cdd6b958d04d8f486f21"
    # Contract, not path spelling: the actual checkout must be at the pinned commit and expose
    # the complete pinned BFCL-v3 native layout through the isolated interpreter's spec.
    assert _pinned_commit(spec["repository_root"]) == spec["official_commit"]
    repo, resolved, ids, paths = native_layout("bfcl_v3", spec)
    assert _pinned_commit(repo) == spec["official_commit"]
    assert resolved["categories"] == sorted(ids)
    assert len(paths) == 17
    assert all(path.name.startswith("BFCL_v3_") and path.is_file() for path in paths)
    assert sum(len(values) for values in ids.values()) == 4441
    assert all(len(values) == len(set(values)) for values in ids.values())


def test_real_frozen_release_has_complete_counts_and_partial_scope(tmp_path):
    config = read(CONFIG)
    data = Path(config["frozen_data_dir"])
    receipt = read(data / "READY.json")
    assert receipt["config_hash"] == fingerprint(read(data / "protocol.json"))
    assert receipt["training_tasks"] == 3000
    assert receipt["validation_tasks"] == 744
    assert receipt["evaluation_scope"] == {
        "status": "partial_ace_pending_audit",
        "skip_acebench": True,
        "frozen_validation_tasks": 744,
        "scored_validation_tasks": 694,
        "pending_validation_tasks": 50,
    }
    store = RoundStore(data / "tasks.sqlite")
    seen, content = set(), set()
    for number in range(1, 6):
        store.select_round(number)
        rows = store.current["tasks"]
        assert len(rows) == 600
        assert Counter(row["source"] for row in rows) == config["train_quotas_per_round"]
        assert Counter(row["strata"]["interface"] for row in rows if row["domain"] == "code") == {"stdio": 120, "function": 80}
        assert not seen.intersection(row["task_id"] for row in rows)
        assert not content.intersection(row["content_hash"] for row in rows)
        seen.update(row["task_id"] for row in rows)
        content.update(row["content_hash"] for row in rows)
        assert all(store._task(row).task_id == row["task_id"] for row in rows)
    training = read(config["source_training_manifest"])
    validation = read(config["validation_manifest"])
    assert seen == {row["task_id"] for row in training["tasks"]}
    assert_disjoint([training, validation])
    assert len(validation["tasks"]) == 744
    assert not validation["clean_independence_audited"]
    assert {row["exposure"] for row in validation["tasks"]} == {"exposure_unknown"}
    settings = read(ROOT / config["validation_config"])
    selected, specs = select_specs(settings, config["validation_manifest"], "independent_validation", tmp_path / "inputs")
    assert len(selected["tasks"]) == 744
    assert set(specs) == {"humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"}
    assert sum(row["source"] != "acebench" for row in selected["tasks"]) == 694


def test_tool_validation_inputs_match_allocated_frozen_packs():
    settings = read(ROOT / "reproduction/configs/validation_744.json")
    config = read(CONFIG)
    frozen = Path(config["validation_manifest"]).parent
    allocated = Path(config["source_validation_manifest"]).parent / "allocated/validation"
    for name, expected in (("bfcl_v3", 152), ("acebench", 50)):
        source = Path(settings["tool_benchmarks"][name]["data_path"])
        assert source.is_file(), f"Missing configured validation input: {source}"
        assert source == allocated / f"{name}.jsonl"
        data = source.read_bytes()
        assert sum(bool(line.strip()) for line in data.splitlines()) == expected
        assert data == (frozen / f"{name}.jsonl").read_bytes()
