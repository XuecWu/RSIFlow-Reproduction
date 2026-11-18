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
from experiment_progress import validate_workflow_policy
from launch_meta import experiment_prompt
from persistent_meta import (
    PersistentMeta, mainline_reminder, persistent_instructions,
)
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


def _five_round_workflow_policy():
    return {
        "rounds": 5,
        "experiment_scope": "deepcoder_five_round_canary",
        "evaluation_schedule": "none",
        "retry_rejected_from_round": 6,
    }


def _write_five_round_policy(run):
    session = run / "meta_session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "workflow_policy.json").write_text(
        json.dumps(_five_round_workflow_policy()), encoding="utf-8")
    return session


def _assert_five_round_instructions(text):
    lowered = text.lower()
    for required in (
        "one attempt per round",
        "retain completes the current round and advances",
        "b1-b5",
        "no independent validation",
    ):
        assert required in lowered
    for forbidden in (
        "sole attempt",
        "no next round",
        "fresh round-disjoint",
        "fresh, round-disjoint",
        "fresh next-round",
        "next round's fresh",
        "no fixed retry",
        "rejection returns to component selection",
        "rejection retains the parent and returns",
        "return to step 3 on this same round/batch",
        "meta chooses again after rejection",
        "48 representative",
        "round-disjoint",
    ):
        assert forbidden not in lowered


def test_five_round_initial_prompt_and_persistent_instruction_layers_agree(tmp_path):
    config = _five_round_launcher_value(tmp_path)
    run = tmp_path / "run"
    _write_five_round_policy(run)

    prompt = experiment_prompt(tmp_path / "launcher.json", config, run)
    texts = {
        "initial prompt": prompt,
        "persistent instructions": persistent_instructions(_five_round_workflow_policy()),
        "mainline reminder": mainline_reminder(_five_round_workflow_policy()),
    }
    for text in texts.values():
        _assert_five_round_instructions(text)
    assert "repeated 16-task batch" in prompt
    assert "finish_experiment only after B5" in prompt
    payload = prompt.split("Experiment facts and required sequence:\n", 1)[1]
    protocol = json.loads(payload.split("\nMainline stages:\n", 1)[0])
    assert protocol["workflow_policy"] == _five_round_workflow_policy()
    assert protocol["training_schedule"] == "round_repeated"


@pytest.mark.parametrize("override", [
    {"rounds": 1},
    {"rounds": 4},
    {"retry_rejected_from_round": 2},
    {"retry_rejected_from_round": 7},
    {"experiment_scope": "deepcoder_canary"},
    {"experiment_scope": "multidomain"},
])
def test_five_round_none_policy_rejects_wrong_scope_or_shape(override):
    policy = {**_five_round_workflow_policy(), **override}
    with pytest.raises(ValueError, match="evaluation_schedule=none"):
        validate_workflow_policy(policy)


class _FiveRoundOfflineExecutor:
    def execute(self, request):
        operation = request["operation"]
        receipt = {"operation": operation, "status": {
            "compare_scores": "compared",
            "snapshot_task_meta": "snapshotted",
            "write_text": "written",
        }.get(operation, "completed")}
        if operation == "sft":
            receipt["candidate_state_path"] = "candidate.json"
        if operation == "append_skills":
            receipt["appended_count"] = 1
        return receipt


def _five_round_persistent_meta(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    run = tmp_path / "run"
    _write_five_round_policy(run)
    loop = PersistentMeta(
        project=project, run=run, codex_home=run / "meta_session/codex_home",
        executor=_FiveRoundOfflineExecutor(),
        meta_config={"model": "offline-model", "provider": "offline-provider"}, rounds=5,
    )

    def complete_job(request, turn_dir):
        return {"status": "completed", "result": loop.tools.executor.execute(request)}

    loop.tools.jobs.start = complete_job
    return loop


def _call_five_round_tool(loop, operation, round_number, **extra):
    return loop.tools("experiment", {"request": {
        "operation": operation, "round_number": round_number, **extra,
    }})


def _complete_five_round_tools_a0(loop):
    _call_five_round_tool(loop, "bootstrap", 0)
    _call_five_round_tool(loop, "snapshot_task_meta", 0)


def _record_five_round_tools(loop, number, choice="accept_candidate", *, skip=0, steps=10):
    operations = [
        ("run_parent", {}),
        ("prepare_meta_evidence", {}),
        ("write_text", {
            "path": str(loop.run / f"round_{number}/attempts/attempt_1/decision.json"),
        }),
        ("sft", {}),
        ("run_candidate", {}),
        ("compare_scores", {}),
        ("write_text", {
            "path": str(loop.run / f"round_{number}/attempts/attempt_1/selection.json"),
            "content": json.dumps({"accept_or_retain": choice}),
        }),
        ("append_skills", {"path": str(loop.run / "meta/skills.jsonl")}),
        ("write_text", {"path": str(loop.run / "meta/context.json")}),
        ("snapshot_task_meta", {}),
    ]
    responses = []
    for operation, extra in operations[skip:skip + steps]:
        responses.append(_call_five_round_tool(loop, operation, number, **extra))
    return responses


def test_five_round_finish_tool_rejects_b4_and_incomplete_b5_then_accepts_b5(tmp_path):
    loop = _five_round_persistent_meta(tmp_path)
    _complete_five_round_tools_a0(loop)
    for number in range(1, 5):
        _record_five_round_tools(loop, number)

    premature = loop.tools("finish_experiment", {"summary": "before B5"})
    assert '"status":"incomplete"' in premature
    assert loop.tools.finished is False
    assert loop.tools.summary is None

    _record_five_round_tools(loop, 5, steps=6)
    missing_selection = loop.tools("finish_experiment", {"summary": "no B5 selection"})
    assert '"status":"incomplete"' in missing_selection
    assert loop.progress.rebuild()["next_milestone"] == "selection_recorded"
    assert loop.tools.finished is False

    _record_five_round_tools(loop, 5, skip=6)
    complete = loop.tools("finish_experiment", {"summary": "five rounds complete"})
    assert '"status":"complete"' in complete
    assert loop.tools.finished is True
    assert loop.tools.summary == "five rounds complete"


def test_real_five_round_resume_and_retain_receipts_keep_instruction_semantics(
        tmp_path, monkeypatch):
    import persistent_meta

    loop = _five_round_persistent_meta(tmp_path)
    _complete_five_round_tools_a0(loop)
    _record_five_round_tools(loop, 1)
    retain_responses = _record_five_round_tools(loop, 2, "retain_parent")
    assert loop.progress.rebuild()["current_stage"] == "B3"
    _assert_five_round_instructions(retain_responses[6])
    _assert_five_round_instructions(retain_responses[-1])
    turn_count = len(list((loop.run / "meta_session/turns").iterdir()))
    (loop.run / "meta_session/state.json").write_text(json.dumps({
        "transport": "app_server", "status": "disconnected",
        "thread_id": "thread-five-round", "sandbox_backend": "legacy_host",
        "capability_revision": "native_coding_v1",
    }), encoding="utf-8")
    config = _five_round_launcher_value(tmp_path)
    initial = experiment_prompt(tmp_path / "launcher.json", config, loop.run)
    captured = {"requests": [], "turns": []}

    class Server:
        def __init__(self, **kwargs):
            self.process = type("Process", (), {"pid": 12345})()

        def start(self):
            return self

        def request(self, method, params):
            captured["requests"].append((method, params))
            return {"thread": {"id": params["threadId"]}}

        def turn(self, thread_id, prompt, environments):
            captured["turns"].append(prompt)
            if len(captured["turns"]) == 2:
                loop.tools.finished = True
                loop.tools.summary = "offline complete"
            return {"id": f"turn-{len(captured['turns'])}",
                    "status": "failed" if len(captured["turns"]) == 1 else "completed"}

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta, "CodexAppServer", Server)
    monkeypatch.setattr(persistent_meta.time, "sleep", lambda seconds: None)
    result = loop.run_experiment(initial)

    assert result["status"] == "complete"
    assert captured["requests"][0][0] == "thread/resume"
    assert captured["requests"][0][1]["threadId"] == "thread-five-round"
    developer = captured["requests"][0][1]["developerInstructions"]
    assert len(captured["turns"]) == 2
    for text in (developer, *captured["turns"]):
        _assert_five_round_instructions(text)
    assert all("Current stage: B3" in text for text in captured["turns"])
    assert len(list((loop.run / "meta_session/turns").iterdir())) == turn_count
    commands = [json.loads(path.read_text())
                for path in (loop.run / "meta_session/turns").glob("*/command.json")]
    operations = [command["request"]["operation"] for command in commands]
    assert operations.count("bootstrap") == 1
    assert operations.count("run_parent") == 2


def test_five_round_b5_compact_requests_final_handoff_on_repeated_batch(tmp_path):
    loop = _five_round_persistent_meta(tmp_path)
    _complete_five_round_tools_a0(loop)
    for number in range(1, 5):
        _record_five_round_tools(loop, number)
    _record_five_round_tools(loop, 5, steps=8)

    compact = loop.progress.compact_status_block()
    assert "Current stage: B5" in compact
    assert "Next required milestone: context_updated" in compact
    assert "final handoff" in compact
    assert "handoff for the next round" not in compact
    assert "repeated 16-task batch" in compact
    assert "no independent validation" in compact
    assert "round-disjoint" not in compact
    assert "48 representative" not in compact


@pytest.fixture
def five_round_preflight():
    return importlib.import_module("deepcoder_five_round_preflight")


def _preflight_world(release_world, tmp_path, five_round):
    built = five_round.build_five_round_release(
        tmp_path / "logs/release-build", release_world["launcher_path"],
        release_world["parent_path"])
    value = _five_round_launcher_value(tmp_path)
    value.update(frozen_data_dir=built["release_dir"],
                 output_root=str(tmp_path / "logs/formal-output"),
                 run_name="offline-preflight-only")
    config_path = tmp_path / "five-round-machine.json"
    freeze(config_path, value)
    return config_path, Path(built["release_dir"]), built


def test_five_round_preflight_drives_real_progress_and_is_repeatable(
        release_world, tmp_path, five_round, five_round_preflight):
    config, release, _ = _preflight_world(release_world, tmp_path, five_round)
    output = tmp_path / "logs/preflight/receipt.json"

    receipt = five_round_preflight.run_preflight(config, release, output)

    assert receipt["status"] == "passed"
    assert receipt["rounds"] == 5
    assert receipt["real_evolution_started"] is False
    assert receipt["model_calls"] == receipt["api_calls"] == receipt["gpu_calls"] == 0
    assert [item["selection"] for item in receipt["round_checks"]] == [
        "accept_candidate", "retain_parent", "accept_candidate",
        "accept_candidate", "accept_candidate",
    ]
    assert [item["current_stage_after"] for item in receipt["round_checks"]] == [
        "B2", "B3", "B4", "B5", "COMPLETE",
    ]
    assert [item["finish_allowed"] for item in receipt["round_checks"]] == [
        False, False, False, False, True,
    ]
    assert receipt["round_checks"][1]["retain_advanced"] is True
    assert all(item["attempt"] == 1 for item in receipt["round_checks"])
    assert receipt["independent_validation"] == "none"
    assert receipt["validation_milestones"] == []
    assert receipt["same_thread_resume"]["instruction_verified"] is True
    assert receipt["same_thread_resume"]["provider_calls"] == 0
    assert receipt["instruction_layers"] == [
        "initial_prompt", "persistent_instructions", "mainline_reminder",
    ]
    assert len(receipt["reviewed_commit"]) == 40
    assert receipt["config_sha256"] == file_hash(config)
    assert receipt["release_files_sha256"]
    assert not (tmp_path / "logs/formal-output").exists()

    before = output.read_bytes()
    repeated = five_round_preflight.run_preflight(config, release, output)
    assert repeated == receipt
    assert output.read_bytes() == before
    assert five_round_preflight.main([
        "--config", str(config), "--release", str(release), "--output", str(output),
    ]) == 0


def test_five_round_preflight_rejects_tampered_release_without_formal_output(
        release_world, tmp_path, five_round, five_round_preflight):
    config, release, _ = _preflight_world(release_world, tmp_path, five_round)
    manifest = release / "B3/manifest.json"
    value = json.loads(manifest.read_text())
    value["tasks"][0]["content_hash"] = "0" * 64
    manifest.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises((ValueError, RuntimeError), match="hash|manifest|changed"):
        five_round_preflight.run_preflight(
            config, release, tmp_path / "logs/preflight/tampered.json")
    assert not (tmp_path / "logs/formal-output").exists()


def test_five_round_preflight_rejects_output_inside_frozen_release(
        release_world, tmp_path, five_round, five_round_preflight):
    config, release, _ = _preflight_world(release_world, tmp_path, five_round)
    before = five_round_preflight._release_hashes(release)
    output = release / "preflight-receipt.json"

    with pytest.raises((ValueError, RuntimeError), match="release|read-only|output"):
        five_round_preflight.run_preflight(config, release, output)
    assert not output.exists()
    assert five_round_preflight._release_hashes(release) == before


# ---------------------------------------------------------------------------
# Task 6 (offline): read-only five-round result summarizer contracts.
# ---------------------------------------------------------------------------

import hashlib

SUMMARIZER_PATH = ROOT.parent / "reproduction/scripts/summarize_deepcoder_five_round.py"


def _load_summarizer():
    import importlib.util
    spec = importlib.util.spec_from_file_location("summarize_deepcoder_five_round_test", SUMMARIZER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK_IDS = [f"deepcoder_taco:deepcoder-taco-{i}" for i in range(1000, 1016)]
MANIFEST_HASH = "m" * 64


def _source_hash(task_id):
    return hashlib.sha256(("source:" + task_id).encode()).hexdigest()


def _trajectory(task_id, *, success, calls, branch, round_n, state_hash,
                error=None, execution_error_type="candidate_error",
                infrastructure_error=False):
    valid_answer = bool(calls) and success or (bool(calls) and not success)
    # zero-call rows have no valid answer and must not count as full tests
    valid_answer = bool(calls)
    full_verifier = bool(calls)
    verification = {
        "status": "completed",
        "verifier_id": "deepcoder_taco_all_tests_stdio_or_function_v2_tol1e-6",
        "success": bool(success),
        "full_verifier": full_verifier,
        "tests_completed": 11 if calls else 0,
        "tests_total": 11,
    }
    trajectory_id = hashlib.sha256(f"{task_id}:{branch}:{round_n}".encode()).hexdigest()
    return {
        "task_id": task_id, "question_id": task_id, "source": "deepcoder_taco",
        "domain": "code", "split": "evolve_train", "rollout_id": 0,
        "state_hash": state_hash, "manifest_hash": MANIFEST_HASH,
        "task_source_hash": _source_hash(task_id),
        "infrastructure_error": infrastructure_error, "infrastructure_failure": infrastructure_error,
        "verification": verification, "valid_answer": valid_answer,
        "model_call_count": calls,
        "transport_calls": [{"call_id": i, "status": "completed"} for i in range(calls)],
        "execution_error_type": execution_error_type,
        "error_type": "parse_failure" if not calls else ("candidate_error" if not success else None),
        "error": error, "terminal_reward": 1.0 if success else 0.0,
        "branch_id": branch, "round_id": round_n, "collection_stage": "parent_pre_update",
        "trajectory_id": trajectory_id,
    }


def _performance(rows, round_n):
    successes = sum(1 for r in rows if r["verification"]["success"])
    n = len(rows)
    rate = successes / n if n else None
    errors = {}
    for r in rows:
        if not r["verification"]["success"]:
            key = r.get("execution_error_type") or "unknown"
            errors[key] = errors.get(key, 0) + 1
    code = {"correct": successes, "denominator": n, "success_rate": rate,
            "mean_native_reward": rate, "errors": errors}
    return {
        "domains": {"tool_use": {"correct": 0, "denominator": 0, "success_rate": None, "errors": {}},
                    "code": code,
                    "searchqa": {"correct": 0, "denominator": 0, "success_rate": None, "errors": {}}},
        "macro_success": rate, "domain_weights": {"code": 1.0}, "status": "complete",
        "total_rollouts": n, "units": "fraction_0_1", "round_id": round_n,
        "training_manifest": MANIFEST_HASH, "evaluation_protocol": "same_round_full_B_parent_child_v2",
        "feedback_role": "train_evolution",
        "benchmarks": {"deepcoder_taco": {"count": n, "successes": successes, "errors": errors}},
    }


def _write_side(round_dir, side, rows, state_hash):
    side_dir = round_dir / side if side == "parent" else round_dir / "attempts/attempt_1/candidate"
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (side_dir / "performance.json").write_text(json.dumps(_performance(rows, rows[0]["round_id"])), encoding="utf-8")
    cache = side_dir.parent / "rollout_cache" / state_hash / "train_rollouts"
    cache.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (cache / (r["trajectory_id"] + ".json")).write_text(
            json.dumps({"binding": {"task": r["task_id"], "state_hash": state_hash, "rollout": 0},
                        "row": {"model_call_count": r["model_call_count"]}}),
            encoding="utf-8")
        if r["model_call_count"]:
            calls_dir = cache / (r["trajectory_id"] + ".calls")
            calls_dir.mkdir(parents=True, exist_ok=True)
            for i in range(r["model_call_count"]):
                (calls_dir / f"{i:04d}.json").write_text(
                    json.dumps({"call_id": i, "status": "completed", "binding": {"state_hash": state_hash}}),
                    encoding="utf-8")


def _write_round(run, n, *, parent_success, candidate_success, zero_call,
                 component="HARNESS", decision="accept_candidate", errors=None,
                 infrastructure_error_ids=()):
    errors = errors or {}
    round_dir = run / f"round_{n}"
    parent_state = hashlib.sha256(f"parent-state-{n}".encode()).hexdigest()
    candidate_state = hashlib.sha256(f"candidate-state-{n}".encode()).hexdigest()

    def make_rows(success_set, branch, state_hash):
        rows = []
        for tid in TASK_IDS:
            calls = 0 if tid in zero_call else 7
            rows.append(_trajectory(
                tid, success=tid in success_set, calls=calls, branch=branch, round_n=n,
                state_hash=state_hash, error=errors.get(tid),
                infrastructure_error=tid in infrastructure_error_ids))
        return rows

    parent_rows = make_rows(parent_success, "parent", parent_state)
    candidate_rows = make_rows(candidate_success, "candidate", candidate_state)
    _write_side(round_dir, "parent", parent_rows, parent_state)
    attempt = round_dir / "attempts/attempt_1"
    _write_side(round_dir, "candidate", candidate_rows, candidate_state)
    before = len(parent_success) / 16
    after = len(candidate_success) / 16
    new_success = len(candidate_success - parent_success)
    new_regression = len(parent_success - candidate_success)
    (attempt / "decision.json").write_text(json.dumps({
        "round": n, "attempt": 1, "component": component, "relevant_skill_ids": [],
        "rationale": "fixture", "prediction": {},
    }), encoding="utf-8")
    (attempt / "selection.json").write_text(json.dumps({
        "round": n, "attempt": 1, "decision": decision, "reason": "fixture",
        "before": before, "after": after, "delta": after - before,
        "paired_tasks": 16, "new_success": new_success, "new_regression": new_regression,
    }), encoding="utf-8")
    (attempt / "skill_use.json").write_text(json.dumps({"used_skill_ids": []}), encoding="utf-8")
    diff_dir = attempt / "task_differences"
    diff_dir.mkdir(parents=True, exist_ok=True)
    (diff_dir / "summary.json").write_text(json.dumps({
        "status": "compared", "paired_tasks": 16, "same_id_set": True,
        "counts": {"retained_failure": 16 - len(parent_success | candidate_success),
                   "new_success": new_success, "new_regression": new_regression},
    }), encoding="utf-8")
    snap = run / "snapshots" / f"round_{n}_attempt_1"
    (snap / "task").mkdir(parents=True, exist_ok=True)
    (snap / "snapshot_receipt.json").write_text(json.dumps({"status": "snapshotted"}), encoding="utf-8")
    (snap / "task/task_state_snapshot.json").write_text(json.dumps({"generation": n}), encoding="utf-8")
    return {"parent_success": parent_success, "candidate_success": candidate_success,
            "new_success": new_success, "new_regression": new_regression}


def _build_two_round_run(tmp_path, *, complete=False):
    run = tmp_path / "run"
    run.mkdir()
    # B1: accept, positive gain; new success/regression overlap zero-call process anomalies.
    # new_success TASK_IDS[3] and new_regression TASK_IDS[0] are zero-call to force overlap.
    b1 = _write_round(
        run, 1,
        parent_success=set(TASK_IDS[:3]),
        candidate_success=set(TASK_IDS[3:7]),
        zero_call=set(TASK_IDS[7:16]) | {TASK_IDS[3], TASK_IDS[0]},
        errors={TASK_IDS[7]: "FileNotFoundError: [Errno 2] No such file or directory",
                TASK_IDS[8]: "BlockingIOError: [Errno 11] Resource temporarily unavailable (EAGAIN)",
                TASK_IDS[3]: "FileNotFoundError: [Errno 2] No such file or directory"},
        component="HARNESS", decision="accept_candidate")
    # B2: retain, zero delta
    b2 = _write_round(
        run, 2,
        parent_success=set(TASK_IDS[3:7]),
        candidate_success=set(TASK_IDS[3:7]),
        zero_call=set(TASK_IDS[7:16]),
        component="HARNESS", decision="retain_parent")
    (run / "active_task.json").write_text(json.dumps({
        "generation": 1, "model_ref": "/models/Qwen3-4B",
        "harness_path": str(run / "round_1/attempts/attempt_1/build/candidate_bundle.json"),
        "checkpoint_path": "/models/Qwen3-4B", "artifacts": {"directory": None, "manifest": []},
    }), encoding="utf-8")
    (run / "snapshots/A0/task").mkdir(parents=True, exist_ok=True)
    (run / "snapshots/A0/snapshot_receipt.json").write_text(json.dumps({"status": "snapshotted"}), encoding="utf-8")
    meta = run / "meta"
    meta.mkdir(exist_ok=True)
    (meta / "skills.jsonl").write_text(json.dumps({"id": "case.HARNESS.x", "type": "case"}) + "\n", encoding="utf-8")
    (meta / "context.json").write_text(json.dumps({"round": 2, "attempt": 1, "component": "HARNESS", "selection": "retained"}), encoding="utf-8")
    session = run / "meta_session"
    session.mkdir(exist_ok=True)
    (session / "state.json").write_text(json.dumps({"status": "complete" if complete else "running", "transport": "app_server"}), encoding="utf-8")
    (session / "RUN_STATUS.md").write_text(
        "Current stage: **COMPLETE**\nFinish allowed: **yes**\n" if complete else
        "Current stage: **B3**\nFinish allowed: **no**\n", encoding="utf-8")
    jobs = session / "jobs"
    jobs.mkdir(exist_ok=True)
    job = jobs / "job1"
    job.mkdir()
    result = run / "controller_transport/job1/result.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    if complete:
        result.write_text(json.dumps({"status": "completed", "returncode": 0}), encoding="utf-8")
    (job / "job.json").write_text(json.dumps({"job_id": "job1", "result_path": str(result)}), encoding="utf-8")
    if complete:
        (session / "services_stopped.json").write_text(json.dumps({"status": "stopped", "gpus": [0, 1, 2, 3]}), encoding="utf-8")
        # add B3-B5 to make a complete five-round run
        for n in (3, 4, 5):
            _write_round(run, n, parent_success=set(TASK_IDS[3:7]), candidate_success=set(TASK_IDS[3:7]),
                         zero_call=set(TASK_IDS[7:16]), component="HARNESS",
                         decision="accept_candidate" if n == 5 else "retain_parent")
    return run, b1, b2


def test_summarizer_two_rounds_denominator_calls_and_transitions(tmp_path):
    summarize = _load_summarizer().summarize
    run, b1, b2 = _build_two_round_run(tmp_path)
    result = summarize(run)
    assert result["completed_rounds"] == 2
    assert result["run_complete"] is False
    rounds = {r["round"]: r for r in result["rounds"]}
    assert set(rounds) == {1, 2}
    for n, spec in ((1, b1), (2, b2)):
        r = rounds[n]
        assert r["parent"]["denominator"] == 16
        assert r["candidate"]["denominator"] == 16
        assert r["denominator_match"] is True
        assert r["ids_match"] is True
        assert r["source_hashes_match"] is True
        # calls cross-recomputed from trajectories + transport + persisted receipts
        assert r["calls"]["parent"]["trajectory_calls"] == r["calls"]["parent"]["persisted_receipts"]
        assert r["calls"]["parent"]["transport_calls"] == r["calls"]["parent"]["trajectory_calls"]
        assert r["calls"]["cross_check_ok"] is True
        assert r["transitions"]["new_success"] == spec["new_success"]
        assert r["transitions"]["new_regression"] == spec["new_regression"]
    assert rounds[1]["delta"] == pytest.approx(0.0625)
    assert rounds[1]["selection"]["decision"] == "accept_candidate"
    assert rounds[1]["activated"] is True
    assert rounds[2]["selection"]["decision"] == "retain_parent"
    assert rounds[2]["activated"] is False
    assert rounds[2]["delta"] == pytest.approx(0.0)


def test_summarizer_separates_verification_valid_full_and_success(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path)
    # B2 parent: 9 zero-call rows + 7 called rows (4 success). Zero-call rows have a
    # completed verification record but no valid answer and must not count as full tests.
    r = {x["round"]: x for x in summarize(run)["rounds"]}[2]
    v = r["verification"]["parent"]
    assert v["completed"] == 16
    assert v["valid_answer"] == 7
    assert v["full_verifier"] == 7
    assert v["success"] == 4
    # zero-call rows must NOT be counted as full tests
    assert v["full_verifier"] < v["completed"]
    assert v["success"] <= v["full_verifier"]


def test_summarizer_zero_call_errors_and_transition_overlap(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path)
    r = {x["round"]: x for x in summarize(run)["rounds"]}[1]
    errs = r["errors"]["parent"]
    assert errs["zero_call"] == 11  # 9 retained + TASK_IDS[3] + TASK_IDS[0]
    assert errs["enoent"] >= 1
    assert errs["eagain"] >= 1
    assert errs["execution_error_type"]["candidate_error"] >= 1
    # protocol infrastructure_error preserved (fixture default False)
    assert errs["infrastructure_error_true"] == 0
    # zero-call overlap with new_success / new_regression
    overlap = r["zero_call_overlap"]
    assert overlap["new_success_zero_call"] >= 1
    assert overlap["new_regression_zero_call"] >= 1


def test_summarizer_component_selection_active_snapshot_skills_context(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path)
    result = summarize(run)
    r1 = {x["round"]: x for x in result["rounds"]}[1]
    assert r1["component"] == "HARNESS"
    assert r1["attempt_count"] == 1
    assert r1["snapshot_present"] is True
    assert result["active_identity"]["generation"] == 1
    assert result["skills_present"] is True
    assert result["context_present"] is True


def test_summarizer_missing_b3_reports_two_rounds_not_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path)
    result = summarize(run)
    assert result["completed_rounds"] == 2
    assert result["expected_rounds"] == 5
    assert result["run_complete"] is False
    assert any("B3" in b or "round" in b.lower() for b in result["run_complete_blockers"])


def test_summarizer_run_complete_requires_all_real_facts(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path, complete=True)
    result = summarize(run)
    assert result["completed_rounds"] == 5
    assert result["run_complete"] is True
    assert result["run_complete_blockers"] == []
    # remove stopped-services evidence -> must not claim complete
    (run / "meta_session/services_stopped.json").unlink()
    again = summarize(run)
    assert again["run_complete"] is False
    assert any("service" in b.lower() for b in again["run_complete_blockers"])


def test_summarizer_model_sft_not_run_without_real_evidence(tmp_path):
    summarize = _load_summarizer().summarize
    run, _, _ = _build_two_round_run(tmp_path)
    result = summarize(run)
    assert result["model_sft"]["status"] == "not_run"
    assert result["model_sft"]["rounds"] == []
    # add a real MODEL decision + candidate training_metrics -> recorded
    attempt = run / "round_2/attempts/attempt_1"
    decision = json.loads((attempt / "decision.json").read_text())
    decision["component"] = "MODEL"
    (attempt / "decision.json").write_text(json.dumps(decision), encoding="utf-8")
    (attempt / "candidate/training_metrics.json").write_text(
        json.dumps({"optimizer_steps": 5, "warmup_steps": 2, "training_loss": 0.25}), encoding="utf-8")
    again = summarize(run)
    assert again["model_sft"]["status"] == "recorded"
    assert 2 in again["model_sft"]["rounds"]
