"""Offline configuration contracts for the repeated DeepCoder five-round canary."""
import copy
import importlib
import json
import sqlite3
from pathlib import Path

import pytest

from test_deepcoder_canary import (
    ROOT, _canary_launcher_value, canary_config, release_world,
)
from evolution_protocol import file_hash, freeze, make_manifest
from rsiflow.task_meta.pipeline import PipelineConfig, active_domains
from rsiflow.task_meta.round_evolution import RoundStore
from task_adapter import TaskAdapter


def _five_round_launcher_value(tmp_path):
    value = _canary_launcher_value(tmp_path)
    value.update(experiment_scope="deepcoder_five_round_canary",
                 training_schedule="round_repeated", rounds=5,
                 allocated_tasks=80, unique_tasks=16,
                 retry_rejected_from_round=6, evaluation_schedule="none")
    return value


def test_five_round_adapter_preserves_repeated_schedule(tmp_path):
    value = _five_round_launcher_value(tmp_path)
    path = tmp_path / "launcher.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    config = TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(path)})
    assert config.experiment_scope == "deepcoder_five_round_canary"
    assert config.training_schedule == "round_repeated"
    assert config.max_generations == 5
    assert config.window_quotas == {"tool_use": 0, "code": 16, "searchqa": 0}
    assert config.round_protocol["training_schedule"] == "round_repeated"
    assert config.round_protocol["unique_tasks"] == 16
    assert active_domains(config) == ("code",)


@pytest.mark.parametrize("override", [
    {"rounds": 4}, {"allocated_tasks": 16}, {"unique_tasks": 15},
    {"training_tasks_per_pass": 15},
    {"train_quotas_per_round": {"envscaler": 1, "deepcoder_taco": 16,
                                "hotpotqa": 0, "2wiki": 0}},
])
def test_five_round_rejects_inconsistent_scope(tmp_path, override):
    value = _five_round_launcher_value(tmp_path)
    value.update(override)
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(path)})


def test_repeated_schedule_is_rejected_outside_five_round_scope(canary_config):
    canary_config.training_schedule = "round_repeated"
    canary_config.search_dev_fraction = 0
    with pytest.raises(ValueError, match="round_repeated requires deepcoder_five_round_canary"):
        PipelineConfig.model_validate(canary_config.model_dump()).checked()


def _five_round_pipeline_value(canary_config):
    canary_config.experiment_scope = "deepcoder_five_round_canary"
    canary_config.training_schedule = "round_repeated"
    canary_config.max_generations = 5
    canary_config.window_quotas = {"tool_use": 0, "code": 16, "searchqa": 0}
    canary_config.probe_per_domain = dict(canary_config.window_quotas)
    canary_config.round_protocol = {
        "training_schedule": "round_repeated", "unique_tasks": 16,
    }
    return canary_config.model_dump()


INVALID_FIVE_ROUND_RUNTIME_CONFIGS = [
    ({"training_schedule": "round_disjoint"},
     "deepcoder_five_round_canary requires round_repeated"),
    ({"max_generations": 4},
     "DeepCoder five-round canary requires five rounds and 80 task positions"),
    ({"round_protocol": None},
     "DeepCoder five-round canary requires round_protocol unique_tasks=16"),
    ({"round_protocol": {"training_schedule": "round_repeated"}},
     "DeepCoder five-round canary requires round_protocol unique_tasks=16"),
    ({"round_protocol": {"training_schedule": "round_repeated", "unique_tasks": 15}},
     "DeepCoder five-round canary requires round_protocol unique_tasks=16"),
    ({"round_protocol": {"unique_tasks": 16}},
     "DeepCoder five-round canary requires round_protocol training_schedule to match the top-level schedule"),
    ({"round_protocol": {"training_schedule": "round_disjoint", "unique_tasks": 16}},
     "DeepCoder five-round canary requires round_protocol training_schedule to match the top-level schedule"),
]


@pytest.mark.parametrize("override, message", INVALID_FIVE_ROUND_RUNTIME_CONFIGS)
def test_five_round_pipeline_rejects_invalid_runtime_identity(canary_config, override, message):
    value = _five_round_pipeline_value(canary_config)
    value.update(override)
    with pytest.raises(ValueError, match=message):
        PipelineConfig.model_validate(value).checked()


@pytest.mark.parametrize("override, message", INVALID_FIVE_ROUND_RUNTIME_CONFIGS)
def test_schema_v1_adapter_rejects_invalid_runtime_identity(
        canary_config, tmp_path, override, message):
    value = _five_round_pipeline_value(canary_config)
    value.update(override)
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(path)})


@pytest.fixture
def five_round():
    return importlib.import_module("prepare_deepcoder_five_round")


def _alternate_parent(release_world, tmp_path, tasks, name):
    value = make_manifest("train_evolution", 1, tasks, split_seed=42)
    path = tmp_path / f"{name}.json"
    freeze(path, value)
    return path


def _record_bytes(task):
    with Path(task["record_file"]).open("rb") as stream:
        stream.seek(task["record_offset"])
        return stream.read(task["record_length"])


def test_release_repeats_sixteen_tasks_with_one_sqlite_index(
        release_world, tmp_path, five_round):
    source_paths = {task["record_file"] for task in release_world["parent"]["tasks"]}
    source_paths.update({
        str(release_world["retrieval"]),
        str(release_world["retrieval"].with_suffix(".sqlite.manifest.json")),
    })
    hashes_before = {path: file_hash(Path(path)) for path in sorted(source_paths)}
    output = tmp_path / "logs/five"

    receipt = five_round.build_five_round_release(
        output, release_world["launcher_path"], release_world["parent_path"])

    assert receipt["rounds"] == 5
    assert receipt["unique_tasks"] == 16
    assert receipt["allocated_task_slots"] == 80
    assert receipt["schedule"] == "round_repeated"
    release = Path(receipt["release_dir"])
    source_tasks = {task["task_id"]: task for task in release_world["parent"]["tasks"]}
    ids_by_round = []
    manifest_hashes = {}
    for number in range(1, 6):
        current = json.loads((release / f"B{number}/manifest.json").read_text())
        assert current["round_id"] == number
        assert len(current["tasks"]) == 16
        ids_by_round.append([row["task_id"] for row in current["tasks"]])
        assert (release / f"B{number}/deepcoder_taco.jsonl").is_file()
        manifest_hashes[str(number)] = current["manifest_hash"]
        for row in current["tasks"]:
            assert row["round_id"] == number
            payload = Path(row["record_file"]).resolve()
            assert payload.parent == (release / f"B{number}").resolve()
            assert _record_bytes(row) == _record_bytes(source_tasks[row["task_id"]])
    assert ids_by_round == [ids_by_round[0]] * 5
    assert len(set(manifest_hashes.values())) == 5

    with sqlite3.connect(release / "tasks.sqlite") as connection:
        indexed = connection.execute("SELECT task_id, path FROM tasks").fetchall()
    assert len(indexed) == 16
    assert {row[0] for row in indexed} == set(ids_by_round[0])
    assert all(Path(row[1]).resolve().parent == (release / "B1").resolve()
               for row in indexed)

    store = RoundStore(release / "tasks.sqlite")
    for number in (1, 5):
        store.select_round(number)
        tasks, _ = store.window(None, {"code": 16})
        assert len(tasks) == 16

    ready = json.loads((release.parent / "READY.json").read_text())
    assert ready["round_manifest_hashes"] == manifest_hashes
    assert ready["status"] == "ready"
    assert ready["schedule"] == "round_repeated"
    assert ready["task_count"] == ready["unique_tasks"] == 16
    assert ready["allocated_task_slots"] == 80
    assert ready["source_assets_unchanged"] is True
    assert ready["formal_round_completed"] is False
    assert ready["model_calls"] == ready["api_calls"] == ready["gpu_calls"] == 0
    protocol = json.loads((release / "protocol.json").read_text())
    assert protocol["schedule"] == "round_repeated"
    assert protocol["rounds"] == 5
    assert protocol["tasks_per_round"] == 16
    assert protocol["unique_tasks"] == 16
    assert protocol["allocated_task_slots"] == 80
    assert {path: file_hash(Path(path)) for path in sorted(source_paths)} == hashes_before


def test_release_rejects_changed_source_record(release_world, tmp_path, five_round):
    task = next(task for task in release_world["parent"]["tasks"]
                if task["source"] == "deepcoder_taco"
                and task["strata"]["interface"] == "function")
    with Path(task["record_file"]).open("ab") as stream:
        stream.write(b"changed")
    changed_hash = file_hash(task["record_file"])
    output = tmp_path / "logs/changed"

    with pytest.raises(ValueError, match="Frozen source changed"):
        five_round.build_five_round_release(
            output, release_world["launcher_path"], release_world["parent_path"])

    assert file_hash(task["record_file"]) == changed_hash
    assert not (output / "READY.json").exists()


def test_release_refuses_existing_output_directory(release_world, tmp_path, five_round):
    output = tmp_path / "logs/existing"
    output.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="must not exist"):
        five_round.build_five_round_release(
            output, release_world["launcher_path"], release_world["parent_path"])


def test_release_rejects_non_function_records(release_world, tmp_path, five_round):
    tasks = copy.deepcopy(release_world["parent"]["tasks"])
    for task in tasks:
        if task["source"] == "deepcoder_taco":
            task["strata"]["interface"] = "stdio"
    parent = _alternate_parent(release_world, tmp_path, tasks, "non-function")
    output = tmp_path / "logs/non-function"

    with pytest.raises(ValueError, match="fewer than sixteen"):
        five_round.build_five_round_release(
            output, release_world["launcher_path"], parent)
    assert not output.exists()


def test_release_rejects_fewer_than_sixteen_records(release_world, tmp_path, five_round):
    tasks = [task for task in release_world["parent"]["tasks"]
             if task["source"] == "deepcoder_taco"
             and task["strata"]["interface"] == "function"][:15]
    parent = _alternate_parent(release_world, tmp_path, tasks, "fifteen")
    output = tmp_path / "logs/fifteen"

    with pytest.raises(ValueError, match="fewer than sixteen"):
        five_round.build_five_round_release(
            output, release_world["launcher_path"], parent)
    assert not output.exists()


def test_five_round_machine_config_preserves_single_round_assets(tmp_path):
    project = ROOT.parent
    original_path = project / "reproduction/configs/train_4b_deepcoder_canary_workspace.json"
    config_path = project / "reproduction/configs/train_4b_deepcoder_five_round_workspace.json"
    original = json.loads(original_path.read_text())
    value = json.loads(config_path.read_text())
    workspace = "/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round"
    expected = {
        "experiment_scope": "deepcoder_five_round_canary",
        "training_schedule": "round_repeated",
        "rounds": 5,
        "allocated_tasks": 80,
        "unique_tasks": 16,
        "output_root": f"{workspace}/Rollout_logs/deepcoder_five_round_16_v1",
        "run_name": "deepcoder_function_16_five_round_workspace_v1",
        "frozen_data_dir": (
            f"{workspace}/reproduction/logs/deepcoder-five-round-16-v1/release"
        ),
        "data_release": "deepcoder_function_16_five_round_v1",
        "retry_rejected_from_round": 6,
    }

    assert value == {**original, **expected}
    assert value["meta_skill_seed_path"] is None
    assert value["training_tasks_per_pass"] == 16
    assert value["ports"] == original["ports"]

    adapter = TaskAdapter(ROOT, tmp_path / "receipts")
    effective = adapter._config({"config_path": str(config_path)})
    original_effective = adapter._config({"config_path": str(original_path)})
    assert effective.max_generations == 5
    assert effective.training_schedule == "round_repeated"
    assert effective.round_protocol["unique_tasks"] == 16
    assert effective.task_checkpoint == original_effective.task_checkpoint
    assert effective.meta == original_effective.meta
    assert effective.training == original_effective.training
    assert effective.task_replicas == original_effective.task_replicas
    assert effective.seed == original_effective.seed == 42

    output = Path(value["output_root"])
    run = output / "runs" / value["run_name"]
    frozen = Path(value["frozen_data_dir"])
    for path in (output, run, frozen):
        assert path.is_relative_to(project.resolve())
        assert not path.is_relative_to((project.parent / "preflight").resolve())
