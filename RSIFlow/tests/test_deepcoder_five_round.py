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
#
# Fixtures mirror production: a trusted machine config + frozen release built with
# evolution_protocol.make_manifest and data_protocol content hashing; side-scoped
# rollout_cache receipts whose per-call request_sha256 is the production canonical
# value_hash(transport call minus response); full TaskAgentState identity chains
# (generation/model_ref/checkpoint+weights/harness bytes/artifacts); snapshot seals
# with parent/candidate/selected states; compare/activate receipts bound to exact
# paths/hashes; raw workflow_state milestones + ordered turns + durable jobs; the
# output-root four-GPU registry; a kind=rsiflow_external_cleanup receipt; and the
# production model_update SFT schema (world_size/rank_records/epoch/loss/merge probe).
# ---------------------------------------------------------------------------

import hashlib
import math

import data_protocol as dp
from evolution_protocol import file_hash, make_manifest

SUMMARIZER_PATH = ROOT.parent / "reproduction/scripts/summarize_deepcoder_five_round.py"


def _load_summarizer():
    import importlib.util
    spec = importlib.util.spec_from_file_location("summarize_deepcoder_five_round_test", SUMMARIZER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TASK_IDS = [f"deepcoder_taco:deepcoder-taco-{i}" for i in range(1000, 1016)]
CHECKPOINT = "/models/Qwen3-4B"
VERIFIER_ID = "deepcoder_taco_all_tests_stdio_or_function_v2_tol1e-6"
WEIGHTS = [{"path": "model-00001-of-00001.safetensors", "bytes": 100, "sha256": "w" * 64}]


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _value_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _source_hash(task_id):
    return _sha("source:" + task_id)


def _build_release(base, rounds):
    release = base / "release"
    manifests, record_hashes = {}, {}
    for n in range(1, rounds + 1):
        bdir = release / f"B{n}"
        bdir.mkdir(parents=True)
        jsonl = bdir / "deepcoder_taco.jsonl"
        records = [{"task_id": tid, "prompt": f"problem {tid}"} for tid in TASK_IDS]
        offsets = []
        with jsonl.open("wb") as stream:
            for rec in records:
                off = stream.tell()
                line = (json.dumps(rec, ensure_ascii=False) + "\n").encode()
                stream.write(line)
                offsets.append((off, len(line)))
        rec_hash = file_hash(jsonl)
        record_hashes[n] = rec_hash
        tasks = []
        for tid, (off, ln), rec in zip(TASK_IDS, offsets, records):
            tasks.append({
                "source": "deepcoder_taco", "version": "v1", "original_split": "train",
                "task_id": tid, "group_id": tid,
                "content_hash": dp.text_hash(dp.public_text(rec, "deepcoder_taco")),
                "role": "train_evolution", "round_id": n, "domain": "code",
                "record_file": str(jsonl), "record_offset": off, "record_length": ln,
                "record_sha256": rec_hash, "source_sha256": rec_hash,
                "strata": {"interface": "function"}, "purpose": "evolution_train",
                "native_id": tid, "date": "2026-11-18",
            })
        manifest = make_manifest("train_evolution", n, tasks)
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifests[n] = manifest
    (release / "protocol.json").write_text(json.dumps({
        "rounds": rounds, "schedule": "round_repeated", "tasks_per_round": 16, "unique_tasks": 16,
    }), encoding="utf-8")
    return release, manifests, record_hashes


def _build_config(base, output_root, run_name, release, rounds):
    config_path = base / "machine.json"
    config_path.write_text(json.dumps({
        "output_root": str(output_root), "run_name": run_name,
        "frozen_data_dir": str(release), "rounds": rounds,
        "experiment_scope": "deepcoder_five_round_canary", "training_schedule": "round_repeated",
    }), encoding="utf-8")
    return config_path


def _round_scope(round_n, task_id, manifest_hash, collection_stage):
    return {"role": "train_evolution", "purpose": "evolution_train", "round_id": round_n,
            "manifest_hash": manifest_hash, "collection_stage": collection_stage,
            "task_hash": _sha(task_id), "harness_sha256": "h" * 64,
            "checkpoint_manifest": WEIGHTS, "artifacts": [], "protocol_hash": "p" * 64}


def _transport_call(call_id):
    return {
        "call_id": call_id, "status": "completed",
        "messages": [{"role": "user", "content": f"q{call_id}"}],
        "response": {"message": {"role": "assistant", "content": f"a{call_id}"}, "usage": {},
                     "binding": {"checkpoint_path": CHECKPOINT, "weights": WEIGHTS},
                     "finish_reason": "stop", "request_id": f"r{call_id}",
                     "model_ref_requested": CHECKPOINT, "model_ref_response": CHECKPOINT},
        "seed": 42, "temperature": 0.7, "tools": [], "max_tokens": 2048,
        "chat_template_kwargs": {}, "usage_complete": True, "unknown_usage_calls": 0,
    }


def _trajectory(task_id, *, success, calls, round_n, state_hash, branch, manifest_hash,
                task_source_hash, error=None, execution_error_type="candidate_error",
                infrastructure_error=False, tests_total=11, collection_stage="parent_pre_update"):
    called = calls > 0
    verification = {
        "status": "completed", "verifier_id": VERIFIER_ID, "success": bool(success),
        "full_verifier": called,
        "tests_completed": tests_total if called else None,
        "tests_total": tests_total if called else None,
    }
    return {
        "task_id": task_id, "question_id": task_id, "source": "deepcoder_taco",
        "domain": "code", "split": "evolve_train", "rollout_id": 0,
        "state_hash": state_hash, "manifest_hash": manifest_hash,
        "task_source_hash": task_source_hash, "source_role": "train_evolution",
        "collection_stage": collection_stage, "round_id": round_n,
        "infrastructure_error": infrastructure_error, "infrastructure_failure": infrastructure_error,
        "verification": verification, "valid_answer": called,
        "model_call_count": calls, "transport_calls": [_transport_call(i) for i in range(calls)],
        "execution_error_type": execution_error_type,
        "error_type": "parse_failure" if not called else ("candidate_error" if not success else None),
        "error": error, "terminal_reward": 1.0 if success else 0.0,
        "branch_id": branch, "trajectory_id": _sha(f"{task_id}:{branch}:{round_n}"),
        "model_ref": CHECKPOINT, "checkpoint_manifest": WEIGHTS,
    }


def _performance(rows, round_n, manifest_hash):
    successes = sum(1 for r in rows if r["verification"]["success"] is True)
    n = len(rows)
    rate = successes / n if n else None
    collection_stage = rows[0]["collection_stage"] if rows else "parent_pre_update"
    errors = {}
    for r in rows:
        if r["verification"]["success"] is not True:
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
        "training_manifest": manifest_hash, "evaluation_protocol": "same_round_full_B_parent_child_v2",
        "feedback_role": "train_evolution", "collection_stage": collection_stage,
        "probe_identity": _sha(f"probe-{round_n}"),
        "benchmarks": {"deepcoder_taco": {"count": n, "successes": successes, "errors": errors}},
    }


def _call_receipt(task_id, state_hash, round_n, transport_call, manifest_hash,
                  task_source_hash, collection_stage):
    request = {k: v for k, v in transport_call.items() if k != "response"}
    return {
        "binding": {"task": task_id, "task_source_hash": task_source_hash,
                    "state_hash": state_hash, "rollout": 0, "probe": False,
                    "round_scope": _round_scope(round_n, task_id, manifest_hash, collection_stage)},
        "call_id": transport_call["call_id"], "status": "completed",
        "request_sha256": _value_hash(request), "request": request,
        "response": transport_call["response"],
    }


def _write_side(side_dir, cache_root, rows, state_hash, round_n, manifest_hash):
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (side_dir / "performance.json").write_text(
        json.dumps(_performance(rows, round_n, manifest_hash)), encoding="utf-8")
    cache = cache_root / state_hash / "train_rollouts"
    cache.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (cache / (r["trajectory_id"] + ".json")).write_text(json.dumps({
            "binding": {"task": r["task_id"], "task_source_hash": r["task_source_hash"],
                        "state_hash": state_hash, "rollout": 0, "probe": False,
                        "round_scope": _round_scope(round_n, r["task_id"], manifest_hash, r["collection_stage"])},
            "row": {"task_id": r["task_id"], "state_hash": state_hash, "rollout_id": 0,
                    "model_call_count": r["model_call_count"], "checkpoint_manifest": WEIGHTS},
        }), encoding="utf-8")
        if r["model_call_count"]:
            calls_dir = cache / (r["trajectory_id"] + ".calls")
            calls_dir.mkdir(parents=True, exist_ok=True)
            for tc in r["transport_calls"]:
                (calls_dir / f"{tc['call_id']:04d}.json").write_text(json.dumps(
                    _call_receipt(r["task_id"], state_hash, round_n, tc, manifest_hash,
                                  r["task_source_hash"], r["collection_stage"])), encoding="utf-8")


def _task_state(generation, harness_path):
    return {"generation": generation, "model_ref": CHECKPOINT, "checkpoint_path": CHECKPOINT,
            "checkpoint_manifest": WEIGHTS, "harness_path": str(harness_path),
            "artifacts": {"directory": None, "manifest": []}}


def _harness_file(run, generation):
    path = run / "harness" / f"gen_{generation}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"harness_generation": generation}), encoding="utf-8")
    return path


def _transport_receipt(run, identifier, payload):
    directory = run / "controller_transport" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


_TURN_COUNTER = [0]


def _turn_receipt(run, operation, round_number, payload):
    _TURN_COUNTER[0] += 1
    name = f"{_TURN_COUNTER[0]:04d}"
    directory = run / "meta_session/turns" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "command.json").write_text(json.dumps({
        "action": "tool", "request": {"operation": operation, "round_number": round_number},
        "transport": "app_server",
    }), encoding="utf-8")
    (directory / "receipt.json").write_text(json.dumps({"operation": operation, **payload}), encoding="utf-8")
    return directory


def _write_job(run, identifier, operation, *, status="completed", returncode=0,
               result_extra=None, write_result=True, write_job=True):
    jobs = run / "meta_session/jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_dir = jobs / identifier
    job_dir.mkdir(parents=True, exist_ok=True)
    transport = run / "controller_transport" / identifier
    transport.mkdir(parents=True, exist_ok=True)
    result_path = transport / "result.json"
    turn_dir = run / "meta_session/turns" / identifier
    turn_dir.mkdir(parents=True, exist_ok=True)
    result = {"operation": operation, "status": status, "returncode": returncode}
    if result_extra:
        result.update(result_extra)
    if write_result:
        result_path.write_text(json.dumps(result), encoding="utf-8")
        (turn_dir / "receipt.json").write_text(json.dumps(result), encoding="utf-8")
        (turn_dir / "command.json").write_text(json.dumps({
            "action": "tool", "request": {"operation": operation}}), encoding="utf-8")
    if write_job:
        (job_dir / "job.json").write_text(json.dumps({
            "job_id": identifier, "request": {"operation": operation},
            "pid": 10000 + abs(hash(identifier)) % 1000, "process_start": None,
            "created_at": 1.0, "turn_dir": str(turn_dir), "result_path": str(result_path),
            "stdout_path": str(transport / "stdout.log"), "stderr_path": str(transport / "stderr.log"),
        }), encoding="utf-8")
    return result_path


def _write_round(run, n, *, parent_success, candidate_success,
                 zero_call_parent, zero_call_candidate,
                 parent_generation, candidate_generation, manifest_hash, task_source_hash,
                 component="HARNESS", decision="accept_candidate",
                 errors_parent=None, errors_candidate=None, infrastructure_error_ids=()):
    errors_parent = errors_parent or {}
    errors_candidate = errors_candidate or {}
    round_dir = run / f"round_{n}"
    parent_state_hash = _sha(f"parent-state-{n}")
    candidate_state_hash = _sha(f"candidate-state-{n}")

    def make_rows(success_set, zero_set, branch, state_hash, errors, collection_stage):
        rows = []
        for tid in TASK_IDS:
            calls = 0 if tid in zero_set else 7
            rows.append(_trajectory(
                tid, success=tid in success_set, calls=calls, round_n=n,
                state_hash=state_hash, branch=branch, manifest_hash=manifest_hash,
                task_source_hash=task_source_hash, error=errors.get(tid),
                infrastructure_error=tid in infrastructure_error_ids,
                collection_stage=collection_stage))
        return rows

    parent_rows = make_rows(parent_success, zero_call_parent, "parent", parent_state_hash, errors_parent, "parent_pre_update")
    candidate_rows = make_rows(candidate_success, zero_call_candidate, "candidate", candidate_state_hash, errors_candidate, "child_post_update")
    _write_side(round_dir / "parent", round_dir / "rollout_cache", parent_rows, parent_state_hash, n, manifest_hash)
    attempt = round_dir / "attempts/attempt_1"
    _write_side(attempt / "candidate", attempt / "rollout_cache", candidate_rows, candidate_state_hash, n, manifest_hash)

    before = len(parent_success) / 16
    after = len(candidate_success) / 16
    delta = after - before
    new_success = len(candidate_success - parent_success)
    new_regression = len(parent_success - candidate_success)
    retained_failure = 16 - len(parent_success | candidate_success)
    selected_generation = candidate_generation if decision == "accept_candidate" else parent_generation

    (attempt / "decision.json").write_text(json.dumps({
        "round": n, "attempt": 1, "component": component, "relevant_skill_ids": [],
        "rationale": "fixture", "prediction": {},
    }), encoding="utf-8")
    (attempt / "selection.json").write_text(json.dumps({
        "round": n, "attempt": 1, "decision": decision, "reason": "fixture",
        "before": before, "after": after, "delta": delta, "paired_tasks": 16,
        "new_success": new_success, "new_regression": new_regression,
    }), encoding="utf-8")
    (attempt / "skill_use.json").write_text(json.dumps({"used_skill_ids": []}), encoding="utf-8")
    diff_dir = attempt / "task_differences"
    diff_dir.mkdir(parents=True, exist_ok=True)
    (diff_dir / "summary.json").write_text(json.dumps({
        "status": "compared", "paired_tasks": 16, "same_id_set": True,
        "counts": {"retained_failure": retained_failure, "new_success": new_success, "new_regression": new_regression},
        "domain_transitions": {"code": {"retained_failure": retained_failure, "new_success": new_success, "new_regression": new_regression}},
        "source_mismatches": [], "duplicates": {"before": [], "after": []},
        "missing_in_after": [], "extra_in_after": [], "notes": [],
    }), encoding="utf-8")

    parent_harness = _harness_file(run, parent_generation)
    candidate_harness = attempt / "build/candidate_bundle.json"
    candidate_harness.parent.mkdir(parents=True, exist_ok=True)
    candidate_harness.write_text(json.dumps({"harness_generation": candidate_generation}), encoding="utf-8")
    parent_state = _task_state(parent_generation, parent_harness)
    candidate_state = _task_state(candidate_generation, candidate_harness)
    selected_state = _task_state(selected_generation,
                                 candidate_harness if decision == "accept_candidate" else parent_harness)
    (attempt / "before").mkdir(parents=True, exist_ok=True)
    (attempt / "before/parent_task.json").write_text(json.dumps(parent_state), encoding="utf-8")
    (attempt / "build/candidate_state.json").write_text(json.dumps(candidate_state), encoding="utf-8")

    snap = run / "snapshots" / f"round_{n}_attempt_1"
    (snap / "task").mkdir(parents=True, exist_ok=True)
    (snap / "task/parent_state.json").write_text(json.dumps(parent_state), encoding="utf-8")
    (snap / "task/candidate_state.json").write_text(json.dumps(candidate_state), encoding="utf-8")
    (snap / "task/task_state_snapshot.json").write_text(json.dumps(selected_state), encoding="utf-8")
    files_sha256 = {
        "task/parent_state.json": _sha(json.dumps(parent_state)),
        "task/candidate_state.json": _sha(json.dumps(candidate_state)),
        "task/task_state_snapshot.json": _sha(json.dumps(selected_state)),
    }
    (snap / "snapshot_receipt.json").write_text(json.dumps({
        "status": "snapshotted", "destination": str(snap),
        "combinations": {
            "parent": {"state_path": str(snap / "task/parent_state.json"),
                       "source_state": str(attempt / "before/parent_task.json")},
            "candidate": {"state_path": str(snap / "task/candidate_state.json"),
                          "source_state": str(attempt / "build/candidate_state.json")},
            "selected": {"state_path": str(snap / "task/task_state_snapshot.json"),
                         "source_state": str(run / "active_task.json")},
        },
        "files_sha256": files_sha256,
        "meta": {"id": f"meta_{n+1:03d}", "sha256": _sha(f"meta-{n}")},
        "references": {"context_reference": {"sha256": _sha(f"context-{n}")}},
    }), encoding="utf-8")

    parent_perf_path = str(round_dir / "parent/performance.json")
    candidate_perf_path = str(attempt / "candidate/performance.json")
    compare_payload = {
        "operation": "compare_scores", "status": "compared", "metric": "macro_success",
        "before": before, "after": after, "delta": delta,
        "before_path": parent_perf_path, "after_path": candidate_perf_path,
        "before_sha256": file_hash(Path(parent_perf_path)), "after_sha256": file_hash(Path(candidate_perf_path)),
    }
    _transport_receipt(run, f"compare_{n}", compare_payload)
    _turn_receipt(run, "compare_scores", n, {"status": "compared", "before": before, "after": after, "delta": delta})
    if decision == "accept_candidate":
        candidate_state_path = str(attempt / "build/candidate_state.json")
        active_sha = file_hash(Path(candidate_state_path))
        activate_payload = {
            "operation": "activate_task", "status": "activated_as_requested",
            "source_state": candidate_state_path, "active_state": str(run / "active_task.json"),
            "source_sha256": active_sha, "active_sha256": active_sha, "selection_note": "fixture accept",
        }
        _transport_receipt(run, f"activate_{n}", activate_payload)
        _turn_receipt(run, "activate_task", n, {"status": "activated_as_requested", "source_sha256": active_sha})
    _transport_receipt(run, f"snapshot_{n}", {"operation": "snapshot_task_meta", "status": "snapshotted", "destination": str(snap)})
    _turn_receipt(run, "snapshot_task_meta", n, {"status": "snapshotted"})
    _write_job(run, f"run_parent_{n}", "run_parent")
    _write_job(run, f"run_candidate_{n}", "run_candidate")
    return {"parent_success": parent_success, "candidate_success": candidate_success,
            "new_success": new_success, "new_regression": new_regression,
            "retained_failure": retained_failure, "delta": delta,
            "parent_generation": parent_generation, "selected_generation": selected_generation}


def _cleanup_receipt(run, output_root, config_path, *, status="completed", attempt="0001",
                     run_binding=None, registry_phase="services_stopped", compute_apps=None,
                     free_verified=True, owners_exited=True):
    directory = run / "meta_session/supervision" / attempt / "external-cleanup" / "obs1"
    directory.mkdir(parents=True, exist_ok=True)
    gpu_obs = {"time": "2026-11-18T00:00:00Z", "command": "nvidia-smi", "result": 0,
               "stdout_hash": _sha("gpu"), "expected_uuids": ["GPU-0", "GPU-1", "GPU-2", "GPU-3"],
               "per_gpu": [{"gpu": g, "memory_free": 80000} for g in range(4)],
               "compute_apps": compute_apps if compute_apps is not None else [],
               "free_verified": free_verified, "namespace_notes": ""}
    (directory / "gpu_observation.json").write_text(json.dumps(gpu_obs), encoding="utf-8")
    registry_path = str(output_root / "services/four_gpu_services.json")
    exit_state = "exited" if owners_exited else "alive"
    receipt = {
        "schema_version": 1, "kind": "rsiflow_external_cleanup", "observation_id": "obs1",
        "status": status, "started_at_utc": "2026-11-18T00:00:00Z", "observed_at_utc": "2026-11-18T00:01:00Z",
        "blockers": [] if status == "completed" else ["pending_owner"],
        "writer": {"script_path": "reconcile_deepcoder_run.py", "hash": _sha("writer"),
                   "host": "dev4", "boot_id": "boot", "pid_namespace": "ns", "uid": 10045},
        "binding": {"reviewed_commit": "0" * 40, "config_path": str(config_path),
                    "config_hash": file_hash(Path(config_path)) if config_path and Path(config_path).is_file() else _sha("cfg"),
                    "base_config_path": "/base.json", "base_config_hash": _sha("base"),
                    "effective_config_hash": _sha("effective"), "output_root": str(output_root),
                    "run_dir": run_binding if run_binding is not None else str(run),
                    "supervision_attempt": attempt, "launch_receipt_path": "/launch.json",
                    "launch_receipt_hash": _sha("launch"), "backend": "linux_bwrap_seccomp_v1", "thread_id": "thread-1"},
        "supervision": {"identity_path": "identity.json", "identity_hash": _sha("id"),
                        "exit_path": "exit", "exit_hash": _sha("0"), "exit_value": 0,
                        "reconcile_path": "reconcile.json", "reconcile_hash": _sha("rec"),
                        "reconcile_status": "complete", "all_previous_attempts_accounted_for": True},
        "protocol": {"state_path": "state.json", "state_hash": _sha("state"),
                     "current_stage": "COMPLETE", "finish_allowed": True},
        "owners": {"launcher": {"state": exit_state}, "app_server": {"state": exit_state},
                   "jobs": [{"state": exit_state}], "training_phases": [],
                   "inference_services": [{"state": exit_state}]},
        "registry": {"path": registry_path, "before_hash": _sha("reg-before"), "after_hash": _sha("reg-after"),
                     "phase": registry_phase, "checkpoint": CHECKPOINT, "weights": WEIGHTS,
                     "stop_action_skipped_or_executed": "executed", "command_result": 0},
        "gpu_observations": [gpu_obs], "ports_observation": {"status": "closed"},
        "actions": [], "errors": [],
        "evidence_files_sha256": {"gpu_observation.json": file_hash(directory / "gpu_observation.json")},
        "occupier": {"prior_identity_hash": _sha("occ"), "stop_receipt_hash": _sha("stop"), "restore_pending": False},
    }
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    return receipt_path


def _build_run(tmp_path, *, complete=False, with_config=True, with_cleanup=True):
    _TURN_COUNTER[0] = 0
    output_root = tmp_path / "output_root"
    run = output_root / "runs" / "run"
    run.mkdir(parents=True)
    rounds = 5 if complete else 2
    release, manifests, record_hashes = _build_release(tmp_path, rounds)
    config_path = _build_config(tmp_path, output_root, "run", release, rounds) if with_config else None
    if complete:
        plan = [
            (1, set(TASK_IDS[:3]), set(TASK_IDS[3:7]), "accept_candidate"),
            (2, set(TASK_IDS[3:7]), set(TASK_IDS[3:7]), "retain_parent"),
            (3, set(TASK_IDS[3:7]), set(TASK_IDS[3:8]), "accept_candidate"),
            (4, set(TASK_IDS[3:8]), set(TASK_IDS[3:8]), "retain_parent"),
            (5, set(TASK_IDS[3:8]), set(TASK_IDS[3:9]), "accept_candidate"),
        ]
    else:
        plan = [
            (1, set(TASK_IDS[:3]), set(TASK_IDS[3:7]), "accept_candidate"),
            (2, set(TASK_IDS[3:7]), set(TASK_IDS[3:7]), "retain_parent"),
        ]
    zero_call = set(TASK_IDS[9:16])
    generation = 0
    specs = {}
    _turn_receipt(run, "bootstrap", 0, {"status": "completed"})
    _turn_receipt(run, "snapshot_task_meta", 0, {"status": "snapshotted"})
    _write_job(run, "bootstrap_0", "bootstrap")
    for n, parent_success, candidate_success, decision in plan:
        parent_generation = generation
        candidate_generation = generation + 1
        specs[n] = _write_round(
            run, n, parent_success=parent_success, candidate_success=candidate_success,
            zero_call_parent=zero_call, zero_call_candidate=zero_call,
            parent_generation=parent_generation, candidate_generation=candidate_generation,
            manifest_hash=manifests[n]["manifest_hash"], task_source_hash=record_hashes[n],
            component="HARNESS", decision=decision)
        generation = specs[n]["selected_generation"]

    final_harness = _harness_file(run, generation)
    (run / "active_task.json").write_text(json.dumps(_task_state(generation, final_harness)), encoding="utf-8")
    a0 = run / "snapshots/A0"
    (a0 / "task").mkdir(parents=True, exist_ok=True)
    a0_harness = _harness_file(run, 0)
    (a0 / "task/task_state_snapshot.json").write_text(json.dumps(_task_state(0, a0_harness)), encoding="utf-8")
    (a0 / "snapshot_receipt.json").write_text(json.dumps({
        "status": "snapshotted", "destination": str(a0),
        "combinations": {"selected": {"state_path": str(a0 / "task/task_state_snapshot.json"),
                                      "source_state": str(run / "active_task.json")}},
        "files_sha256": {"task/task_state_snapshot.json": file_hash(a0 / "task/task_state_snapshot.json")},
        "meta": {"id": "meta_001", "sha256": _sha("meta-0")},
    }), encoding="utf-8")

    meta = run / "meta"
    meta.mkdir(exist_ok=True)
    (meta / "skills.jsonl").write_text(json.dumps({"id": "case.HARNESS.x", "type": "case"}) + "\n", encoding="utf-8")
    (meta / "context.json").write_text(json.dumps({"round": plan[-1][0], "attempt": 1}), encoding="utf-8")
    session = run / "meta_session"
    session.mkdir(exist_ok=True)
    (session / "state.json").write_text(json.dumps({
        "status": "complete" if complete else "running", "transport": "app_server",
        "last_turn_status": "completed" if complete else "running",
        "launcher_pid": 14871, "codex_pid": 14896,
    }), encoding="utf-8")
    (session / "RUN_STATUS.md").write_text(
        ("Current stage: **COMPLETE**\nFinish allowed: **yes**\nFinal stage: **B5**\n"
         if complete else "Current stage: **B3**\nFinish allowed: **no**\nFinal stage: **B1**\n"),
        encoding="utf-8")
    stage_ids = ["A0"] + [f"B{n}" for n in range(1, rounds + 1)]
    (session / "workflow_state.json").write_text(json.dumps({
        "current_stage": "COMPLETE" if complete else "B3",
        "finish_allowed": complete, "final_stage": f"B{rounds}",
        "stages": [{"id": sid, "status": "complete" if complete else ("complete" if sid in ("A0", "B1", "B2") else "pending"),
                    "remaining_milestones": []} for sid in stage_ids],
    }), encoding="utf-8")
    if complete:
        _turn_receipt(run, "finish_experiment", 5, {"status": "complete", "remaining": {}})
        supervision = session / "supervision" / "0001"
        supervision.mkdir(parents=True, exist_ok=True)
        (supervision / "identity.json").write_text(json.dumps({
            "pid": 14852, "pgid": 14852, "start_ticks": "100", "run_dir": str(run), "attempt_dir": str(supervision),
        }), encoding="utf-8")
        (supervision / "reconcile.json").write_text(json.dumps({
            "status": "complete", "term_targets": [], "kill_targets": [], "remaining": [],
        }), encoding="utf-8")
        (supervision / "exit").write_text("0", encoding="utf-8")
        registry = output_root / "services" / "four_gpu_services.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "phase": "services_stopped", "checkpoint": CHECKPOINT,
            "services": [{"gpu": g, "base_url": f"http://127.0.0.1:{8271+g}", "pid": 20000 + g,
                          "identity": {"pid": 20000 + g, "start_ticks": "1", "pgid": 20000 + g},
                          "command": ["python", "start_multidomain_service.py"],
                          "config": str(registry.parent / f"four_gpu/gpu_{g}.json"),
                          "verified_binding": {"checkpoint_path": CHECKPOINT, "weights": WEIGHTS}} for g in range(4)],
            "started_at": 1.0, "verified_at": 2.0, "stopped_at": 3.0,
        }), encoding="utf-8")
    cleanup_path = None
    if complete and with_cleanup:
        cleanup_path = _cleanup_receipt(run, output_root, config_path)
    return run, specs, {"config_path": config_path, "cleanup_path": cleanup_path,
                        "release": release, "manifests": manifests, "output_root": output_root}


def _summarize(run, ctx, **kwargs):
    module = _load_summarizer()
    return module.summarize(run, config_path=ctx.get("config_path"),
                            cleanup_receipt_path=ctx.get("cleanup_path"), **kwargs)


def _rounds(result):
    return {r["round"]: r for r in result["rounds"]}


def _blockers(result):
    return result.get("run_complete_blockers", []) + result.get("protocol_complete_blockers", [])


# --- positive controls -----------------------------------------------------

def test_summarizer_complete_run_is_legal_and_complete(tmp_path):
    run, specs, ctx = _build_run(tmp_path, complete=True)
    result = _summarize(run, ctx)
    assert result["trusted_config"] is True
    assert result["completed_rounds"] == 5
    assert result["protocol_complete"] is True
    assert result["launcher_exited"] is True
    assert result["run_complete"] is True
    assert result["run_complete_blockers"] == []
    for n, spec in specs.items():
        r = _rounds(result)[n]
        assert r["products_complete"] is True
        if r["selection"]["decision"] == "accept_candidate":
            assert r["delta"] > 0
    assert result["active_identity"]["generation"] == specs[5]["selected_generation"]


def test_summarizer_two_rounds_observed_facts(tmp_path):
    run, specs, ctx = _build_run(tmp_path)
    result = _summarize(run, ctx)
    assert result["completed_rounds"] == 2
    assert result["run_complete"] is False
    rounds = _rounds(result)
    for n, spec in specs.items():
        r = rounds[n]
        assert r["parent"]["denominator"] == 16
        assert r["candidate"]["denominator"] == 16
        assert r["products_complete"] is True
        assert r["calls"]["cross_check_ok"] is True
        assert r["calls"]["parent"]["trajectory_calls"] == r["calls"]["parent"]["persisted_receipts"]
        assert r["transitions"]["new_success"] == spec["new_success"]
    assert rounds[1]["delta"] == pytest.approx(0.0625)
    assert rounds[1]["activated"] is True
    assert rounds[2]["activated"] is False


def test_summarizer_verification_separation(tmp_path):
    run, _, ctx = _build_run(tmp_path)
    r = _rounds(_summarize(run, ctx))[2]
    v = r["verification"]["parent"]
    assert v == {"completed": 16, "valid_answer": 9, "full_verifier": 9, "success": 4}


def test_summarizer_missing_b3_not_complete(tmp_path):
    run, _, ctx = _build_run(tmp_path)
    result = _summarize(run, ctx)
    assert result["completed_rounds"] == 2
    assert result["protocol_complete"] is False
    assert any("B3" in b for b in _blockers(result))


# --- F1: trusted config/release frozen membership --------------------------

def test_f1_no_trusted_config_blocks_completion_but_keeps_facts(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_config=False)
    result = _load_summarizer().summarize(run, cleanup_receipt_path=ctx["cleanup_path"])
    assert result["trusted_config"] is False
    assert result["protocol_complete"] is False
    assert result["run_complete"] is False
    assert any("trusted config" in b for b in _blockers(result))
    # observed arithmetic still reported
    r1 = _rounds(result)[1]
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["calls"]["parent"]["persisted_receipts"] == r1["calls"]["parent"]["trajectory_calls"]


def test_f1_consistent_id_rename_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    rename = {tid: f"deepcoder_taco:fake-{i}" for i, tid in enumerate(TASK_IDS)}
    # Rename consistently across trajectories AND persisted rollout/call bindings so
    # internal self-consistency holds; only the trusted frozen manifest can reject it.
    for side, cache_root in (("round_1/parent", "round_1/rollout_cache"),
                             ("round_1/attempts/attempt_1/candidate", "round_1/attempts/attempt_1/rollout_cache")):
        path = run / side / "train_trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for r in rows:
            r["task_id"] = rename[r["task_id"]]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        for cache_json in (run / cache_root).glob("*/train_rollouts/*.json"):
            receipt = json.loads(cache_json.read_text())
            receipt["binding"]["task"] = rename[receipt["binding"]["task"]]
            cache_json.write_text(json.dumps(receipt), encoding="utf-8")
        for call_file in (run / cache_root).glob("*/train_rollouts/*.calls/*.json"):
            call = json.loads(call_file.read_text())
            call["binding"]["task"] = rename[call["binding"]["task"]]
            call_file.write_text(json.dumps(call), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False
    assert any("frozen" in b for b in _rounds(result)[1]["product_blockers"])


def test_f1_consistent_manifest_hash_change_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    for side in ("round_1/parent", "round_1/attempts/attempt_1/candidate"):
        path = run / side / "train_trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for r in rows:
            r["manifest_hash"] = "f" * 64
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        perf_path = run / side / "performance.json"
        perf = json.loads(perf_path.read_text())
        perf["training_manifest"] = "f" * 64
        perf_path.write_text(json.dumps(perf), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


def test_f1_wrong_run_dir_derivation_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    config = json.loads(Path(ctx["config_path"]).read_text())
    config["run_name"] = "other_run"
    Path(ctx["config_path"]).write_text(json.dumps(config), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["trusted_config"] is False
    assert result["run_complete"] is False


def test_f1_tests_total_must_be_positive_int(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for r in rows:
        if r["verification"]["success"] is True:
            r["verification"]["tests_total"] = True  # bool, not positive int
            r["verification"]["tests_completed"] = True
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


def test_f1_benchmark_performance_inconsistency_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    perf_path = run / "round_1/parent/performance.json"
    perf = json.loads(perf_path.read_text())
    perf["benchmarks"]["deepcoder_taco"]["successes"] = 99
    perf_path.write_text(json.dumps(perf), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


# --- F2: canonical request hash + request/response/model/weights alignment --

def test_f2_wrong_request_hash_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["request_sha256"] = "not-the-request-hash"
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_f2_wrong_response_model_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["response"]["model_ref_response"] = "UNRELATED_MODEL"
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_f2_request_response_mismatch_transport_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["request"]["messages"] = [{"role": "user", "content": "tampered"}]
    receipt["request_sha256"] = _value_hash(receipt["request"])  # self-consistent but != transport
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_f2_wrong_weights_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["response"]["binding"]["weights"] = [{"path": "other", "bytes": 1, "sha256": "x" * 64}]
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_f2_malformed_extra_call_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    calls_dir = next(d for d in cache.glob("*.calls") if d.is_dir())
    (calls_dir / "9999.json").write_text("{bad json", encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_f2_per_task_offsetting_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    called = [r for r in rows if r["model_call_count"] > 0]
    called[0]["model_call_count"] += 1
    called[1]["model_call_count"] -= 1
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


# --- F3: full Task identity chain / snapshot seal --------------------------

def test_f3_same_generation_different_harness_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    active_path = run / "active_task.json"
    active = json.loads(active_path.read_text())
    other = run / "harness" / "unrelated.json"
    other.write_text(json.dumps({"harness_generation": 999}), encoding="utf-8")
    active["harness_path"] = str(other)  # generation unchanged, harness bytes differ
    active_path.write_text(json.dumps(active), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("active chain" in b for b in _blockers(result))


def test_f3_same_generation_different_checkpoint_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    active_path = run / "active_task.json"
    active = json.loads(active_path.read_text())
    active["checkpoint_path"] = "/models/Other"
    active["model_ref"] = "/models/Other"
    active_path.write_text(json.dumps(active), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False


def test_f3_snapshot_selected_outside_seal_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    receipt_path = run / "snapshots/round_1_attempt_1/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["combinations"]["selected"]["state_path"] = str(run / "active_task.json")
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


def test_f3_retain_selected_must_equal_parent(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    # B2 is retain; point its selected snapshot state at the candidate (wrong)
    snap_state = run / "snapshots/round_2_attempt_1/task/task_state_snapshot.json"
    candidate = json.loads((run / "snapshots/round_2_attempt_1/task/candidate_state.json").read_text())
    snap_state.write_text(json.dumps(candidate), encoding="utf-8")
    receipt_path = run / "snapshots/round_2_attempt_1/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files_sha256"]["task/task_state_snapshot.json"] = file_hash(snap_state)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[2]["products_complete"] is False


def test_f3_zero_gain_accept_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    selection_path = run / "round_5/attempts/attempt_1/selection.json"
    selection = json.loads(selection_path.read_text())
    selection["after"] = selection["before"]
    selection["delta"] = 0.0
    selection["new_success"] = 0
    selection["new_regression"] = 0
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[5]["products_complete"] is False


def test_f3_accept_without_activate_receipt_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    (run / "controller_transport/activate_1/result.json").unlink()
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["activated"] is False
    assert _rounds(result)[1]["products_complete"] is False


def test_f3_corrupt_snapshot_hash_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    receipt_path = run / "snapshots/round_1_attempt_1/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files_sha256"]["task/task_state_snapshot.json"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


def test_f3_missing_skills_context_a0_block(tmp_path):
    import shutil
    run, _, ctx = _build_run(tmp_path, complete=True)
    (run / "meta/skills.jsonl").unlink()
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("skill" in b.lower() for b in _blockers(result))
    run2, _, ctx2 = _build_run(tmp_path / "b", complete=True)
    shutil.rmtree(run2 / "snapshots/A0")
    result2 = _summarize(run2, ctx2)
    assert any("A0" in b for b in _blockers(result2))


# --- F4: real job inventory / ordered turns / workflow milestones ----------

def test_f4_missing_all_jobs_rejected(tmp_path):
    import shutil
    run, _, ctx = _build_run(tmp_path, complete=True)
    shutil.rmtree(run / "meta_session/jobs")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("job" in b.lower() for b in _blockers(result))


def test_f4_completed_nonzero_returncode_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "controller_transport/run_parent_1/result.json"
    value = json.loads(path.read_text())
    value["status"] = "completed"
    value["returncode"] = 1
    path.write_text(json.dumps(value), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("required production jobs failed" in b for b in _blockers(result))


def test_f4_unknown_status_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "controller_transport/run_parent_1/result.json"
    value = json.loads(path.read_text())
    value["status"] = "starting"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False


def test_f4_wrong_result_root_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    job_path = run / "meta_session/jobs/run_parent_1/job.json"
    job = json.loads(job_path.read_text())
    job["result_path"] = str(tmp_path / "elsewhere" / "result.json")
    job_path.write_text(json.dumps(job), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False


def test_f4_early_finish_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    # move finish turn before B5 snapshot by renaming it to a low number
    turns = run / "meta_session/turns"
    finish_dir = next(d for d in turns.iterdir()
                      if (d / "command.json").is_file()
                      and json.loads((d / "command.json").read_text())["request"]["operation"] == "finish_experiment")
    early = turns / "0000_early"
    finish_dir.rename(early)
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("finish" in b.lower() for b in _blockers(result))


def test_f4_workflow_incomplete_stage_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    workflow_path = run / "meta_session/workflow_state.json"
    workflow = json.loads(workflow_path.read_text())
    for stage in workflow["stages"]:
        if stage["id"] == "B3":
            stage["remaining_milestones"] = ["selection_recorded"]
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("workflow incomplete" in b for b in _blockers(result))


def test_f4_exploration_terminal_failure_allowed(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _write_job(run, "explore_cmd", "run_command", status="exited", returncode=127)
    result = _summarize(run, ctx)
    assert result["protocol_complete"] is True


# --- F5: external cleanup receipt consumer ---------------------------------

def test_f5_protocol_complete_without_cleanup_run_incomplete(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    result = _summarize(run, ctx)
    assert result["protocol_complete"] is True
    assert result["run_complete"] is False
    assert "external_cleanup_missing" in result["external_cleanup"]["blockers"]
    assert result["services_stopped"] is True  # registry fact still shown


def test_f5_cleanup_pending_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], status="pending")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["protocol_complete"] is True
    assert result["run_complete"] is False
    assert any("external_cleanup_pending" in b for b in result["external_cleanup"]["blockers"])


def test_f5_cleanup_wrong_run_binding_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], run_binding="/other/run")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False
    assert "external_cleanup_run_binding" in result["external_cleanup"]["blockers"]


def test_f5_cleanup_gpu_compute_apps_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], compute_apps=[{"pid": 999}])
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False
    assert "external_cleanup_gpu_compute_apps" in result["external_cleanup"]["blockers"]


def test_f5_cleanup_owner_alive_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], owners_exited=False)
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False


def test_f5_exit0_stopped_alone_insufficient(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    # registry stopped + supervision exit0 exist, but no cleanup receipt
    result = _summarize(run, ctx)
    assert result["services_stopped"] is True
    assert result["launcher_exited"] is True
    assert result["run_complete"] is False


def test_f5_completed_cleanup_binding_valid_completes(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    result = _summarize(run, ctx)
    assert result["external_cleanup"]["ok"] is True
    assert result["run_complete"] is True


# --- F6: production MODEL/SFT schema ---------------------------------------

def _add_model_round(run, ctx, n=2, *, mutate=None):
    attempt = run / f"round_{n}/attempts/attempt_1"
    decision_path = attempt / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    parent_state = json.loads((attempt / "before/parent_task.json").read_text())
    out = attempt / "candidate"
    model_update = out / "model_update"
    model_update.mkdir(parents=True, exist_ok=True)
    child = _task_state(parent_state["generation"] + 1, attempt / "build/candidate_bundle.json")
    child["checkpoint_path"] = "/models/Qwen3-4B-trained"
    child["model_ref"] = "/models/Qwen3-4B-trained"
    selection_report = {"positive_samples": 4, "input": 4, "unique": 4}
    metrics = {
        "world_size": 4,
        "rank_records": [{"rank": r, "local_rank": r, "sampled_indices": [r], "optimizer_steps": 1,
                          "peak_memory_bytes": 1024} for r in range(4)],
        "num_train_epochs_completed": 1.0, "training_loss": 0.5,
        "changed_lora_tensors": 7, "lora_delta_l2": 0.1,
        "merged_probe_name": "q_proj", "merged_probe_changed_elements": 100,
        "optimizer_steps": 1, "selection": selection_report,
        "positive_trajectories": 4, "checkpoint_path": "/models/Qwen3-4B-trained",
        "serving_status": "ready",
    }
    gpu = {"status": "completed", "restored_checkpoint": "/models/Qwen3-4B-trained",
           "replica_gpus": [0, 1, 2, 3], "gpus": [0, 1, 2, 3]}
    trained = {"model_ref": "/models/Qwen3-4B-trained", "checkpoint_path": "/models/Qwen3-4B-trained",
               "training_metrics": str(model_update / "training_metrics.json"),
               "training_method": "positive_sft_lora", "training_steps": 1, "training_summary": "fixture"}
    if mutate:
        mutate(metrics, gpu, child, trained)
    (out / "candidate_state.json").write_text(json.dumps(child), encoding="utf-8")
    (model_update / "training_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (model_update / "gpu_phase_receipt.json").write_text(json.dumps(gpu), encoding="utf-8")
    (model_update / "checkpoint_trained.json").write_text(json.dumps(trained), encoding="utf-8")
    _transport_receipt(run, f"sft_{n}", {
        "operation": "sft", "status": "completed",
        "candidate_state_path": str(out / "candidate_state.json"),
        "training_metrics_path": str(model_update / "training_metrics.json"),
        "checkpoint_path": "/models/Qwen3-4B-trained", "positive_samples": 4,
    })
    _turn_receipt(run, "sft", n, {"status": "completed"})
    return metrics


def test_f6_model_completed_real_schema(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] == "completed"
    assert 2 in result["model_sft"]["rounds"]


def test_f6_model_selected_no_evidence_blocks(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    decision_path = run / "round_2/attempts/attempt_1/decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] in ("selected_pending", "selected_failed")
    assert result["run_complete"] is False
    assert _rounds(result)[2]["products_complete"] is False


def test_f6_fictional_fields_not_accepted(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    attempt = run / "round_2/attempts/attempt_1"
    decision_path = attempt / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    out = attempt / "candidate"
    (out / "model_update").mkdir(parents=True, exist_ok=True)
    (out / "model_update/training_metrics.json").write_text(json.dumps({
        "optimizer_steps": 1, "ranks": 4, "epochs": 1, "merged": True, "reloaded": True,
    }), encoding="utf-8")
    _transport_receipt(run, "sft_2", {
        "operation": "sft", "status": "completed",
        "candidate_state_path": str(out / "candidate_state.json"),
        "training_metrics_path": str(out / "model_update/training_metrics.json"),
    })
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"
    assert result["run_complete"] is False


@pytest.mark.parametrize("field", ["world_size", "rank_records", "epoch", "loss", "changed_lora", "merged_probe", "child"])
def test_f6_wrong_fields_rejected(tmp_path, field):
    run, _, ctx = _build_run(tmp_path, complete=True)

    def mutate(metrics, gpu, child, trained):
        if field == "world_size":
            metrics["world_size"] = 1
        elif field == "rank_records":
            metrics["rank_records"] = metrics["rank_records"][:3]
        elif field == "epoch":
            metrics["num_train_epochs_completed"] = 2.0
        elif field == "loss":
            metrics["training_loss"] = float("nan")
        elif field == "changed_lora":
            metrics["changed_lora_tensors"] = 0
        elif field == "merged_probe":
            metrics["merged_probe_changed_elements"] = 0
        elif field == "child":
            child["generation"] = 99

    _add_model_round(run, ctx, 2, mutate=mutate)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"
    assert result["run_complete"] is False


def test_f6_not_selected_without_model_round(tmp_path):
    run, _, ctx = _build_run(tmp_path)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] == "not_selected"


# --- I8 errno layering (retained) + real single-run observed facts ----------

def test_i8_error_layering_fixture(tmp_path):
    run, _, ctx = _build_run(tmp_path)
    parent_path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in parent_path.read_text().splitlines() if line.strip()]
    zero = [r for r in rows if r["model_call_count"] == 0]
    for r in zero[:3]:
        r["error"] = "FileNotFoundError: [Errno 2] No such file or directory"
    for r in zero[3:]:
        r["error"] = "BlockingIOError: [Errno 11] Resource temporarily unavailable (EAGAIN)"
    called_success = [r for r in rows if r["model_call_count"] > 0 and r["verification"]["success"]]
    called_success[0]["error"] = "FileNotFoundError: [Errno 2] No such file or directory"
    parent_path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    errs = _rounds(_summarize(run, ctx))[1]["errors"]["parent"]
    assert errs["error_mentions_all_rows"]["enoent"] == 4
    assert errs["zero_call_negative"]["count"] == 7
    assert errs["zero_call_negative"]["enoent"] == 3
    assert errs["zero_call_negative"]["eagain"] == 4
    assert errs["called_with_error"]["enoent"] == 1
    assert errs["success_with_error"]["enoent"] == 1


def test_i8_realistic_zero_call_overlap(tmp_path):
    import shutil
    run, _, ctx = _build_run(tmp_path)
    shutil.rmtree(run / "round_1")
    manifests = ctx["manifests"]
    release, _, record_hashes = _build_release(tmp_path / "rel2", 2)
    _write_round(
        run, 1, parent_success=set(TASK_IDS[:3]), candidate_success=set(TASK_IDS[3:7]),
        zero_call_parent=set(TASK_IDS[3:16]), zero_call_candidate=set(TASK_IDS[:3]) | set(TASK_IDS[7:16]),
        parent_generation=0, candidate_generation=1,
        manifest_hash=manifests[1]["manifest_hash"],
        task_source_hash=file_hash(release / "B1/deepcoder_taco.jsonl"),
        component="HARNESS", decision="accept_candidate")
    r1 = _rounds(_summarize(run, ctx))[1]
    assert r1["products_complete"] is True
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["zero_call_overlap"] == {"new_success_zero_call": 4, "new_regression_zero_call": 3}


def test_real_single_run_observed_facts_readonly():
    module = _load_summarizer()
    src = Path("/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/"
               "Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_workspace_v1")
    config = Path("/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round/"
                  "reproduction/configs/train_4b_deepcoder_canary_workspace.json")
    if not src.is_dir() or not config.is_file():
        pytest.skip("real single-round canary run/config not available")
    result = module.summarize(src, config_path=config)
    assert result["completed_rounds"] == 1
    assert result["run_complete"] is False
    r1 = _rounds(result)[1]
    assert r1["products_complete"] is True
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["transitions"] == {"new_success": 4, "new_regression": 3, "retained_failure": 9, "retained_success": 0}
    pe, ce = r1["errors"]["parent"], r1["errors"]["candidate"]
    assert pe["error_mentions_all_rows"]["enoent"] == 9
    assert pe["error_mentions_all_rows"]["eagain"] == 6
    assert pe["zero_call_negative"]["enoent"] == 3
    assert pe["zero_call_negative"]["eagain"] == 6
    assert ce["error_mentions_all_rows"]["enoent"] == 8
    assert ce["zero_call_negative"]["enoent"] == 1
    assert ce["zero_call_negative"]["eagain"] == 7
    assert pe["success_with_error"]["enoent"] == 3
    assert r1["calls"]["parent"]["persisted_receipts"] == 51
    assert r1["calls"]["candidate"]["persisted_receipts"] == 93
    assert r1["calls"]["cross_check_ok"] is True
    assert r1["zero_call_overlap"] == {"new_success_zero_call": 4, "new_regression_zero_call": 3}
    assert r1["verification"]["parent"] == {"completed": 16, "valid_answer": 7, "full_verifier": 7, "success": 3}


def test_real_single_run_without_config_blocks_completion():
    module = _load_summarizer()
    src = Path("/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/"
               "Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_workspace_v1")
    if not src.is_dir():
        pytest.skip("real single-round canary run not available")
    result = module.summarize(src)
    assert result["trusted_config"] is False
    assert result["protocol_complete"] is False
    assert result["run_complete"] is False
    r1 = _rounds(result)[1]
    # observed arithmetic preserved even without trusted config
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["calls"]["parent"]["persisted_receipts"] == 51


# --- M1 Markdown -----------------------------------------------------------

def test_m1_markdown_symmetric_and_gates(tmp_path):
    module = _load_summarizer()
    run, _, ctx = _build_run(tmp_path, complete=True)
    result = _summarize(run, ctx)
    text = module._markdown(result)
    assert "verification parent" in text and "verification candidate" in text
    assert "errors parent" in text and "errors candidate" in text
    assert "calls parent" in text and "calls candidate" in text
    assert "active identity" in text
    assert "attempt_count" in text
    assert "snapshot_present" in text
    assert "skills_present" in text and "context_present" in text
    assert "protocol_complete" in text and "external_cleanup" in text