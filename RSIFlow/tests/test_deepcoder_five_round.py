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
# Fixtures mirror the production run layout verified against the real single-round
# canary: side-scoped rollout_cache receipts with binding.task/state_hash/rollout/
# round_scope, per-call receipts with request_sha256/response model identity,
# performance.json with round_id/training_manifest/feedback_role/collection_stage,
# decision/selection with round/attempt binding, task_differences, snapshot receipts
# with combinations/files_sha256, controller_transport compare_scores/activate_task
# receipts, durable jobs with request/result/turn receipts, supervision reconcile,
# finish_experiment turn receipt, and the output-root owned four-GPU service registry.
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
CHECKPOINT = "/models/Qwen3-4B"
VERIFIER_ID = "deepcoder_taco_all_tests_stdio_or_function_v2_tol1e-6"
WEIGHTS = [{"path": "model-00001-of-00001.safetensors", "bytes": 100, "sha256": "w" * 64}]


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _source_hash(task_id):
    return _sha("source:" + task_id)


def _round_scope(round_n, task_id, collection_stage="parent_pre_update"):
    return {"role": "train_evolution", "purpose": "evolution_train", "round_id": round_n,
            "manifest_hash": MANIFEST_HASH, "collection_stage": collection_stage,
            "task_hash": _sha(task_id), "harness_sha256": "h" * 64,
            "checkpoint_manifest": WEIGHTS, "artifacts": [], "protocol_hash": "p" * 64}


def _trajectory(task_id, *, success, calls, round_n, state_hash, branch,
                error=None, execution_error_type="candidate_error",
                infrastructure_error=False, tests_total=11,
                collection_stage="parent_pre_update"):
    called = calls > 0
    verification = {
        "status": "completed", "verifier_id": VERIFIER_ID, "success": bool(success),
        "full_verifier": called,
        "tests_completed": tests_total if called else None,
        "tests_total": tests_total if called else None,
    }
    transport_calls = [
        {"call_id": i, "status": "completed", "messages": [{"role": "user", "content": "q"}],
         "response": {"message": {"role": "assistant", "content": "a"}}}
        for i in range(calls)
    ]
    return {
        "task_id": task_id, "question_id": task_id, "source": "deepcoder_taco",
        "domain": "code", "split": "evolve_train", "rollout_id": 0,
        "state_hash": state_hash, "manifest_hash": MANIFEST_HASH,
        "task_source_hash": _source_hash(task_id), "source_role": "train_evolution",
        "collection_stage": collection_stage, "round_id": round_n,
        "infrastructure_error": infrastructure_error, "infrastructure_failure": infrastructure_error,
        "verification": verification, "valid_answer": called,
        "model_call_count": calls, "transport_calls": transport_calls,
        "execution_error_type": execution_error_type,
        "error_type": "parse_failure" if not called else ("candidate_error" if not success else None),
        "error": error, "terminal_reward": 1.0 if success else 0.0,
        "branch_id": branch, "trajectory_id": _sha(f"{task_id}:{branch}:{round_n}"),
        "model_ref": CHECKPOINT,
    }


def _performance(rows, round_n):
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
        "training_manifest": MANIFEST_HASH, "evaluation_protocol": "same_round_full_B_parent_child_v2",
        "feedback_role": "train_evolution", "collection_stage": collection_stage,
        "probe_identity": _sha(f"probe-{round_n}"),
        "benchmarks": {"deepcoder_taco": {"count": n, "successes": successes, "errors": errors}},
    }


def _call_receipt(task_id, state_hash, round_n, call_id, collection_stage="parent_pre_update"):
    return {
        "binding": {"task": task_id, "task_source_hash": _source_hash(task_id),
                    "state_hash": state_hash, "rollout": 0, "probe": False,
                    "round_scope": _round_scope(round_n, task_id, collection_stage)},
        "call_id": call_id, "status": "completed",
        "request_sha256": _sha(f"req:{task_id}:{round_n}:{call_id}"),
        "request": {"messages": [{"role": "user", "content": "q"}]},
        "response": {"message": {"role": "assistant", "content": "a"}, "usage": {},
                     "binding": {"checkpoint_path": CHECKPOINT, "weights": WEIGHTS},
                     "finish_reason": "stop", "request_id": f"r{call_id}",
                     "model_ref_requested": CHECKPOINT, "model_ref_response": CHECKPOINT,
                     "raw_generation": "a", "wall_time_seconds": 0.1,
                     "serving_endpoint": "http://127.0.0.1:8271", "chat_template_kwargs": {}},
    }


def _write_side(side_dir, cache_root, rows, state_hash, round_n):
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (side_dir / "performance.json").write_text(
        json.dumps(_performance(rows, round_n)), encoding="utf-8")
    cache = cache_root / state_hash / "train_rollouts"
    cache.mkdir(parents=True, exist_ok=True)
    for r in rows:
        (cache / (r["trajectory_id"] + ".json")).write_text(json.dumps({
            "binding": {"task": r["task_id"], "task_source_hash": r["task_source_hash"],
                        "state_hash": state_hash, "rollout": 0, "probe": False,
                        "round_scope": _round_scope(round_n, r["task_id"], r["collection_stage"])},
            "row": {"task_id": r["task_id"], "state_hash": state_hash,
                    "rollout_id": 0, "model_call_count": r["model_call_count"]},
        }), encoding="utf-8")
        if r["model_call_count"]:
            calls_dir = cache / (r["trajectory_id"] + ".calls")
            calls_dir.mkdir(parents=True, exist_ok=True)
            for i in range(r["model_call_count"]):
                (calls_dir / f"{i:04d}.json").write_text(
                    json.dumps(_call_receipt(r["task_id"], state_hash, round_n, i, r["collection_stage"])),
                    encoding="utf-8")


def _transport_receipt(run, identifier, payload):
    directory = run / "controller_transport" / identifier
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _turn_receipt(run, name, payload):
    directory = run / "meta_session/turns" / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "receipt.json").write_text(json.dumps(payload), encoding="utf-8")
    return directory


def _write_round(run, n, *, parent_success, candidate_success,
                 zero_call_parent, zero_call_candidate,
                 parent_generation, candidate_generation,
                 component="HARNESS", decision="accept_candidate",
                 errors_parent=None, errors_candidate=None, infrastructure_error_ids=()):
    errors_parent = errors_parent or {}
    errors_candidate = errors_candidate or {}
    round_dir = run / f"round_{n}"
    parent_state = _sha(f"parent-state-{n}")
    candidate_state = _sha(f"candidate-state-{n}")

    def make_rows(success_set, zero_set, branch, state_hash, errors):
        collection_stage = "parent_pre_update" if branch == "parent" else "child_post_update"
        rows = []
        for tid in TASK_IDS:
            calls = 0 if tid in zero_set else 7
            rows.append(_trajectory(
                tid, success=tid in success_set, calls=calls, round_n=n,
                state_hash=state_hash, branch=branch, error=errors.get(tid),
                infrastructure_error=tid in infrastructure_error_ids,
                collection_stage=collection_stage))
        return rows

    parent_rows = make_rows(parent_success, zero_call_parent, "parent", parent_state, errors_parent)
    candidate_rows = make_rows(candidate_success, zero_call_candidate, "candidate", candidate_state, errors_candidate)
    _write_side(round_dir / "parent", round_dir / "rollout_cache", parent_rows, parent_state, n)
    attempt = round_dir / "attempts/attempt_1"
    _write_side(attempt / "candidate", attempt / "rollout_cache", candidate_rows, candidate_state, n)

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
        "comparison_receipt": f"meta_session/turns/compare_{n}/receipt.json",
        "differences": f"round_{n}/attempts/attempt_1/task_differences/summary.json",
    }), encoding="utf-8")
    (attempt / "skill_use.json").write_text(json.dumps({"used_skill_ids": []}), encoding="utf-8")

    diff_dir = attempt / "task_differences"
    diff_dir.mkdir(parents=True, exist_ok=True)
    (diff_dir / "summary.json").write_text(json.dumps({
        "status": "compared", "paired_tasks": 16, "same_id_set": True,
        "counts": {"retained_failure": retained_failure,
                   "new_success": new_success, "new_regression": new_regression},
        "domain_transitions": {"code": {"retained_failure": retained_failure,
                                        "new_success": new_success, "new_regression": new_regression}},
        "source_mismatches": [], "duplicates": {"before": [], "after": []},
        "missing_in_after": [], "extra_in_after": [], "notes": [],
    }), encoding="utf-8")

    (attempt / "before").mkdir(parents=True, exist_ok=True)
    (attempt / "before/parent_task.json").write_text(json.dumps({
        "generation": parent_generation, "model_ref": CHECKPOINT,
        "harness_path": str(run / f"round_{n}/parent/harness.json"),
        "checkpoint_path": CHECKPOINT, "checkpoint_manifest": WEIGHTS,
    }), encoding="utf-8")
    (attempt / "build").mkdir(parents=True, exist_ok=True)
    (attempt / "build/candidate_state.json").write_text(json.dumps({
        "generation": candidate_generation, "model_ref": CHECKPOINT,
        "harness_path": str(attempt / "build/candidate_bundle.json"),
        "checkpoint_path": CHECKPOINT, "checkpoint_manifest": WEIGHTS,
    }), encoding="utf-8")
    (attempt / "build/candidate_bundle.json").write_text(
        json.dumps({"harness": f"harness_{n}"}), encoding="utf-8")

    snap = run / "snapshots" / f"round_{n}_attempt_1"
    (snap / "task").mkdir(parents=True, exist_ok=True)
    (snap / "task/task_state_snapshot.json").write_text(
        json.dumps({"generation": selected_generation}), encoding="utf-8")
    (snap / "task/parent_state.json").write_text(
        json.dumps({"generation": parent_generation}), encoding="utf-8")
    (snap / "task/candidate_state.json").write_text(
        json.dumps({"generation": candidate_generation}), encoding="utf-8")
    files_sha256 = {
        "task/task_state_snapshot.json": _sha(json.dumps({"generation": selected_generation})),
        "task/parent_state.json": _sha(json.dumps({"generation": parent_generation})),
        "task/candidate_state.json": _sha(json.dumps({"generation": candidate_generation})),
    }
    (snap / "snapshot_receipt.json").write_text(json.dumps({
        "status": "snapshotted", "destination": str(snap),
        "combinations": {
            "parent": {"model": "model_001", "harness": f"harness_{n}",
                       "state_path": str(snap / "task/parent_state.json"),
                       "source_state": str(attempt / "before/parent_task.json")},
            "candidate": {"model": "model_001", "harness": f"harness_{n+1}",
                          "state_path": str(snap / "task/candidate_state.json"),
                          "source_state": str(attempt / "build/candidate_state.json")},
            "selected": {"model": "model_001",
                         "harness": f"harness_{n+1}" if decision == "accept_candidate" else f"harness_{n}",
                         "state_path": str(snap / "task/task_state_snapshot.json"),
                         "source_state": str(run / "active_task.json")},
        },
        "files_sha256": files_sha256,
        "meta": {"id": f"meta_{n+1:03d}", "sha256": _sha(f"meta-{n}"),
                 "path": str(run / f"versions/meta_{n+1:03d}/contents.jsonl")},
        "references": {"context_reference": {"sha256": _sha(f"context-{n}")}},
        "context_reference": {"sha256": _sha(f"context-{n}")},
    }), encoding="utf-8")

    parent_perf_path = str(round_dir / "parent/performance.json")
    candidate_perf_path = str(attempt / "candidate/performance.json")
    facts = {"status": "complete", "total_rollouts": 16, "training_manifest": MANIFEST_HASH,
             "round_id": n, "feedback_role": "train_evolution",
             "evaluation_protocol": "same_round_full_B_parent_child_v2"}
    _transport_receipt(run, f"compare_{n}", {
        "operation": "compare_scores", "status": "compared", "metric": "macro_success",
        "before": before, "after": after, "delta": delta,
        "before_path": parent_perf_path, "after_path": candidate_perf_path,
        "before_sha256": _sha(f"parent-perf-{n}"), "after_sha256": _sha(f"candidate-perf-{n}"),
        "before_facts": facts, "after_facts": facts,
    })
    _turn_receipt(run, f"compare_{n}", {
        "operation": "compare_scores", "status": "compared",
        "before": before, "after": after, "delta": delta,
    })
    if decision == "accept_candidate":
        candidate_state_path = str(attempt / "build/candidate_state.json")
        active_sha = _sha(f"active-{n}")
        _transport_receipt(run, f"activate_{n}", {
            "operation": "activate_task", "status": "activated_as_requested",
            "source_state": candidate_state_path, "active_state": str(run / "active_task.json"),
            "source_sha256": active_sha, "active_sha256": active_sha,
            "selection_note": "fixture accept",
        })
        _turn_receipt(run, f"activate_{n}", {
            "operation": "activate_task", "status": "activated_as_requested",
            "source_state": candidate_state_path, "active_sha256": active_sha,
        })
    _transport_receipt(run, f"snapshot_{n}", {
        "operation": "snapshot_task_meta", "status": "snapshotted",
        "destination": str(snap),
    })
    return {"parent_success": parent_success, "candidate_success": candidate_success,
            "new_success": new_success, "new_regression": new_regression,
            "retained_failure": retained_failure, "delta": delta,
            "parent_generation": parent_generation, "selected_generation": selected_generation}


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
    if write_job:
        (job_dir / "job.json").write_text(json.dumps({
            "job_id": identifier, "request": {"operation": operation},
            "pid": 10000 + abs(hash(identifier)) % 1000, "process_start": None,
            "created_at": 1.0, "turn_dir": str(turn_dir),
            "result_path": str(result_path),
            "stdout_path": str(transport / "stdout.log"),
            "stderr_path": str(transport / "stderr.log"),
        }), encoding="utf-8")
    return result_path


def _build_run(tmp_path, *, complete=False):
    output_root = tmp_path / "output_root"
    run = output_root / "runs" / "run"
    run.mkdir(parents=True)
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
    for n, parent_success, candidate_success, decision in plan:
        parent_generation = generation
        candidate_generation = generation + 1
        specs[n] = _write_round(
            run, n, parent_success=parent_success, candidate_success=candidate_success,
            zero_call_parent=zero_call, zero_call_candidate=zero_call,
            parent_generation=parent_generation, candidate_generation=candidate_generation,
            component="HARNESS", decision=decision)
        generation = specs[n]["selected_generation"]
        _write_job(run, f"run_parent_{n}", "run_parent")
        _write_job(run, f"run_candidate_{n}", "run_candidate")

    final_generation = generation
    (run / "active_task.json").write_text(json.dumps({
        "generation": final_generation, "model_ref": CHECKPOINT,
        "harness_path": str(run / "round_1/attempts/attempt_1/build/candidate_bundle.json"),
        "checkpoint_path": CHECKPOINT, "checkpoint_manifest": WEIGHTS,
        "artifacts": {"directory": None, "manifest": []},
    }), encoding="utf-8")

    a0 = run / "snapshots/A0"
    (a0 / "task").mkdir(parents=True, exist_ok=True)
    (a0 / "task/task_state_snapshot.json").write_text(json.dumps({"generation": 0}), encoding="utf-8")
    (a0 / "snapshot_receipt.json").write_text(json.dumps({
        "status": "snapshotted", "destination": str(a0),
        "combinations": {"selected": {"model": "model_001", "harness": "harness_001",
                                      "state_path": str(a0 / "task/task_state_snapshot.json"),
                                      "source_state": str(run / "active_task.json")}},
        "files_sha256": {"task/task_state_snapshot.json": _sha(json.dumps({"generation": 0}))},
        "meta": {"id": "meta_001", "sha256": _sha("meta-0"),
                 "path": str(run / "versions/meta_001/contents.jsonl")},
        "references": {"context_reference": {"sha256": _sha("context-0")}},
    }), encoding="utf-8")

    meta = run / "meta"
    meta.mkdir(exist_ok=True)
    (meta / "skills.jsonl").write_text(
        json.dumps({"id": "case.HARNESS.x", "type": "case"}) + "\n", encoding="utf-8")
    (meta / "context.json").write_text(
        json.dumps({"round": plan[-1][0], "attempt": 1, "component": "HARNESS"}), encoding="utf-8")

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
    _write_job(run, "bootstrap_0", "bootstrap")
    if complete:
        _turn_receipt(run, "finish", {"operation": "finish_experiment",
                                      "status": "complete", "remaining": {}})
        supervision = session / "supervision" / "0001"
        supervision.mkdir(parents=True, exist_ok=True)
        (supervision / "identity.json").write_text(json.dumps({
            "pid": 14852, "pgid": 14852, "start_ticks": "100",
            "run_dir": str(run), "attempt_dir": str(supervision),
        }), encoding="utf-8")
        (supervision / "reconcile.json").write_text(json.dumps({
            "status": "complete", "term_targets": [], "kill_targets": [], "remaining": [],
        }), encoding="utf-8")
        (supervision / "exit").write_text("0", encoding="utf-8")
        registry = output_root / "services" / "four_gpu_services.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "phase": "services_stopped", "checkpoint": CHECKPOINT,
            "services": [{"gpu": g, "base_url": f"http://127.0.0.1:{8271+g}",
                          "pid": 20000 + g, "identity": {"pid": 20000 + g, "start_ticks": "1", "pgid": 20000 + g},
                          "command": ["python", "start_multidomain_service.py"],
                          "config": str(registry.parent / f"four_gpu/gpu_{g}.json"),
                          "verified_binding": {"checkpoint_path": CHECKPOINT, "weights": WEIGHTS}}
                         for g in range(4)],
            "started_at": 1.0, "verified_at": 2.0, "stopped_at": 3.0,
        }), encoding="utf-8")
    return run, specs


def _rounds(result):
    return {r["round"]: r for r in result["rounds"]}


def _blocker_text(result):
    return (result.get("run_complete_blockers", [])
            + result.get("protocol_complete_blockers", []))


# --- positive controls -----------------------------------------------------

def test_summarizer_two_rounds_denominator_calls_and_transitions(tmp_path):
    summarize = _load_summarizer().summarize
    run, specs = _build_run(tmp_path)
    result = summarize(run)
    assert result["completed_rounds"] == 2
    assert result["run_complete"] is False
    rounds = _rounds(result)
    assert set(rounds) == {1, 2}
    for n, spec in specs.items():
        r = rounds[n]
        assert r["parent"]["denominator"] == 16
        assert r["candidate"]["denominator"] == 16
        assert r["denominator_match"] is True
        assert r["ids_match"] is True
        assert r["source_hashes_match"] is True
        assert r["products_complete"] is True
        assert r["calls"]["parent"]["trajectory_calls"] == r["calls"]["parent"]["persisted_receipts"]
        assert r["calls"]["parent"]["transport_calls"] == r["calls"]["parent"]["trajectory_calls"]
        assert r["calls"]["cross_check_ok"] is True
        assert r["transitions"]["new_success"] == spec["new_success"]
        assert r["transitions"]["new_regression"] == spec["new_regression"]
    assert rounds[1]["delta"] == pytest.approx(0.0625)
    assert rounds[1]["activated"] is True
    assert rounds[2]["selection"]["decision"] == "retain_parent"
    assert rounds[2]["activated"] is False
    assert rounds[2]["delta"] == pytest.approx(0.0)


def test_summarizer_separates_verification_valid_full_and_success(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    r = _rounds(summarize(run))[2]
    v = r["verification"]["parent"]
    assert v["completed"] == 16
    assert v["valid_answer"] == 9
    assert v["full_verifier"] == 9
    assert v["success"] == 4
    assert v["full_verifier"] < v["completed"]
    assert v["success"] <= v["full_verifier"]


def test_summarizer_complete_run_is_legal_and_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, specs = _build_run(tmp_path, complete=True)
    result = summarize(run)
    assert result["completed_rounds"] == 5
    assert result["protocol_complete"] is True
    assert result["run_complete"] is True
    assert result["run_complete_blockers"] == []
    # every accept is strictly positive; final active matches B5 selected generation
    for n, spec in specs.items():
        r = _rounds(result)[n]
        if r["selection"]["decision"] == "accept_candidate":
            assert r["delta"] > 0
    assert result["active_identity"]["generation"] == specs[5]["selected_generation"]


def test_summarizer_component_selection_active_snapshot_skills_context(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["component"] == "HARNESS"
    assert r1["attempt_count"] == 1
    assert r1["snapshot_present"] is True
    assert result["active_identity"]["generation"] == 1
    assert result["skills_present"] is True
    assert result["context_present"] is True


def test_summarizer_missing_b3_reports_two_rounds_not_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    result = summarize(run)
    assert result["completed_rounds"] == 2
    assert result["expected_rounds"] == 5
    assert result["run_complete"] is False
    assert result["protocol_complete"] is False
    assert any("B3" in b for b in _blocker_text(result))


# --- I1: attempt set must be exactly {attempt_1} with round/attempt binding --

def test_i1_extra_attempt_two_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    extra = run / "round_1/attempts/attempt_2"
    extra.mkdir(parents=True)
    (extra / "decision.json").write_text(json.dumps({"round": 1, "attempt": 2}), encoding="utf-8")
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["attempt_count"] == 2
    assert r1["products_complete"] is False
    assert result["run_complete"] is False


def test_i1_only_attempt_two_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    attempts = run / "round_1/attempts"
    (attempts / "attempt_1").rename(attempts / "attempt_2")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False
    assert result["run_complete"] is False


def test_i1_attempt_ten_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    attempts = run / "round_1/attempts"
    (attempts / "attempt_1").rename(attempts / "attempt_10")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


def test_i1_wrong_round_selection_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    selection_path = run / "round_1/attempts/attempt_1/selection.json"
    selection = json.loads(selection_path.read_text())
    selection["round"] = 2
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


# --- I2: frozen 16 unique cohort, hashes, bool success, full verifier --------

def test_i2_fifteen_rows_both_sides_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    for side in ("round_1/parent", "round_1/attempts/attempt_1/candidate"):
        path = run / side / "train_trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        path.write_text("".join(json.dumps(r) + "\n" for r in rows[:-1]), encoding="utf-8")
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["products_complete"] is False
    assert result["run_complete"] is False


def test_i2_duplicate_task_id_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows[-1]["task_id"] = rows[0]["task_id"]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


def test_i2_missing_source_hashes_both_sides_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    for side in ("round_1/parent", "round_1/attempts/attempt_1/candidate"):
        path = run / side / "train_trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        for r in rows:
            r["task_source_hash"] = ""
            r["manifest_hash"] = ""
        path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


def test_i2_string_success_and_pending_verification_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for r in rows:
        r["verification"]["status"] = "pending"
        r["verification"]["success"] = "false"
        r["verification"]["full_verifier"] = False
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["products_complete"] is False
    # string "false" must never be counted as a success
    assert r1["parent"]["successes"] == 0


def test_i2_missing_performance_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "round_1/parent/performance.json").unlink()
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


def test_i2_success_without_full_verifier_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for r in rows:
        if r["verification"]["success"] is True:
            r["verification"]["full_verifier"] = False
            r["verification"]["tests_completed"] = 0
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


# --- I3: per-task/per-call persisted receipt binding, no offsetting ----------

def test_i3_per_task_call_offsetting_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    called = [r for r in rows if r["model_call_count"] > 0]
    called[0]["model_call_count"] += 1
    called[1]["model_call_count"] -= 1
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["calls"]["cross_check_ok"] is False
    assert r1["products_complete"] is False


def test_i3_missing_persisted_call_receipt_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    calls_dir = next(d for d in cache.glob("*.calls") if d.is_dir())
    next(calls_dir.glob("*.json")).unlink()
    result = summarize(run)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_i3_wrong_state_call_binding_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["binding"]["state_hash"] = "f" * 64
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_i3_non_terminal_call_status_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    call_file = next(cache.glob("*.calls/*.json"))
    receipt = json.loads(call_file.read_text())
    receipt["status"] = "running"
    call_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


# --- I4: selection/activation/active chain/snapshot/skills/context/A0 --------

def test_i4_zero_gain_accept_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    # make B5 a zero-gain accept by equalizing candidate success to parent
    attempt = run / "round_5/attempts/attempt_1"
    selection_path = attempt / "selection.json"
    selection = json.loads(selection_path.read_text())
    selection["after"] = selection["before"]
    selection["delta"] = 0.0
    selection["new_success"] = 0
    selection["new_regression"] = 0
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[5]["products_complete"] is False
    assert result["run_complete"] is False


def test_i4_accept_without_activate_receipt_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "controller_transport/activate_1/result.json").unlink()
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["activated"] is False
    assert r1["products_complete"] is False


def test_i4_missing_skills_or_context_blocks_completion(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "meta/skills.jsonl").unlink()
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("skill" in b.lower() for b in _blocker_text(result))


def test_i4_missing_a0_blocks_completion(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    import shutil
    shutil.rmtree(run / "snapshots/A0")
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("A0" in b for b in _blocker_text(result))


def test_i4_corrupt_snapshot_hash_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    receipt_path = run / "snapshots/round_1_attempt_1/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files_sha256"]["task/task_state_snapshot.json"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = summarize(run)
    assert _rounds(result)[1]["products_complete"] is False


def test_i4_missing_active_identity_blocks_completion(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "active_task.json").unlink()
    result = summarize(run)
    assert result["run_complete"] is False
    assert result["active_identity"]["generation"] is None


def test_i4_broken_cross_round_active_chain_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    # B3 parent generation must equal B2 selected; corrupt B3 parent state
    parent_state = run / "round_3/attempts/attempt_1/before/parent_task.json"
    value = json.loads(parent_state.read_text())
    value["generation"] = 99
    parent_state.write_text(json.dumps(value), encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("chain" in b.lower() or "active" in b.lower() for b in _blocker_text(result))


# --- I5: real job results / finish / stage / supervision ---------------------

def test_i5_malformed_job_result_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "controller_transport/run_parent_1/result.json").write_text("{bad json", encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("job" in b.lower() for b in _blocker_text(result))


def test_i5_running_job_result_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    path = run / "controller_transport/run_parent_1/result.json"
    value = json.loads(path.read_text())
    value["status"] = "running"
    path.write_text(json.dumps(value), encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False


def test_i5_missing_job_json_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "meta_session/jobs/run_parent_1/job.json").unlink()
    result = summarize(run)
    assert result["run_complete"] is False


def test_i5_wrong_current_stage_blocks_completion(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "meta_session/RUN_STATUS.md").write_text(
        "Current stage: **B2**\nFinish allowed: **yes**\n", encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("stage" in b.lower() for b in _blocker_text(result))


def test_i5_missing_finish_receipt_blocks_completion(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run / "meta_session/turns/finish/receipt.json").unlink()
    result = summarize(run)
    assert result["run_complete"] is False
    assert any("finish" in b.lower() for b in _blocker_text(result))


def test_i5_unreconciled_supervision_blocks_run_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    reconcile = run / "meta_session/supervision/0001/reconcile.json"
    reconcile.write_text(json.dumps({"status": "pending", "remaining": ["pid:1"]}), encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False


# --- I6: output-root owned registry; protocol_complete vs run_complete -------

def test_i6_registry_not_stopped_blocks_run_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    registry = run.parent.parent / "services/four_gpu_services.json"
    value = json.loads(registry.read_text())
    value["phase"] = "inference_ready"
    registry.write_text(json.dumps(value), encoding="utf-8")
    result = summarize(run)
    assert result["protocol_complete"] is True
    assert result["run_complete"] is False
    assert any("service" in b.lower() or "registry" in b.lower() for b in _blocker_text(result))


def test_i6_missing_registry_blocks_run_complete(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    (run.parent.parent / "services/four_gpu_services.json").unlink()
    result = summarize(run)
    assert result["run_complete"] is False
    assert result["protocol_complete"] is True


def test_i6_wrong_root_registry_is_rejected(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    registry = run.parent.parent / "services/four_gpu_services.json"
    value = json.loads(registry.read_text())
    value["checkpoint"] = "/models/OtherModel"
    registry.write_text(json.dumps(value), encoding="utf-8")
    result = summarize(run)
    assert result["run_complete"] is False


# --- I7: production MODEL/SFT model_update path and evidence -----------------

def test_i7_model_selected_without_evidence_blocks(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    decision_path = run / "round_2/attempts/attempt_1/decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    result = summarize(run)
    assert result["model_sft"]["status"] in ("selected_pending", "selected_failed")
    assert result["run_complete"] is False
    assert _rounds(result)[2]["products_complete"] is False


def test_i7_model_sft_completed_with_production_layout(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    attempt = run / "round_2/attempts/attempt_1"
    decision_path = attempt / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    output = attempt / "candidate"
    (output / "model_update").mkdir(parents=True, exist_ok=True)
    (output / "model_update/training_metrics.json").write_text(json.dumps({
        "optimizer_steps": 5, "warmup_steps": 2, "training_loss": 0.25,
        "ranks": 4, "epochs": 1, "merged": True, "reloaded": True,
        "positive_samples": 4,
    }), encoding="utf-8")
    _transport_receipt(run, "sft_2", {
        "operation": "sft", "status": "completed",
        "candidate_state_path": str(output / "candidate_state.json"),
        "training_metrics_path": str(output / "model_update/training_metrics.json"),
        "checkpoint_path": CHECKPOINT, "positive_samples": 4,
    })
    result = summarize(run)
    assert result["model_sft"]["status"] == "completed"
    assert 2 in result["model_sft"]["rounds"]


def test_i7_model_sft_wrong_path_not_recorded(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path, complete=True)
    attempt = run / "round_2/attempts/attempt_1"
    decision_path = attempt / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    # metrics at candidate root (wrong; production uses model_update/) must not certify
    (attempt / "candidate/training_metrics.json").write_text(
        json.dumps({"optimizer_steps": 5, "warmup_steps": 2, "training_loss": 0.25}), encoding="utf-8")
    result = summarize(run)
    assert result["model_sft"]["status"] != "completed"
    assert result["run_complete"] is False


def test_summarizer_model_sft_not_selected_without_model_round(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    result = summarize(run)
    assert result["model_sft"]["status"] == "not_selected"
    assert result["model_sft"]["rounds"] == []


# --- I8: error mention layering and per-task transition attribution ----------

def test_i8_error_layering_fixture(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    # parent: 7 zero-call rows -> ENOENT on 3, EAGAIN on 4; called ENOENT on a success.
    # all-row ENOENT (4) must differ from the zero-call subset (3); the called success
    # carrying ENOENT must not be miscounted as a zero-call process failure.
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
    result = summarize(run)
    errs = _rounds(result)[1]["errors"]["parent"]
    assert errs["error_mentions_all_rows"]["enoent"] == 4
    assert errs["error_mentions_all_rows"]["eagain"] == 4
    assert errs["zero_call_negative"]["count"] == 7
    assert errs["zero_call_negative"]["enoent"] == 3
    assert errs["zero_call_negative"]["eagain"] == 4
    assert errs["called_with_error"]["enoent"] == 1
    assert errs["success_with_error"]["enoent"] == 1


def test_i8_real_single_run_errno_layering_readonly():
    summarize = _load_summarizer().summarize
    src = Path("/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/"
               "Rollout_logs/deepcoder_canary_16_v1/runs/deepcoder_function_16_canary_workspace_v1")
    if not src.is_dir():
        pytest.skip("real single-round canary run not available")
    result = summarize(src)
    assert result["completed_rounds"] == 1
    assert result["run_complete"] is False
    r1 = _rounds(result)[1]
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["transitions"] == {"new_success": 4, "new_regression": 3,
                                 "retained_failure": 9, "retained_success": 0}
    pe = r1["errors"]["parent"]
    ce = r1["errors"]["candidate"]
    assert pe["error_mentions_all_rows"]["enoent"] == 9
    assert pe["error_mentions_all_rows"]["eagain"] == 6
    assert pe["zero_call_negative"]["enoent"] == 3
    assert pe["zero_call_negative"]["eagain"] == 6
    assert ce["error_mentions_all_rows"]["enoent"] == 8
    assert ce["error_mentions_all_rows"]["eagain"] == 7
    assert ce["zero_call_negative"]["enoent"] == 1
    assert ce["zero_call_negative"]["eagain"] == 7
    assert pe["success_with_error"]["enoent"] == 3
    assert r1["calls"]["parent"]["persisted_receipts"] == 51
    assert r1["calls"]["candidate"]["persisted_receipts"] == 93
    assert r1["calls"]["cross_check_ok"] is True
    # zero-call process anomalies overlap the outcome transitions (causal attribution limited)
    assert r1["zero_call_overlap"] == {"new_success_zero_call": 4, "new_regression_zero_call": 3}
    # verification completed / valid answer / full verifier / success stay separated
    assert r1["verification"]["parent"] == {"completed": 16, "valid_answer": 7,
                                            "full_verifier": 7, "success": 3}
    assert r1["verification"]["candidate"]["success"] == 4
    assert r1["errors"]["parent"]["infrastructure_error_true"] == 0
    assert r1["products_complete"] is True


def test_i8_realistic_zero_call_overlap_accept_fixture(tmp_path):
    summarize = _load_summarizer().summarize
    run, _ = _build_run(tmp_path)
    # Mirror the real canary: new successes are parent zero-call failures that the
    # candidate calls and solves; regressions are parent called successes that the
    # candidate fails pre-model (zero calls). Positive gain, accept, overlap 4/3.
    import shutil
    shutil.rmtree(run / "round_1")  # fresh round so no stale per-call receipts remain
    _write_round(
        run, 1,
        parent_success=set(TASK_IDS[:3]), candidate_success=set(TASK_IDS[3:7]),
        zero_call_parent=set(TASK_IDS[3:16]),
        zero_call_candidate=set(TASK_IDS[:3]) | set(TASK_IDS[7:16]),
        parent_generation=0, candidate_generation=1,
        component="HARNESS", decision="accept_candidate")
    result = summarize(run)
    r1 = _rounds(result)[1]
    assert r1["products_complete"] is True
    assert r1["delta"] == pytest.approx(0.0625)
    assert r1["activated"] is True
    assert r1["transitions"]["new_success"] == 4
    assert r1["transitions"]["new_regression"] == 3
    assert r1["zero_call_overlap"] == {"new_success_zero_call": 4, "new_regression_zero_call": 3}
    # per-task transition details carry both sides' calls and errno for attribution
    details = {d["task_id"]: d for d in r1["task_transitions"]}
    new_success_task = TASK_IDS[3]
    assert details[new_success_task]["transition"] == "new_success"
    assert details[new_success_task]["parent_calls"] == 0
    assert details[new_success_task]["candidate_calls"] > 0
    regression_task = TASK_IDS[0]
    assert details[regression_task]["transition"] == "new_regression"
    assert details[regression_task]["parent_calls"] > 0
    assert details[regression_task]["candidate_calls"] == 0


# --- M1: Markdown symmetric parent/candidate facts ---------------------------

def test_m1_markdown_shows_both_sides_and_gates(tmp_path):
    module = _load_summarizer()
    run, _ = _build_run(tmp_path, complete=True)
    result = module.summarize(run)
    text = module._markdown(result)
    assert "candidate" in text.lower()
    assert "verification parent" in text and "verification candidate" in text
    assert "errors parent" in text and "errors candidate" in text
    assert "active" in text.lower()
    assert "attempt" in text.lower()
    assert "snapshot" in text.lower()
    assert "skills" in text.lower()
    assert "context" in text.lower()
    assert "protocol_complete" in text