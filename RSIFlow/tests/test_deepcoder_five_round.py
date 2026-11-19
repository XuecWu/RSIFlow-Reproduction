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


def test_f7_production_transport_resume_after_sft_no_retrain(tmp_path, monkeypatch):
    # F7: a controlled Codex app-server/transport drives the REAL PersistentMeta resume
    # from the next milestone after a successful SFT. The training-dispatch spy (the sft
    # operation entry) fails on re-call and must stay calls=0; child/receipt bytes/hash
    # unchanged; no repeat A0/parent. Not a helper-only self-proof.
    import persistent_meta

    dispatch = {"sft_calls": 0, "armed": False}

    class SpyExecutor(_FiveRoundOfflineExecutor):
        def execute(self, request):
            if request["operation"] == "sft":
                dispatch["sft_calls"] += 1
                if dispatch["armed"]:
                    raise AssertionError("production training dispatch re-called on resume")
            return super().execute(request)

    project = tmp_path / "project"; project.mkdir()
    run = tmp_path / "run"
    _write_five_round_policy(run)
    loop = PersistentMeta(
        project=project, run=run, codex_home=run / "meta_session/codex_home",
        executor=SpyExecutor(),
        meta_config={"model": "offline-model", "provider": "offline-provider"}, rounds=5,
    )

    def complete_job(request, turn_dir):
        return {"status": "completed", "result": loop.tools.executor.execute(request)}

    loop.tools.jobs.start = complete_job
    _complete_five_round_tools_a0(loop)
    # Round 1 through SFT only (run_parent, prepare_meta_evidence, decision, sft): the
    # training product exists but candidate_rollout/compare/selection are NOT yet done.
    _record_five_round_tools(loop, 1, steps=4)
    assert dispatch["sft_calls"] == 1
    sft_receipt = json.loads((run / "round_1/attempts/attempt_1/candidate_state.json").read_text()) \
        if (run / "round_1/attempts/attempt_1/candidate_state.json").is_file() else None
    # capture the training product bytes/hash produced by the offline sft dispatch
    candidate_path = run / "candidate.json"
    before_bytes = candidate_path.read_bytes() if candidate_path.is_file() else b""
    import hashlib as _hl
    before_hash = _hl.sha256(before_bytes).hexdigest()
    dispatch["armed"] = True  # any further sft dispatch now fails
    dispatch["sft_calls"] = 0

    (run / "meta_session/state.json").write_text(json.dumps({
        "transport": "app_server", "status": "disconnected",
        "thread_id": "thread-sft-resume", "sandbox_backend": "legacy_host",
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
            # Continue round 1 from the NEXT milestone after sft (candidate_rollout onward),
            # driving the real controller tools; never re-dispatch sft.
            _record_five_round_tools(loop, 1, skip=4)
            loop.tools.finished = True
            loop.tools.summary = "offline resume complete"
            return {"id": f"turn-{len(captured['turns'])}", "status": "completed"}

        def close(self):
            pass

    monkeypatch.setattr(persistent_meta, "CodexAppServer", Server)
    monkeypatch.setattr(persistent_meta.time, "sleep", lambda seconds: None)
    result = loop.run_experiment(initial)

    assert result["status"] == "complete"
    assert captured["requests"][0][0] == "thread/resume"
    assert captured["requests"][0][1]["threadId"] == "thread-sft-resume"
    # production training dispatch was NOT re-called; child/receipt bytes/hash unchanged
    assert dispatch["sft_calls"] == 0
    after_bytes = candidate_path.read_bytes() if candidate_path.is_file() else b""
    assert after_bytes == before_bytes
    assert _hl.sha256(after_bytes).hexdigest() == before_hash
    # no repeat A0/parent: bootstrap once, run_parent once (round 1 only)
    commands = [json.loads(p.read_text()) for p in (run / "meta_session/turns").glob("*/command.json")]
    operations = [c["request"]["operation"] for c in commands]
    assert operations.count("bootstrap") == 1
    assert operations.count("run_parent") == 1
    assert operations.count("sft") == 1  # the original round-1 dispatch, not a retrain


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
    value = _five_round_launcher_value(base)
    value.update(output_root=str(output_root), run_name=run_name, frozen_data_dir=str(release))
    config_path = base / "machine.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    return config_path


def _fixture_protocol(config_path):
    from evolution_protocol import fingerprint
    cfg = TaskAdapter(ROOT, Path(config_path).parent / "receipts")._config({"config_path": str(config_path)})
    return fingerprint({"runtime_config": cfg.model_dump(), "stage": "train_evolution_v1"})


def _make_scope(round_n, manifest_hash, collection_stage, state, protocol_hash):
    from rsiflow.task_meta.durable import task_hash, load_task
    return {"role": "train_evolution", "purpose": "evolution_train", "round_id": round_n,
            "manifest_hash": manifest_hash, "collection_stage": collection_stage,
            "task_hash": task_hash(load_task(state)),
            "harness_sha256": file_hash(Path(state["harness_path"])),
            "checkpoint_manifest": state["checkpoint_manifest"],
            "artifacts": _artifact_manifest_fixture(state["artifacts"]["directory"]),
            "protocol_hash": protocol_hash}


def _artifact_manifest_fixture(directory):
    if directory is None or not Path(directory).exists():
        return []
    root = Path(directory)
    return [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
             "sha256": file_hash(p)} for p in sorted(root.rglob("*")) if p.is_file()]


def _fingerprint_fixture(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _transport_call(call_id, weights=WEIGHTS, model_ref=CHECKPOINT):
    return {
        "call_id": call_id, "status": "completed",
        "messages": [{"role": "user", "content": f"q{call_id}"}],
        "response": {"message": {"role": "assistant", "content": f"a{call_id}"}, "usage": {},
                     "binding": {"checkpoint_path": model_ref, "weights": weights},
                     "finish_reason": "stop", "request_id": f"r{call_id}",
                     "model_ref_requested": model_ref, "model_ref_response": model_ref},
        "seed": 42, "temperature": 0.7, "tools": [], "max_tokens": 2048,
        "chat_template_kwargs": {}, "usage_complete": True, "unknown_usage_calls": 0,
    }


def _trajectory(task_id, *, success, calls, round_n, state_hash, branch, manifest_hash,
                task_source_hash, error=None, execution_error_type="candidate_error",
                infrastructure_error=False, tests_total=11, collection_stage="parent_pre_update",
                scope=None, checkpoint_manifest=WEIGHTS, model_ref=CHECKPOINT):
    called = calls > 0
    verification = {
        "status": "completed", "verifier_id": VERIFIER_ID, "success": bool(success),
        "full_verifier": called,
        "tests_completed": tests_total if called else None,
        "tests_total": tests_total if called else None,
    }
    scope = scope or {}
    return {
        "task_id": task_id, "question_id": task_id, "source": "deepcoder_taco",
        "domain": "code", "split": "evolve_train", "rollout_id": 0,
        "state_hash": state_hash, "manifest_hash": manifest_hash,
        "task_source_hash": task_source_hash, "source_role": "train_evolution", "purpose": "evolution_train",
        "collection_stage": collection_stage, "round_id": round_n,
        "infrastructure_error": infrastructure_error, "infrastructure_failure": infrastructure_error,
        "verification": verification, "valid_answer": called,
        "model_call_count": calls,
        "transport_calls": [_transport_call(i, checkpoint_manifest, model_ref) for i in range(calls)],
        "execution_error_type": execution_error_type,
        "error_type": "parse_failure" if not called else ("candidate_error" if not success else None),
        "error": error, "terminal_reward": 1.0 if success else 0.0,
        "branch_id": branch,
        "trajectory_id": _fingerprint_fixture([scope, task_id, 0]),
        "harness_hash": scope.get("harness_sha256"), "harness_sha256": scope.get("harness_sha256"),
        "parent_policy_hash": _fingerprint_fixture(checkpoint_manifest),
        "success_verifier_version": scope.get("protocol_hash"),
        "model_ref": model_ref, "artifact_input_manifest": [],
        "messages": [{"role": "user", "content": task_id}, {"role": "assistant", "content": "answer"}],
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


def _call_receipt(task_id, state_hash, transport_call, scope, task_record, task_source_hash):
    request = {k: v for k, v in transport_call.items() if k != "response"}
    request.update(status="started", usage_complete=False, unknown_usage_calls=1)
    return {
        "binding": {"task": task_id, "task_source_hash": task_source_hash,
                    "state_hash": state_hash, "rollout": 0, "probe": False,
                    "round_scope": scope, "task_record": task_record},
        "call_id": transport_call["call_id"], "status": "completed",
        "request_sha256": _value_hash(request), "request": request,
        "response": transport_call["response"],
    }


def _write_side(side_dir, cache_root, rows, state_hash, round_n, manifest_hash, scope, manifest):
    side_dir.mkdir(parents=True, exist_ok=True)
    (side_dir / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from rsiflow.task_meta.storage import save_json
    save_json(side_dir / "performance.json", _performance(rows, round_n, manifest_hash))
    members = {t["task_id"]: t for t in manifest["tasks"]}
    cache = cache_root / state_hash / "train_rollouts"
    cache.mkdir(parents=True, exist_ok=True)
    for r in rows:
        task_record = members[r["task_id"]]["content_hash"]
        (cache / (r["trajectory_id"] + ".json")).write_text(json.dumps({
            "binding": {"task": r["task_id"], "task_source_hash": r["task_source_hash"],
                        "state_hash": state_hash, "rollout": 0, "probe": False,
                        "round_scope": scope, "task_record": task_record},
            "row": r,
        }), encoding="utf-8")
        if r["model_call_count"]:
            calls_dir = cache / (r["trajectory_id"] + ".calls")
            calls_dir.mkdir(parents=True, exist_ok=True)
            for tc in r["transport_calls"]:
                (calls_dir / f"{tc['call_id']:04d}.json").write_text(json.dumps(
                    _call_receipt(r["task_id"], state_hash, tc, scope, task_record,
                                  r["task_source_hash"])), encoding="utf-8")


def _task_state(generation, harness_path):
    return {"generation": generation, "model_ref": CHECKPOINT, "checkpoint_path": CHECKPOINT,
            "checkpoint_manifest": WEIGHTS, "harness_path": str(harness_path),
            "artifacts": {"directory": None, "manifest": []}}


def _harness_file(run, generation):
    path = run / "harness" / f"gen_{generation}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"harness_generation": generation}), encoding="utf-8")
    return path


def _fixture_snapshot(run, snap, parent, candidate, selected, *, attempt=None, parent_source=None):
    from component_versions import ComponentVersions, snapshot
    import shutil
    if snap.exists():
        shutil.rmtree(snap)
    meta = run / "snapshot_inputs" / snap.name
    meta.mkdir(parents=True, exist_ok=True)
    skills = meta / "skills.jsonl"
    skills.write_text("" if snap.name == "A0" else json.dumps({"id": snap.name}) + "\n")
    context = meta / "context.json"
    context.write_text(json.dumps({"snapshot": snap.name}))
    active = run / "active_task.json"
    active.write_text(json.dumps(selected))
    states = {"selected": active}
    if parent is not None:
        attempt = attempt or run / snap.name.split("_attempt")[0] / "attempts/attempt_1"
        states.update(parent=parent_source or attempt / "before/parent_task.json",
                      candidate=attempt / "build/candidate_state.json")
    return snapshot(ComponentVersions(run / "versions", ROOT), snap, states, skills,
                    {"context_path": str(context), "context_reference": {
                        "source": str(context), "path": str(context), "sha256": file_hash(context)}})


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
    (transport / "request.json").write_text(json.dumps({"operation": operation}))
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
                 parent_generation, candidate_generation, manifest, protocol_hash,
                 component="HARNESS", decision="accept_candidate",
                 errors_parent=None, errors_candidate=None, infrastructure_error_ids=()):
    errors_parent = errors_parent or {}
    errors_candidate = errors_candidate or {}
    manifest_hash = manifest["manifest_hash"]
    task_source_hash = manifest["tasks"][0]["record_sha256"]
    round_dir = run / f"round_{n}"
    attempt = round_dir / "attempts/attempt_1"
    parent_harness = _harness_file(run, parent_generation)
    candidate_harness = attempt / "build/candidate_bundle.json"
    candidate_harness.parent.mkdir(parents=True, exist_ok=True)
    candidate_harness.write_text(json.dumps({"harness_generation": candidate_generation}), encoding="utf-8")
    parent_state = _task_state(parent_generation, parent_harness)
    candidate_state = _task_state(candidate_generation, candidate_harness)
    parent_scope = _make_scope(n, manifest_hash, "parent_pre_update", parent_state, protocol_hash)
    candidate_scope = _make_scope(n, manifest_hash, "child_post_update", candidate_state, protocol_hash)
    parent_state_hash = _fingerprint_fixture(parent_scope)
    candidate_state_hash = _fingerprint_fixture(candidate_scope)

    def make_rows(success_set, zero_set, branch, state_hash, errors, collection_stage, scope):
        rows = []
        for tid in TASK_IDS:
            calls = 0 if tid in zero_set else 7
            rows.append(_trajectory(
                tid, success=tid in success_set, calls=calls, round_n=n,
                state_hash=state_hash, branch=branch, manifest_hash=manifest_hash,
                task_source_hash=task_source_hash, error=errors.get(tid),
                infrastructure_error=tid in infrastructure_error_ids,
                collection_stage=collection_stage, scope=scope))
        return rows

    parent_rows = make_rows(parent_success, zero_call_parent, "parent", parent_state_hash, errors_parent, "parent_pre_update", parent_scope)
    candidate_rows = make_rows(candidate_success, zero_call_candidate, "candidate", candidate_state_hash, errors_candidate, "child_post_update", candidate_scope)
    _write_side(round_dir / "parent", round_dir / "rollout_cache", parent_rows, parent_state_hash, n, manifest_hash, parent_scope, manifest)
    _write_side(attempt / "candidate", attempt / "rollout_cache", candidate_rows, candidate_state_hash, n, manifest_hash, candidate_scope, manifest)

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
    _fixture_snapshot(run, snap, parent_state, candidate_state, selected_state)

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


def _fixture_journal(run, config_path, complete):
    """Use real commands/receipts and the production progress rebuilder in tmp."""
    from experiment_progress import ExperimentProgress
    import shutil
    turns = run / "meta_session/turns"
    shutil.rmtree(turns, ignore_errors=True)
    _TURN_COUNTER[0] = 0
    config = json.loads(Path(config_path).read_text())
    policy = {k: config[k] for k in ("rounds", "experiment_scope", "retry_rejected_from_round", "evaluation_schedule")}
    (run / "meta_session/workflow_policy.json").write_text(json.dumps(policy))

    def emit(op, number, request, receipt, job=None):
        turn = _turn_receipt(run, op, number, receipt)
        request = {"operation": op, "round_number": number, **request}
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        if job:
            transport = run / "controller_transport" / job
            transport.mkdir(exist_ok=True)
            (transport / "request.json").write_text(json.dumps(request))
            (transport / "result.json").write_text(json.dumps({"operation": op, **receipt}))
            path = run / "meta_session/jobs" / job / "job.json"
            old = json.loads(path.read_text()) if path.exists() else {}
            old.update(job_id=job, request=request, pid=10000 + len(list((run / "meta_session/jobs").glob("*/job.json"))),
                       process_start="100", turn_dir=str(turn), result_path=str(transport / "result.json"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(old))
        return turn

    emit("bootstrap", 0, {"config_path": str(config_path)},
         {"status": "completed", "state_path": str(run / "active_task.json")}, "bootstrap_0")
    a0 = run / "snapshots/A0/snapshot_receipt.json"
    emit("snapshot_task_meta", 0, {"destination": str(a0.parent)}, json.loads(a0.read_text()))
    for n in range(1, 6):
        rd = run / f"round_{n}"
        if not rd.exists():
            break
        at = rd / "attempts/attempt_1"
        common = {"config_path": str(config_path)}
        component = json.loads((at / "decision.json").read_text())["component"]
        direct = component == "ARTIFACTS"
        production_model = component == "MODEL" and (at / "candidate_rollout").is_dir()
        production = direct or production_model
        parent_state = (run / "snapshots/A0/task/task_state_snapshot.json" if direct and n == 1 else
                        run / f"snapshots/B{n - 1}_attempt_1/task/task_state_snapshot.json" if production else
                        at / "before/parent_task.json")
        candidate_dir = at / ("candidate_rollout" if production else "candidate")
        parent_command_state = parent_state
        if production:
            parent_snapshot = json.loads((parent_state.parent.parent / "snapshot_receipt.json").read_text())
            parent_command_state = parent_snapshot["combinations"]["selected"]["source_state"]
        for op, state, output in (("run_parent", parent_command_state, rd / "parent"),):
            parent_receipt = {"status": "completed", "state_path": str(state), "output_dir": str(output),
                              "performance_path": str(output / "performance.json")}
            if production:
                performance = json.loads((output / "performance.json").read_text())
                parent_receipt.update(trajectories_path=str(output / "train_trajectories.jsonl"),
                                      tasks_observed=16, macro_success=performance["macro_success"],
                                      collection_stage="parent_pre_update")
            emit(op, n, {**common, "state_path": str(state), "output_dir": str(output)},
                 parent_receipt, f"{op}_{n}")
        ev = rd / "evidence/statistics.json"
        ev.parent.mkdir(exist_ok=True); ev.write_text("{}")
        emit("prepare_meta_evidence", n, {"output_dir": str(ev.parent)},
             {"status": "completed", "statistics_path": str(ev)})
        emit("write_text", n, {"path": str(at / "decision.json")},
             {"status": "written", "path": str(at / "decision.json")})
        if component == "MODEL":
            result = json.loads((run / f"controller_transport/sft_{n}/result.json").read_text())
            sft_state = (run / f"round_{n - 1}/attempts/attempt_1/build/candidate_state.json"
                         if production_model else at / "before/parent_task.json")
            sft_output = at / ("build" if production_model else "candidate")
            emit("sft", n, {**common, "state_path": str(sft_state),
                            "parent_rollout_dir": str(rd / "parent"), "output_dir": str(sft_output)},
                 result, f"sft_{n}")
        else:
            emit("rsiflow", n, {"output_dir": str(at / "build")},
                 {"status": "completed", "candidate_state_path": str(at / "build/candidate_state.json")})
        candidate_receipt = {"status": "completed", "state_path": str(at / "build/candidate_state.json"),
                             "output_dir": str(candidate_dir),
                             "performance_path": str(candidate_dir / "performance.json")}
        if production:
            performance = json.loads((candidate_dir / "performance.json").read_text())
            candidate_receipt.update(trajectories_path=str(candidate_dir / "train_trajectories.jsonl"),
                                     tasks_observed=16, macro_success=performance["macro_success"],
                                     collection_stage="child_post_update")
        emit("run_candidate", n, {**common, "state_path": str(at / "build/candidate_state.json"),
                                 "output_dir": str(candidate_dir)}, candidate_receipt, f"run_candidate_{n}")
        compare = run / f"controller_transport/compare_{n}/result.json"
        emit("compare_scores", n, {"before": str(rd / "parent/performance.json"),
                                   "after": str(candidate_dir / "performance.json")}, json.loads(compare.read_text()))
        sel = json.loads((at / "selection.json").read_text())
        choice = sel.get("decision", sel.get("selection"))
        if "decision" in sel:
            # Production progress recognizes the explicit accept_or_retain field.
            sel["accept_or_retain"] = choice
            (at / "selection.json").write_text(json.dumps(sel))
        emit("write_text", n, {"path": str(at / "selection.json"), "content": json.dumps(sel)},
             {"status": "written", "path": str(at / "selection.json")})
        if choice in {"accept", "accept_candidate"}:
            act = run / f"controller_transport/activate_{n}/result.json"
            emit("activate_task", n, {"source": str(at / "build/candidate_state.json")}, json.loads(act.read_text()))
        emit("append_skills", n, {"path": str(run / "meta/skills.jsonl")},
             {"status": "appended", "appended_count": 1, "path": str(run / "meta/skills.jsonl")})
        emit("write_text", n, {"path": str(run / "meta/context.json")},
             {"status": "written", "path": str(run / "meta/context.json")})
        snap_name = f"B{n}_attempt_1" if production else f"round_{n}_attempt_1"
        snap = run / f"snapshots/{snap_name}/snapshot_receipt.json"
        emit("snapshot_task_meta", n, {"destination": str(snap.parent)}, json.loads(snap.read_text()))
    if complete:
        emit("finish_experiment", 5, {}, {"status": "complete", "remaining": {}})
    ExperimentProgress(run / "meta_session", rounds=5).rebuild()


def _fixture_launch_chain(attempt, binding, identities, expected_gpus, *, seal=lambda p: None):
    """Synthetic immutable evidence, using the same schema as the real collector.
    Process observation remains the responsibility of the caller's test boundary.
    """
    import sys
    scripts = ROOT.parent / "reproduction/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from deepcoder_five_round_supervisor import host_identity
    h = host_identity()
    binding = {**binding, "backend": "linux_bwrap_seccomp_v1", "mode": "fresh"}
    common = {k: binding[k] for k in ("config_path", "config_hash", "run_dir", "supervision_attempt")}
    def write(name, value):
        path = attempt / name
        path.write_text(json.dumps(value))
        seal(path)
        return file_hash(path)
    pre = write("prelaunch-seal.json", {"kind": "rsiflow_prelaunch_seal", "binding": common,
                "host_identity": h, "expected_gpus": expected_gpus})
    launch_hash = write("launch.json", {"kind": "rsiflow_launch", "binding": binding,
                  "prelaunch_hash": pre,
                  "supervisor": {"pid": identities["launcher"]["pgid"], "pgid": identities["launcher"]["pgid"], "start_ticks": "100"}})
    runtime_hash = write("post-launch-identity.json", {"kind": "rsiflow_post_launch_identity", **common,
                        "launch_hash": launch_hash, "host_identity": h, "thread_id": binding["thread_id"],
                        "owners": identities, "expected_gpus": expected_gpus})
    write("launch-final.json", {"kind": "rsiflow_launch_final", **common, "runtime_hash": runtime_hash})
    return binding, h


def _cleanup_receipt(run, output_root, config_path, *, status="completed", attempt="0001",
                     run_binding=None, registry_phase="services_stopped", compute_apps=None,
                     free_verified=True, owners_exited=True):
    # A consumer fixture, not a production cleanup writer: all process/GPU
    # observations below are synthetic tmp files and no live inspection occurs.
    directory = run / "meta_session/supervision" / attempt / "external-cleanup/obs1"
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {}

    def seal(path):
        path = Path(path)
        evidence[str(path)] = file_hash(path)
        return str(path), evidence[str(path)]

    def write(path, value):
        path.write_text(json.dumps(value))
        return seal(path)

    actual_config = config_path or run.parent.parent.parent / "machine.json"
    config = json.loads(Path(actual_config).read_text())
    base = (ROOT / config.get("runtime_source", "runtime") / config["base_config"]).resolve()
    effective_path = directory / "effective.json"
    effective = TaskAdapter(ROOT, directory)._config({"config_path": str(actual_config)}).model_dump()
    write(effective_path, effective)
    binding = {"config_path": str(actual_config), "config_hash": file_hash(Path(actual_config)),
               "base_config_path": str(base), "base_config_hash": file_hash(base),
               "effective_config_path": str(effective_path), "effective_config_hash": file_hash(effective_path),
               "run_dir": run_binding or str(run), "output_root": str(output_root),
               "supervision_attempt": attempt, "thread_id": "thread-1"}
    seal(actual_config); seal(base); seal(run / "meta_session/state.json")
    expected_gpus = [{"gpu": g, "uuid": f"GPU-{g}"} for g in range(4)]
    identities = {"launcher": {"pid": 14871, "pgid": 14852, "start_ticks": "100"},
                  "app_server": {"pid": 14896, "pgid": 14896, "start_ticks": "101"}}
    launch_path = run / "meta_session/supervision" / attempt / "launch.json"
    binding, observed_host = _fixture_launch_chain(launch_path.parent, binding, identities, expected_gpus, seal=seal)
    binding = {**binding, "launch_receipt_path": str(launch_path), "launch_receipt_hash": file_hash(launch_path)}
    history = []
    for sup in sorted((run / "meta_session/supervision").iterdir()):
        if not sup.name.isdigit():
            continue
        item = {"attempt": sup.name}
        for name, filename in (("identity", "identity.json"), ("exit", "exit"), ("reconcile", "reconcile.json")):
            item[name + "_path"], item[name + "_hash"] = seal(sup / filename)
        history.append(item)

    def owner(identity, source, label):
        exit_state = "exited" if owners_exited else "alive"
        obs = {"kind": "rsiflow_owner_exit", "run_dir": str(run), "identity": identity,
               "state": exit_state, "group_members": [], "read_errors": [], "observed_at": "2026-11-18T00:01:00Z"}
        obs_path, digest = write(directory / (label + ".json"), obs)
        seal(source)
        return {"identity": identity, "source_path": str(source), "state": exit_state,
                "evidence_path": obs_path, "evidence_sha256": digest}

    owners = {kind: owner(identity, launch_path, kind) for kind, identity in identities.items()}
    owners["jobs"] = []
    for i, jobpath in enumerate(sorted((run / "meta_session/jobs").glob("*/job.json"))):
        job = json.loads(jobpath.read_text())
        ident = {"pid": job["pid"], "pgid": job["pid"], "start_ticks": str(job["process_start"])}
        owners["jobs"].append(owner(ident, jobpath, f"job_{i}"))
    registry_path = output_root / "services/four_gpu_services.json"
    registry = json.loads(registry_path.read_text())
    active = json.loads((run / "active_task.json").read_text())
    owners["inference_services"] = [owner(service["identity"], registry_path, f"service_{i}")
                                    for i, service in enumerate(registry["services"])]
    owners["training_phases"] = []
    for i, phase in enumerate(sorted(run.glob("round_*/attempts/attempt_*/**/gpu_phase_receipt.json"))):
        value = json.loads(phase.read_text())
        identity = {"pid": value["training_pid"], "pgid": value["training_pid"], "start_ticks": "150"}
        owners["training_phases"].append(owner(identity, phase, f"training_{i}"))
    import hashlib as _hl
    raw = "0, GPU-0, A800, 81920, 80000, 100, 0\n1, GPU-1, A800, 81920, 80000, 100, 0\n" \
          "2, GPU-2, A800, 81920, 80000, 100, 0\n3, GPU-3, A800, 81920, 80000, 100, 0\n"
    apps = compute_apps if compute_apps is not None else []
    apps_raw = "".join(f"{a['pid']}, {a.get('uuid', '')}\n" for a in apps)
    gpu = {"time": "2026-11-18T00:01:00Z", "command": ["nvidia-smi", "--query"], "command_result": 0,
           "expected_uuids": [g["uuid"] for g in expected_gpus],
           "per_gpu": [{"gpu": g, "uuid": f"GPU-{g}", "memory_free": 80000, "memory_total": 81920,
                        "memory_used": 100, "utilization": 0} for g in range(4)],
           "compute_apps": apps, "free_verified": free_verified,
           "raw_stdout": raw, "stdout_hash": _hl.sha256(raw.encode()).hexdigest(),
           "apps_command": ["nvidia-smi", "--query-compute-apps=pid,uuid"],
           "apps_raw_stdout": apps_raw, "apps_stdout_hash": _hl.sha256(apps_raw.encode()).hexdigest()}
    gpu_observations = []
    for index in range(2):
        # F6: independent observations with distinct timestamps (valid interval).
        obs = {**gpu, "time": f"2026-11-18T00:01:{index * 2:02d}Z"}
        gpu_path, gpu_hash = write(directory / f"gpu_observation_{index}.json", obs)
        gpu_observations.append({**obs, "evidence_path": gpu_path, "evidence_sha256": gpu_hash})
    ports_path, ports_hash = write(directory / "ports_observation.json", {"status": "closed", "closed_ports": [8271, 8272, 8273, 8274]})
    receipt = {"schema_version": 1, "kind": "rsiflow_external_cleanup", "status": status,
               "started_at_utc": "2026-11-18T00:00:00Z", "observed_at_utc": "2026-11-18T00:02:00Z",
               "binding": binding, "host_identity": observed_host, "supervision": {"all_previous_attempts_accounted_for": True, "attempts": history},
               "registry": {"path": str(registry_path), "after_hash": file_hash(registry_path),
                            "phase": registry_phase, "checkpoint": active["checkpoint_path"], "weights": active["checkpoint_manifest"]},
               "owners": owners, "gpu_observations": gpu_observations,
               "ports_observation": {"status": "closed", "closed_ports": [8271, 8272, 8273, 8274],
                                     "evidence_path": ports_path, "evidence_sha256": ports_hash},
               "evidence_files_sha256": evidence, "errors": [], "blockers": [] if status == "completed" else ["pending"]}
    receipt_path = directory / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    return receipt_path


def _build_run(tmp_path, *, complete=False, with_config=True, with_cleanup=True, round_plan=None):
    _TURN_COUNTER[0] = 0
    output_root = tmp_path / "output_root"
    run = output_root / "runs" / "run"
    run.mkdir(parents=True)
    rounds = 5 if complete else (len(round_plan) if round_plan is not None else 2)
    release, manifests, record_hashes = _build_release(tmp_path, rounds)
    actual_config = _build_config(tmp_path, output_root, "run", release, rounds)
    config_path = actual_config if with_config else None
    if round_plan is not None:
        plan = round_plan
    elif complete:
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
    protocol_hash = _fixture_protocol(actual_config)
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
            manifest=manifests[n], protocol_hash=protocol_hash,
            component="HARNESS", decision=decision)
        generation = specs[n]["selected_generation"]

    final_harness = _harness_file(run, generation)
    (run / "active_task.json").write_text(json.dumps(_task_state(generation, final_harness)), encoding="utf-8")
    a0 = run / "snapshots/A0"
    _fixture_snapshot(run, a0, None, None, _task_state(0, _harness_file(run, 0)))
    (run / "active_task.json").write_text(json.dumps(_task_state(generation, final_harness)))

    meta = run / "meta"
    meta.mkdir(exist_ok=True)
    (meta / "skills.jsonl").write_text(json.dumps({"id": "case.HARNESS.x", "type": "case"}) + "\n", encoding="utf-8")
    (meta / "context.json").write_text(json.dumps({"round": plan[-1][0], "attempt": 1}), encoding="utf-8")
    session = run / "meta_session"
    session.mkdir(exist_ok=True)
    (session / "state.json").write_text(json.dumps({
        "status": "complete" if complete else "running", "transport": "app_server",
        "last_turn_status": "completed" if complete else "running", "thread_id": "thread-1",
        "launcher_pid": 14871, "codex_pid": 14896,
    }), encoding="utf-8")
    (session / "RUN_STATUS.md").write_text(
        ("Current stage: **COMPLETE**\nFinish allowed: **yes**\nFinal stage: **B5**\n"
         if complete else "Current stage: **B3**\nFinish allowed: **no**\nFinal stage: **B1**\n"),
        encoding="utf-8")
    stage_ids = ["A0"] + [f"B{n}" for n in range(1, rounds + 1)]
    b_milestones = ["parent_rollout", "meta_evidence", "decision_recorded", "candidate_built",
                    "candidate_rollout", "scores_compared", "selection_recorded", "skills_appended",
                    "context_updated", "task_meta_snapshot"]
    a0_milestones = ["bootstrap", "task_meta_snapshot", "empty_skills_initialized"]

    def _stage(sid):
        done = complete or sid in ("A0", "B1", "B2")
        required_m = a0_milestones if sid == "A0" else b_milestones
        return {"id": sid, "status": "complete" if done else "pending",
                "remaining_milestones": [] if done else required_m,
                "required_milestones": required_m,
                "completed_milestones": required_m if done else [],
                "evidence": {m: f"evidence/{sid}/{m}.json" for m in required_m} if done else {}}

    (session / "workflow_state.json").write_text(json.dumps({
        "current_stage": "COMPLETE" if complete else "B3",
        "finish_allowed": complete, "final_stage": f"B{rounds}",
        "stages": [_stage(sid) for sid in stage_ids],
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
    _fixture_journal(run, actual_config, complete)
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


def _direct_receipt_path(run, task_id):
    return (run / "round_1/attempts/attempt_1/candidate_rollout/train_rollouts" /
            (_sha(f"{task_id}:0") + ".json"))


def _build_artifacts_run(tmp_path):
    import shutil

    run, _, ctx = _build_run(
        tmp_path,
        round_plan=[(1, set(TASK_IDS[:2]), set(TASK_IDS[:3]), "accept_candidate")],
    )
    round_dir = run / "round_1"
    attempt = round_dir / "attempts/attempt_1"
    old_parent_rows = [json.loads(line) for line in
        (round_dir / "parent/train_trajectories.jsonl").read_text().splitlines() if line]
    manifest = ctx["manifests"][1]
    protocol_hash = _fixture_protocol(ctx["config_path"])
    a0_state_path = run / "snapshots/A0/task/task_state_snapshot.json"
    parent_state = json.loads(a0_state_path.read_text())
    a0_receipt = json.loads((run / "snapshots/A0/snapshot_receipt.json").read_text())
    version_index = json.loads(Path(a0_receipt["index_path"]).read_text())
    harness_id = a0_receipt["combinations"]["selected"]["harness"]
    original_harness = next(entry["source"] for entry in version_index["harness"]
                            if entry["id"] == harness_id)
    parent_rollout_state = {**parent_state, "harness_path": original_harness}

    for directory in (round_dir, run / "controller_transport", run / "meta_session/jobs",
                      run / "snapshots/round_1_attempt_1"):
        shutil.rmtree(directory, ignore_errors=True)
    round_dir.mkdir()

    parent_scope = _make_scope(1, manifest["manifest_hash"], "parent_pre_update",
                               parent_rollout_state, protocol_hash)
    parent_state_hash = _fingerprint_fixture(parent_scope)
    parent_rows = []
    for index, old in enumerate(old_parent_rows):
        row = copy.deepcopy(old)
        row.update(seed=1000 + index, reset_hash=_sha(f"reset:{row['task_id']}"),
                   final_answer="def capitalize(s): return []" if row["task_id"] == TASK_IDS[2] else "",
                   model_answer="", state_hash=parent_state_hash,
                   trajectory_id=_fingerprint_fixture([parent_scope, row["task_id"], 0]))
        parent_rows.append(row)
    _write_side(round_dir / "parent", round_dir / "rollout_cache", parent_rows,
                parent_state_hash, 1, manifest["manifest_hash"], parent_scope, manifest)

    child = attempt / "build/child"
    harness_path = child / "rsiflow_base_manifest.json"
    harness_path.parent.mkdir(parents=True)
    shutil.copyfile(parent_state["harness_path"], harness_path)
    edited_task = TASK_IDS[2]
    edited_name = f"submissions/train/{_sha(edited_task + ':0')}.json"
    baseline = next(row for row in parent_rows if row["task_id"] == edited_task)
    baseline_hash = _value_hash(baseline)
    payload = {"final_answer": "def capitalize(s): return [s.upper(), s.lower()]", "actions": []}
    artifact_path = child / "artifacts" / edited_name
    artifact_path.parent.mkdir(parents=True)
    artifact_record = {
        "schema": "task_submission_v1", "baseline_sha256": baseline_hash,
        "task_id": edited_task, "task_source_hash": baseline["task_source_hash"],
        "rollout_id": 0, "split": baseline["split"], "seed": baseline["seed"],
        "payload": payload,
    }
    artifact_path.write_text(json.dumps(artifact_record), encoding="utf-8")
    artifacts_dir = child / "artifacts"
    candidate_state = {
        **parent_state, "generation": 1, "harness_path": str(harness_path),
        "artifacts": {"directory": str(artifacts_dir),
                      "manifest": _artifact_manifest_fixture(artifacts_dir)},
    }
    (attempt / "build").mkdir(parents=True, exist_ok=True)
    (attempt / "build/candidate_state.json").write_text(json.dumps(candidate_state), encoding="utf-8")
    candidate_scope = _make_scope(1, manifest["manifest_hash"], "child_post_update",
                                  candidate_state, protocol_hash)
    candidate_state_hash = candidate_scope["task_hash"]
    candidate_rows = []
    receipt_root = attempt / "candidate_rollout/train_rollouts"
    receipt_root.mkdir(parents=True)
    for old in parent_rows:
        task_id = old["task_id"]
        name = f"submissions/train/{_sha(task_id + ':0')}.json"
        receipt_path = receipt_root / (_sha(task_id + ":0") + ".json")
        baseline_hash = _value_hash(old)
        edited = task_id == edited_task
        binding = {"state_hash": candidate_state_hash, "baseline_sha256": baseline_hash,
                   "edited": edited, "round_scope": candidate_scope}
        row = copy.deepcopy(old)
        row.update(
            state_hash=candidate_state_hash, model_calls=[], transport_calls=[], sft_conversations=[],
            messages=[], notes=[], input_tokens=0, output_tokens=0, model_call_count=0,
            unknown_usage_calls=0, usage_complete=True, wall_time_seconds=0, tool_calls=[],
            artifact_input_manifest=candidate_state["artifacts"]["manifest"], artifact_chars_used=0,
            submission_origin="meta_output_repair" if edited else "baseline_result_reused",
            baseline_row_sha256=baseline_hash, events=[], meta_generated=True,
            derived_from=[baseline_hash], submission_receipt=str(receipt_path.resolve()),
            collection_stage="child_post_update", branch_id="child",
            trajectory_id=_value_hash([binding, name]), candidate_id=None,
        )
        if edited:
            row.update(final_answer=payload["final_answer"], model_answer=None,
                       terminal_reward=1.0, metrics={"pass": 1.0, "test_pass_fraction": 1.0},
                       verification={"status": "completed", "verifier_id": VERIFIER_ID,
                                     "success": True, "full_verifier": True,
                                     "tests_completed": 11, "tests_total": 11},
                       error_type=None, execution_error_type=None, valid_answer=True,
                       wall_time_seconds=0.25,
                       events=[{"kind": "direct_submission_evaluated",
                                "task_model_calls": 0, "tool_actions": 0}])
        receipt_path.write_text(json.dumps({"binding": binding, "status": "completed", "row": row}),
                                encoding="utf-8")
        candidate_rows.append(row)
    candidate_dir = attempt / "candidate_rollout"
    (candidate_dir / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in candidate_rows), encoding="utf-8")
    candidate_performance = _performance(candidate_rows, 1, manifest["manifest_hash"])
    candidate_performance["training_rewards"] = copy.deepcopy({
        key: candidate_performance[key] for key in
        ("domains", "macro_success", "domain_weights", "status", "total_rollouts", "units")
    })
    candidate_performance.pop("benchmarks")
    candidate_performance.update(
        evaluation_protocol="direct_submission_same_tasks_v1",
        attribution="meta_assisted_output_quality_not_task_policy_improvement",
    )
    (candidate_dir / "performance.json").write_text(json.dumps(candidate_performance), encoding="utf-8")
    (candidate_dir / "cost.json").write_text(json.dumps({
        "model_calls": 0, "input_tokens": 0, "output_tokens": 0, "unknown_usage_calls": 0,
        "usage_complete": True, "api_cost_usd": None, "gpu_hours": 0,
        "wall_time_seconds": 0.25, "replayed_submissions": 1, "reused_baseline_results": 15,
    }), encoding="utf-8")

    (attempt / "decision.json").write_text(json.dumps({
        "component": "ARTIFACTS", "round_number": 1, "attempt": 1,
        "relevant_skill_ids": [], "rationale": "repair one output",
        "prediction": {"target_task_ids": [edited_task], "new_success": 1, "new_regression": 0},
        "boundaries": "Meta-assisted output repair, not standalone Task policy improvement",
    }), encoding="utf-8")
    (attempt / "selection.json").write_text(json.dumps({
        "selection": "accept", "component": "ARTIFACTS", "round": 1, "attempt": 1,
        "before": 0.125, "after": 0.1875, "delta": 0.0625, "paired_tasks": 16,
        "same_id_set": True, "source_hash_mismatches": 0, "new_success": 1, "new_regression": 0,
        "rationale": "Meta-assisted output repair",
    }), encoding="utf-8")
    (attempt / "skill_use.json").write_text(json.dumps({"used_skill_ids": []}), encoding="utf-8")
    diff_dir = attempt / "task_differences"
    diff_dir.mkdir()
    (diff_dir / "summary.json").write_text(json.dumps({
        "status": "compared", "paired_tasks": 16, "same_id_set": True,
        "counts": {"retained_failure": 13, "new_success": 1, "retained_success": 2},
        "domain_transitions": {"code": {"retained_failure": 13, "new_success": 1, "retained_success": 2}},
        "source_mismatches": [], "duplicates": {"before": [], "after": []},
        "missing_in_after": [], "extra_in_after": [], "notes": [],
    }), encoding="utf-8")

    before_path = round_dir / "parent/performance.json"
    after_path = candidate_dir / "performance.json"
    _transport_receipt(run, "compare_1", {
        "operation": "compare_scores", "status": "compared", "metric": "macro_success",
        "before": 0.125, "after": 0.1875, "delta": 0.0625,
        "before_path": str(before_path), "after_path": str(after_path),
        "before_sha256": file_hash(before_path), "after_sha256": file_hash(after_path),
    })
    candidate_state_path = attempt / "build/candidate_state.json"
    active_hash = file_hash(candidate_state_path)
    _transport_receipt(run, "activate_1", {
        "operation": "activate_task", "status": "activated_as_requested",
        "source_state": str(candidate_state_path), "active_state": str(run / "active_task.json"),
        "source_sha256": active_hash, "active_sha256": active_hash,
        "selection_note": "Meta-assisted output repair",
    })
    _fixture_snapshot(run, run / "snapshots/B1_attempt_1", parent_state, candidate_state,
                      candidate_state, attempt=attempt, parent_source=a0_state_path)
    _fixture_journal(run, ctx["config_path"], False)
    return run, ctx


def _mutate_direct_row(run, task_id, mutate):
    receipt_path = _direct_receipt_path(run, task_id)
    receipt = json.loads(receipt_path.read_text())
    mutate(receipt["row"])
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    trajectories = receipt_path.parent.parent / "train_trajectories.jsonl"
    rows = [json.loads(line) for line in trajectories.read_text().splitlines() if line]
    rows = [receipt["row"] if row["task_id"] == task_id else row for row in rows]
    trajectories.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_summarizer_real_shaped_artifacts_direct_round_is_complete(tmp_path):
    run, ctx = _build_artifacts_run(tmp_path)

    result = _summarize(run, ctx)

    round_result = _rounds(result)[1]
    assert round_result["product_blockers"] == []
    assert round_result["products_complete"] is True
    assert result["completed_rounds"] == 1
    assert round_result["parent"]["successes"] == 2
    assert round_result["candidate"]["successes"] == 3
    assert round_result["delta"] == 0.0625
    assert round_result["transitions"] == {
        "new_success": 1, "new_regression": 0, "retained_failure": 13, "retained_success": 2,
    }
    direct = round_result["direct_submission"]
    assert direct["receipts_valid"] == 16
    assert direct["new_model_calls"] == 0
    assert direct["edited_targets"] == [f"submissions/train/{_sha(TASK_IDS[2] + ':0')}.json"]
    assert direct["reused_baseline_results"] == 15
    assert direct["attribution"] == "meta_assisted_output_quality_not_task_policy_improvement"
    assert round_result["snapshot_states"]["parent"] == json.loads(
        (run / "snapshots/A0/task/task_state_snapshot.json").read_text())
    assert not any("active chain" in blocker for blocker in _blockers(result))
    assert "Meta-assisted output repair" in _load_summarizer()._markdown(result)


@pytest.mark.parametrize("case, expected", [
    ("wrong_baseline_hash", "baseline hash"),
    ("wrong_state_hash", "state hash"),
    ("missing_edited_target", "edited target set"),
    ("extra_edited_target", "edited target set"),
    ("changed_unedited_result", "unedited baseline result reuse"),
    ("missing_direct_receipt", "direct receipt missing"),
    ("false_nonzero_calls", "zero new calls"),
    ("artifact_payload", "artifact payload/baseline binding"),
    ("receipt_row_mismatch", "receipt row equality"),
    ("tampered_snapshot_parent", "snapshot parent source"),
    ("compare_metric", "compare_scores metric/before/after"),
    ("compare_before", "compare_scores metric/before/after"),
    ("compare_after", "compare_scores metric/before/after"),
    ("selection_same_id_set", "selection same_id_set"),
    ("selection_source_hashes", "selection source_hash_mismatches"),
    ("activate_active_state", "activate_task active_state"),
])
def test_summarizer_artifacts_direct_tampering_fails_closed(tmp_path, case, expected):
    run, ctx = _build_artifacts_run(tmp_path)
    edited_receipt = _direct_receipt_path(run, TASK_IDS[2])
    if case in {"wrong_baseline_hash", "wrong_state_hash", "missing_edited_target"}:
        receipt = json.loads(edited_receipt.read_text())
        if case == "wrong_baseline_hash":
            receipt["binding"]["baseline_sha256"] = "0" * 64
        elif case == "wrong_state_hash":
            receipt["binding"]["state_hash"] = "0" * 64
        else:
            receipt["binding"]["edited"] = False
        edited_receipt.write_text(json.dumps(receipt), encoding="utf-8")
    elif case == "extra_edited_target":
        path = _direct_receipt_path(run, TASK_IDS[0])
        receipt = json.loads(path.read_text())
        receipt["binding"]["edited"] = True
        path.write_text(json.dumps(receipt), encoding="utf-8")
    elif case == "changed_unedited_result":
        _mutate_direct_row(run, TASK_IDS[0], lambda row: row.update(final_answer="tampered"))
    elif case == "missing_direct_receipt":
        edited_receipt.unlink()
    elif case == "false_nonzero_calls":
        _mutate_direct_row(run, TASK_IDS[0], lambda row: row.update(model_call_count=1))
    elif case == "artifact_payload":
        artifact = run / "round_1/attempts/attempt_1/build/child/artifacts" / (
            f"submissions/train/{_sha(TASK_IDS[2] + ':0')}.json")
        record = json.loads(artifact.read_text())
        record["payload"]["final_answer"] = "not the evaluated payload"
        artifact.write_text(json.dumps(record), encoding="utf-8")
    elif case == "receipt_row_mismatch":
        trajectories = edited_receipt.parent.parent / "train_trajectories.jsonl"
        rows = [json.loads(line) for line in trajectories.read_text().splitlines() if line]
        rows[2]["final_answer"] = "receipt mismatch"
        trajectories.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    elif case == "tampered_snapshot_parent":
        for path in (run / "snapshots/B1_attempt_1/snapshot_receipt.json",
                     run / "snapshots/B1_attempt_1/versions.json"):
            value = json.loads(path.read_text())
            value["combinations"]["parent"]["source_state"] = str(
                run / "round_1/attempts/attempt_1/before/parent_task.json")
            path.write_text(json.dumps(value), encoding="utf-8")
    elif case.startswith("compare_"):
        path = run / "controller_transport/compare_1/result.json"
        receipt = json.loads(path.read_text())
        receipt[case.removeprefix("compare_")] = "wrong_metric" if case == "compare_metric" else 0.5
        path.write_text(json.dumps(receipt), encoding="utf-8")
    elif case.startswith("selection_"):
        path = run / "round_1/attempts/attempt_1/selection.json"
        selection = json.loads(path.read_text())
        if case == "selection_same_id_set":
            selection["same_id_set"] = False
        else:
            selection["source_hash_mismatches"] = 1
        path.write_text(json.dumps(selection), encoding="utf-8")
    elif case == "activate_active_state":
        path = run / "controller_transport/activate_1/result.json"
        receipt = json.loads(path.read_text())
        receipt["active_state"] = str(run / "round_1/active_task.json")
        path.write_text(json.dumps(receipt), encoding="utf-8")

    round_result = _rounds(_summarize(run, ctx))[1]

    assert round_result["products_complete"] is False
    assert any(expected in blocker for blocker in round_result["product_blockers"]), round_result["product_blockers"]


@pytest.mark.parametrize("component", ["HARNESS", "MODEL"])
def test_summarizer_never_accepts_direct_protocol_for_other_components(tmp_path, component):
    run, ctx = _build_artifacts_run(tmp_path)
    decision = run / "round_1/attempts/attempt_1/decision.json"
    value = json.loads(decision.read_text())
    value["component"] = component
    decision.write_text(json.dumps(value), encoding="utf-8")

    round_result = _rounds(_summarize(run, ctx))[1]

    assert round_result["products_complete"] is False
    assert any("direct submission protocol requires component=ARTIFACTS" in blocker
               for blocker in round_result["product_blockers"])


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


def _first_parent_call(run):
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    return cache, next(cache.glob("*.calls/*.json"))


def test_if2_persisted_outcome_mismatch_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    rollout_json = next(cache.glob("*.json"))
    receipt = json.loads(rollout_json.read_text())
    receipt["row"]["verification"] = {"status": "completed", "success": True, "full_verifier": True}
    rollout_json.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_if2_wrong_task_record_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache, call_file = _first_parent_call(run)
    rollout_json = next(cache.glob("*.json"))
    receipt = json.loads(rollout_json.read_text())
    receipt["binding"]["task_record"] = "0" * 64
    rollout_json.write_text(json.dumps(receipt), encoding="utf-8")
    call = json.loads(call_file.read_text())
    call["binding"]["task_record"] = "0" * 64
    call_file.write_text(json.dumps(call), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_if2_wrong_scope_harness_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    for receipt_file in list(cache.glob("*.json")) + list(cache.glob("*.calls/*.json")):
        receipt = json.loads(receipt_file.read_text())
        receipt["binding"]["round_scope"]["harness_sha256"] = "0" * 64
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_if2_wrong_scope_protocol_hash_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = next((run / "round_1/rollout_cache").glob("*/train_rollouts"))
    for receipt_file in list(cache.glob("*.json")) + list(cache.glob("*.calls/*.json")):
        receipt = json.loads(receipt_file.read_text())
        receipt["binding"]["round_scope"]["protocol_hash"] = "0" * 64
        receipt_file.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["calls"]["cross_check_ok"] is False


def test_if2_wrong_response_checkpoint_path_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache, call_file = _first_parent_call(run)
    call = json.loads(call_file.read_text())
    call["response"]["binding"]["checkpoint_path"] = "/models/Unrelated"
    call_file.write_text(json.dumps(call), encoding="utf-8")
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


def test_if3_required_state_removed_from_seal_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    receipt_path = run / "snapshots/round_1_attempt_1/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    del receipt["files_sha256"]["task/parent_state.json"]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"] is False


def test_if3_a0_same_generation_different_task_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    # A0 keeps generation 0 but a different harness content -> B1 parent chain breaks
    a0_state_path = run / "snapshots/A0/task/task_state_snapshot.json"
    a0_state = json.loads(a0_state_path.read_text())
    other = run / "harness" / "a0_unrelated.json"
    other.write_text(json.dumps({"harness_generation": "unrelated"}), encoding="utf-8")
    a0_state["harness_path"] = str(other)
    a0_state_path.write_text(json.dumps(a0_state), encoding="utf-8")
    receipt_path = run / "snapshots/A0/snapshot_receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["files_sha256"]["task/task_state_snapshot.json"] = file_hash(a0_state_path)
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["protocol_complete"] is False
    assert any("active chain" in b for b in _blockers(result))


def test_if3_artifacts_relocation_allowed_content_change_rejected(tmp_path):
    module = _load_summarizer()
    harness = tmp_path / "h.json"
    harness.write_text(json.dumps({"harness": 1}), encoding="utf-8")
    manifest = [{"path": "submission.py", "bytes": 10, "sha256": "a" * 64}]
    base = {"generation": 1, "model_ref": CHECKPOINT, "checkpoint_path": CHECKPOINT,
            "checkpoint_manifest": WEIGHTS, "harness_path": str(harness),
            "artifacts": {"directory": "/source/dir", "manifest": manifest}}
    relocated = json.loads(json.dumps(base))
    relocated["artifacts"]["directory"] = "/versions/relocated/dir"  # same content manifest
    assert module._identity_equal(base, relocated) is True
    changed = json.loads(json.dumps(base))
    changed["artifacts"]["manifest"] = [{"path": "submission.py", "bytes": 10, "sha256": "b" * 64}]
    assert module._identity_equal(base, changed) is False


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


def _build_model_retain_run(tmp_path):
    import shutil

    run, ctx = _build_artifacts_run(tmp_path)
    extra_root = tmp_path / "model-release-input"
    extra_release, extra_manifests, _ = _build_release(extra_root, 2)
    release = ctx["release"]
    shutil.copytree(extra_release / "B2", release / "B2")
    b2_tasks = copy.deepcopy(extra_manifests[2]["tasks"])
    for task in b2_tasks:
        task["record_file"] = task["record_file"].replace(str(extra_release), str(release))
    manifests = {1: ctx["manifests"][1], 2: make_manifest("train_evolution", 2, b2_tasks)}
    (release / "B2/manifest.json").write_text(
        json.dumps(manifests[2], sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ctx["manifests"] = manifests
    parent_state = json.loads(
        (run / "round_1/attempts/attempt_1/build/candidate_state.json").read_text())
    manifest = manifests[2]
    protocol_hash = _fixture_protocol(ctx["config_path"])
    parent_scope = _make_scope(2, manifest["manifest_hash"], "parent_pre_update",
                               parent_state, protocol_hash)
    parent_state_hash = _fingerprint_fixture(parent_scope)
    round_dir = run / "round_2"
    attempt = round_dir / "attempts/attempt_1"
    attempt.mkdir(parents=True)
    parent_rows = []
    call_plan = dict(zip(TASK_IDS[:6], [7, 7, 7, 7, 7, 5]))
    for tid in TASK_IDS:
        row = _trajectory(
            tid, success=tid in set(TASK_IDS[:4]), calls=call_plan.get(tid, 0), round_n=2,
            state_hash=parent_state_hash, branch="parent", manifest_hash=manifest["manifest_hash"],
            task_source_hash=manifest["tasks"][0]["record_sha256"],
            collection_stage="parent_pre_update", scope=parent_scope,
            checkpoint_manifest=parent_state["checkpoint_manifest"], model_ref=parent_state["model_ref"])
        row["artifact_input_manifest"] = parent_state["artifacts"]["manifest"]
        parent_rows.append(row)
    _write_side(round_dir / "parent", round_dir / "rollout_cache", parent_rows,
                parent_state_hash, 2, manifest["manifest_hash"], parent_scope, manifest)
    (attempt / "decision.json").write_text(json.dumps({
        "component": "MODEL", "round": 2, "attempt": 1, "relevant_skill_ids": [],
        "rationale": "positive-only SFT", "prediction": {},
    }), encoding="utf-8")
    (attempt / "selection.json").write_text(json.dumps({
        "selection": "retain_parent", "component": "MODEL", "round": 2, "attempt": 1,
        "before": 0.25, "after": 0.1875, "delta": -0.0625, "paired_tasks": 16,
        "same_id_set": True, "source_hash_mismatches": 0, "new_success": 2, "new_regression": 3,
    }), encoding="utf-8")
    (attempt / "skill_use.json").write_text(json.dumps({"used_skill_ids": []}), encoding="utf-8")
    (attempt / "task_differences").mkdir()
    _add_model_round(run, ctx, 2, production=True)
    return run, ctx


def test_summarizer_real_shaped_model_retain_round_is_terminal_without_activation(tmp_path):
    run, ctx = _build_model_retain_run(tmp_path)

    result = _summarize(run, ctx)

    rounds = _rounds(result)
    assert rounds[1]["products_complete"] is True
    assert rounds[2]["product_blockers"] == []
    assert rounds[2]["products_complete"] is True
    assert rounds[2]["terminal"] is True
    assert rounds[2]["activated"] is False
    assert rounds[2]["parent"]["successes"] == 4
    assert rounds[2]["candidate"]["successes"] == 3
    assert rounds[2]["parent"]["trajectory_calls"] == 40
    assert rounds[2]["candidate"]["trajectory_calls"] == 118
    assert rounds[2]["transitions"] == {
        "new_success": 2, "new_regression": 3, "retained_failure": 10, "retained_success": 1,
    }
    assert result["model_sft"]["status"] == "completed"
    assert rounds[2]["snapshot_states"]["selected"] == rounds[2]["snapshot_states"]["parent"]
    assert rounds[2]["snapshot_states"]["selected"]["checkpoint_manifest"] == (
        rounds[2]["parent_state"]["checkpoint_manifest"])
    assert rounds[2]["snapshot_states"]["candidate"]["checkpoint_manifest"] != (
        rounds[2]["snapshot_states"]["selected"]["checkpoint_manifest"])
    assert not any(blocker.startswith("B2 ") and "active chain" in blocker
                   for blocker in _blockers(result))


@pytest.mark.parametrize("case", [
    "candidate_hash", "selection_score", "compare_hash", "false_accept_activation",
    "candidate_selected", "wrong_previous_snapshot", "incomplete_sft", "failed_sft",
])
def test_summarizer_model_retain_tampering_fails_closed(tmp_path, case):
    run, ctx = _build_model_retain_run(tmp_path)
    attempt = run / "round_2/attempts/attempt_1"
    if case == "candidate_hash":
        path = attempt / "candidate_rollout/train_trajectories.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        rows[0]["task_source_hash"] = "0" * 64
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    elif case == "selection_score":
        path = attempt / "selection.json"
        value = json.loads(path.read_text())
        value["after"] = 0.25
        path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "compare_hash":
        path = run / "controller_transport/compare_2/result.json"
        value = json.loads(path.read_text())
        value["after_sha256"] = "0" * 64
        path.write_text(json.dumps(value), encoding="utf-8")
    elif case == "false_accept_activation":
        path = attempt / "selection.json"
        value = json.loads(path.read_text())
        value["selection"] = "accept"
        path.write_text(json.dumps(value), encoding="utf-8")
        candidate = attempt / "build/candidate_state.json"
        digest = file_hash(candidate)
        _transport_receipt(run, "activate_2", {
            "operation": "activate_task", "status": "activated_as_requested",
            "source_state": str(candidate), "active_state": str(run / "active_task.json"),
            "source_sha256": digest, "active_sha256": digest,
        })
    elif case == "candidate_selected":
        parent = json.loads((run / "snapshots/B1_attempt_1/task/task_state_snapshot.json").read_text())
        child = json.loads((attempt / "build/candidate_state.json").read_text())
        _fixture_snapshot(run, run / "snapshots/B2_attempt_1", parent, child, child,
                          attempt=attempt,
                          parent_source=run / "snapshots/B1_attempt_1/task/task_state_snapshot.json")
    elif case == "wrong_previous_snapshot":
        for path in (run / "snapshots/B2_attempt_1/snapshot_receipt.json",
                     run / "snapshots/B2_attempt_1/versions.json"):
            value = json.loads(path.read_text())
            value["combinations"]["parent"]["source_state"] = str(
                run / "snapshots/A0/task/task_state_snapshot.json")
            path.write_text(json.dumps(value), encoding="utf-8")
    elif case in {"incomplete_sft", "failed_sft"}:
        path = run / "controller_transport/sft_2/result.json"
        value = json.loads(path.read_text())
        value["status"] = "running" if case == "incomplete_sft" else "tool_error"
        path.write_text(json.dumps(value), encoding="utf-8")

    result = _summarize(run, ctx)
    round_result = _rounds(result)[2]

    assert round_result["products_complete"] is False
    if case == "wrong_previous_snapshot":
        assert any("snapshot parent source" in blocker for blocker in round_result["product_blockers"])
    if case in {"incomplete_sft", "failed_sft"}:
        assert result["model_sft"]["status"] in {"selected_pending", "selected_failed"}


# --- F6: production MODEL/SFT schema ---------------------------------------


def _add_model_round(run, ctx, n=2, *, mutate=None, production=False):
    attempt = run / f"round_{n}/attempts/attempt_1"
    decision_path = attempt / "decision.json"
    decision = json.loads(decision_path.read_text())
    decision["component"] = "MODEL"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    previous_snapshot = run / f"snapshots/B{n - 1}_attempt_1/task/task_state_snapshot.json"
    if production:
        parent_state = json.loads(
            (run / f"round_{n - 1}/attempts/attempt_1/build/candidate_state.json").read_text())
    else:
        parent_state = json.loads((attempt / "before/parent_task.json").read_text())
    manifest = ctx["manifests"][n]
    manifest_hash = manifest["manifest_hash"]
    task_source_hash = manifest["tasks"][0]["record_sha256"]
    protocol_hash = _fixture_protocol(ctx["config_path"])
    out = attempt / ("build" if production else "candidate")
    rollout_dir = attempt / "candidate_rollout" if production else out
    model_update = out / "model_update"
    model_update.mkdir(parents=True, exist_ok=True)
    from rsiflow.task_meta.storage import checkpoint_manifest
    checkpoint = model_update / "checkpoint"
    checkpoint.mkdir(exist_ok=True)
    (checkpoint / "model.safetensors").write_bytes(b"offline fixture checkpoint bytes")
    trained_checkpoint = str(checkpoint)
    trained_weights = checkpoint_manifest(checkpoint)
    child = _task_state(parent_state["generation"] + 1, parent_state["harness_path"])
    child["checkpoint_path"] = trained_checkpoint
    child["model_ref"] = trained_checkpoint
    child["checkpoint_manifest"] = trained_weights
    if production:
        child["artifacts"] = copy.deepcopy(parent_state["artifacts"])
    from rsiflow.task_meta.sft import select_positive_rows
    selection_report = {"input_rows": 4, "unique_rows": 4, "duplicates": [], "deduplication_applied": True}
    metrics = {
        "world_size": 4,
        "rank_records": [{"rank": r, "local_rank": r, "sampled_indices": [r], "optimizer_steps": 1,
                          "peak_memory_bytes": 1024} for r in range(4)],
        "num_train_epochs_completed": 1.0, "training_loss": 0.5, "batch_size": 1, "gradient_accumulation_steps": 8,
        "changed_lora_tensors": 7, "lora_delta_l2": 0.1,
        "merged_probe_name": "q_proj", "merged_probe_changed_elements": 100,
        "optimizer_steps": 1, "selection": selection_report,
        "positive_trajectories": 4, "sampled_training_examples": 4, "sampled_row_indices": [0, 1, 2, 3],
        "checkpoint_path": trained_checkpoint, "serving_status": "ready",
    }
    gpu = {"status": "completed", "training_pid": 17000, "restored_checkpoint": trained_checkpoint,
           "replica_gpus": [0, 1, 2, 3], "gpus": [0, 1, 2, 3],
           "training_identity": {"pid": 17000, "pgid": 17000, "start_ticks": "150"},
           "training_run_dir": str(run), "training_request_dir": str(model_update),
           "command": ["python", "train.py", "--request-dir", str(model_update)],
           "training_argv": ["python", "train.py", "--request-dir", str(model_update)]}
    trained = {"model_ref": trained_checkpoint, "checkpoint_path": trained_checkpoint,
               "training_metrics": str(model_update / "training_metrics.json"),
               "training_method": "positive_sft_lora", "training_steps": 1, "training_summary": "fixture"}
    verified = {"checkpoint_path": trained_checkpoint, "model_ref": trained_checkpoint,
                "weights": trained_weights, "served_binding": {"checkpoint_path": trained_checkpoint, "weights": trained_weights},
                "checkpoint_before": {"path": parent_state["checkpoint_path"], "weights": parent_state["checkpoint_manifest"]},
                "training_method": "positive_sft_lora", "training_steps": 1}
    before = {"path": parent_state["checkpoint_path"], "weights": parent_state["checkpoint_manifest"]}
    if mutate:
        mutate(metrics, gpu, child, trained)
    # rebuild the evaluated candidate side so the trained child IS the rolled-out Task
    candidate_scope = _make_scope(n, manifest_hash, "child_post_update", child, protocol_hash)
    candidate_state_hash = _fingerprint_fixture(candidate_scope)
    candidate_success = {TASK_IDS[0], TASK_IDS[4], TASK_IDS[5]} if production else set(TASK_IDS[3:7])
    call_plan = dict(zip(TASK_IDS[:6], [20, 20, 20, 20, 20, 18])) if production else {}
    zero_call = set(TASK_IDS[6:16]) if production else set(TASK_IDS[9:16])
    candidate_rows = []
    for tid in TASK_IDS:
        calls = call_plan.get(tid, 0) if production else (0 if tid in zero_call else 7)
        row = _trajectory(
            tid, success=tid in candidate_success, calls=calls, round_n=n,
            state_hash=candidate_state_hash, branch="candidate", manifest_hash=manifest_hash,
            task_source_hash=task_source_hash, collection_stage="child_post_update",
            scope=candidate_scope, checkpoint_manifest=child["checkpoint_manifest"],
            model_ref=child["model_ref"])
        if production:
            row["artifact_input_manifest"] = child["artifacts"]["manifest"]
        candidate_rows.append(row)
    import shutil
    shutil.rmtree(attempt / "rollout_cache", ignore_errors=True)
    _write_side(rollout_dir, attempt / "rollout_cache", candidate_rows, candidate_state_hash, n,
                manifest_hash, candidate_scope, manifest)
    (attempt / "build/candidate_state.json").write_text(json.dumps(child), encoding="utf-8")
    (out / "candidate_state.json").write_text(json.dumps(child), encoding="utf-8")
    snap = run / "snapshots" / (f"B{n}_attempt_1" if production else f"round_{n}_attempt_1")
    if not production:
        # refresh snapshot candidate state + seal to the trained child
        (snap / "task/candidate_state.json").write_text(json.dumps(child), encoding="utf-8")
        receipt_path = snap / "snapshot_receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["files_sha256"]["task/candidate_state.json"] = file_hash(snap / "task/candidate_state.json")
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    (model_update / "training_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (model_update / "gpu_phase_receipt.json").write_text(json.dumps(gpu), encoding="utf-8")
    (model_update / "checkpoint_trained.json").write_text(json.dumps(trained), encoding="utf-8")
    (model_update / "checkpoint_verified.json").write_text(json.dumps(verified), encoding="utf-8")
    (model_update / "checkpoint_before.json").write_text(json.dumps(before), encoding="utf-8")
    (model_update / "sft_selection.json").write_text(json.dumps(selection_report), encoding="utf-8")
    (model_update / "sft_positive.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in select_positive_rows(
            [json.loads(line) for line in (run / f"round_{n}/parent/train_trajectories.jsonl").read_text().splitlines()], profile="multidomain")), encoding="utf-8")
    _transport_receipt(run, f"sft_{n}", {
        "operation": "sft", "status": "completed",
        "candidate_state_path": str(out / "candidate_state.json"),
        "training_metrics_path": str(model_update / "training_metrics.json"),
        "checkpoint_path": trained_checkpoint, "positive_samples": 4,
    })
    if production:
        selected = json.loads(previous_snapshot.read_text())
        _fixture_snapshot(run, snap, selected, child, selected, attempt=attempt,
                          parent_source=previous_snapshot)
        parent_performance = run / f"round_{n}/parent/performance.json"
        candidate_performance = rollout_dir / "performance.json"
        _transport_receipt(run, f"compare_{n}", {
            "operation": "compare_scores", "status": "compared", "metric": "macro_success",
            "before": 0.25, "after": 0.1875, "delta": -0.0625,
            "before_path": str(parent_performance), "after_path": str(candidate_performance),
            "before_sha256": file_hash(parent_performance), "after_sha256": file_hash(candidate_performance),
        })
        (attempt / "selection.json").write_text(json.dumps({
            "selection": "retain_parent", "component": "MODEL", "round": n, "attempt": 1,
            "before": 0.25, "after": 0.1875, "delta": -0.0625, "paired_tasks": 16,
            "same_id_set": True, "source_hash_mismatches": 0, "new_success": 2, "new_regression": 3,
        }), encoding="utf-8")
        (attempt / "task_differences/summary.json").write_text(json.dumps({
            "status": "compared", "paired_tasks": 16, "same_id_set": True,
            "counts": {"retained_failure": 10, "new_success": 2, "new_regression": 3, "retained_success": 1},
            "source_mismatches": [], "duplicates": {"before": [], "after": []},
            "missing_in_after": [], "extra_in_after": [], "notes": [],
        }), encoding="utf-8")
        (run / "active_task.json").write_text(json.dumps(selected))
    else:
        selected = json.loads((attempt / "before/parent_task.json").read_text())
        _fixture_snapshot(run, snap, selected, child, selected)
        final = json.loads((run / "round_5/attempts/attempt_1/build/candidate_state.json").read_text())
        (run / "active_task.json").write_text(json.dumps(final))
    request = {"model_ref": parent_state["model_ref"], "checkpoint_path": parent_state["checkpoint_path"],
               "rollouts": "rollouts.jsonl", "sft_positive": "sft_positive.jsonl", "sft_profile": "multidomain",
               "training_method": "positive_sft_lora", "positive_samples": 4,
               "length_eligibility": {"excluded": [], "truncation_applied": False}}
    (model_update / "training_request.json").write_text(json.dumps(request))
    (model_update / "rollouts.jsonl").write_text((run / f"round_{n}/parent/train_trajectories.jsonl").read_text())
    _fixture_journal(run, ctx["config_path"], not production)
    if ctx.get("cleanup_path"):
        ctx["cleanup_path"] = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"])
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


# --- I-F4: job identity / terminal whitelist / milestone evidence -----------


def test_if4_wrong_job_id_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    job_path = run / "meta_session/jobs/run_parent_1/job.json"
    job = json.loads(job_path.read_text())
    job["job_id"] = "unrelated_id"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("job_id" in b for b in result["jobs"]["invalid"])


def test_if4_unknown_exploration_status_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _write_job(run, "explore_unknown", "run_command", status="UNRECOGNIZED_NONTERMINAL")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("unknown_status" in b for b in result["jobs"]["invalid"])


def test_if4_failed_b5_snapshot_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    turns = run / "meta_session/turns"
    for turn_dir in turns.iterdir():
        command = _read_json_or_none(turn_dir / "command.json")
        if command and command.get("request", {}).get("operation") == "snapshot_task_meta" \
                and command.get("request", {}).get("round_number") == 5:
            receipt_path = turn_dir / "receipt.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["status"] = "tool_error"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["run_complete"] is False
    assert any("B5 snapshot" in b for b in _blockers(result))


def _read_json_or_none(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


# --- I-F5: cleanup binding / owner inventory / historical supervision -------


def test_if5_wrong_config_hash_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"])
    receipt = json.loads(cleanup.read_text())
    receipt["binding"]["config_hash"] = "0" * 64
    cleanup.write_text(json.dumps(receipt), encoding="utf-8")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False
    assert "external_cleanup_config_hash" in result["external_cleanup"]["blockers"]


def test_if5_old_attempt_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], attempt="0001")
    receipt = json.loads(cleanup.read_text())
    receipt["binding"]["supervision_attempt"] = "0000"
    cleanup.write_text(json.dumps(receipt), encoding="utf-8")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False
    assert "external_cleanup_not_current_attempt" in result["external_cleanup"]["blockers"]


def test_if5_missing_owner_inventory_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"])
    receipt = json.loads(cleanup.read_text())
    receipt["owners"]["jobs"] = []
    cleanup.write_text(json.dumps(receipt), encoding="utf-8")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["run_complete"] is False
    assert "external_cleanup_jobs_inventory_missing" in result["external_cleanup"]["blockers"]


def test_if5_historical_143_latest_0_passes(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True, with_cleanup=False)
    supervision = run / "meta_session/supervision"
    old = supervision / "0001"
    (old / "exit").write_text("143", encoding="utf-8")  # historical terminal failure, reconciled
    latest = supervision / "0002"
    latest.mkdir(parents=True)
    (latest / "identity.json").write_text(json.dumps({
        "pid": 14853, "pgid": 14853, "start_ticks": "200", "run_dir": str(run), "attempt_dir": str(latest)}), encoding="utf-8")
    (latest / "reconcile.json").write_text(json.dumps({
        "status": "complete", "term_targets": [], "kill_targets": [], "remaining": []}), encoding="utf-8")
    (latest / "exit").write_text("0", encoding="utf-8")
    cleanup = _cleanup_receipt(run, ctx["output_root"], ctx["config_path"], attempt="0002")
    result = _load_summarizer().summarize(run, config_path=ctx["config_path"], cleanup_receipt_path=cleanup)
    assert result["supervision"]["all_attempts_accounted_for"] is True
    assert result["launcher_exited"] is True
    assert result["run_complete"] is True
    assert (old / "exit").read_text() == "143"  # historical exit preserved


# --- I-F6: SFT child binding / ranks / sampling / selection / verified ------


def test_if6_cross_run_child_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    # replace the actual built/evaluated candidate with an unrelated base-checkpoint child
    build_path = run / "round_2/attempts/attempt_1/build/candidate_state.json"
    unrelated = _task_state(2, run / "round_2/attempts/attempt_1/build/candidate_bundle.json")
    build_path.write_text(json.dumps(unrelated), encoding="utf-8")
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"
    assert result["run_complete"] is False


def test_if6_duplicate_ranks_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)

    def mutate(metrics, gpu, child, trained):
        metrics["rank_records"] = [{"rank": r, "local_rank": r, "sampled_indices": [r], "optimizer_steps": 1}
                                   for r in (0, 1, 2, 3, 0)]

    _add_model_round(run, ctx, 2, mutate=mutate)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"


def test_if6_empty_sampling_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)

    def mutate(metrics, gpu, child, trained):
        metrics["sampled_row_indices"] = []
        metrics["sampled_training_examples"] = 0

    _add_model_round(run, ctx, 2, mutate=mutate)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"


def test_if6_empty_selection_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)

    def mutate(metrics, gpu, child, trained):
        metrics["selection"] = {}

    _add_model_round(run, ctx, 2, mutate=mutate)
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"


def test_if6_missing_verified_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    (run / "round_2/attempts/attempt_1/candidate/model_update/checkpoint_verified.json").unlink()
    result = _summarize(run, ctx)
    assert result["model_sft"]["status"] != "completed"


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
    _write_round(
        run, 1, parent_success=set(TASK_IDS[:3]), candidate_success=set(TASK_IDS[3:7]),
        zero_call_parent=set(TASK_IDS[3:16]), zero_call_candidate=set(TASK_IDS[:3]) | set(TASK_IDS[7:16]),
        parent_generation=0, candidate_generation=1,
        manifest=manifests[1], protocol_hash=_fixture_protocol(ctx["config_path"]),
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


# Fix4: behavior regressions from the independent r3 review. These must fail on
# e3915fb even though its old complete fixture passes.
@pytest.mark.parametrize("field", ["model_ref", "harness_sha256", "rollout_id",
                                   "terminal_reward", "infrastructure_error"])
def test_fix4_full_persisted_row_rejected(tmp_path, field):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = next((run / "round_1/rollout_cache").glob("*/train_rollouts/*.json"))
    value = json.loads(path.read_text())
    value["row"][field] = "WRONG"
    path.write_text(json.dumps(value))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


@pytest.mark.parametrize("field", ["round_scope", "task_record", "task_source_hash", "probe"])
def test_fix4_full_call_binding_rejected(tmp_path, field):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _, path = _first_parent_call(run)
    value = json.loads(path.read_text())
    value["binding"][field] = "WRONG"
    path.write_text(json.dumps(value))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


@pytest.mark.parametrize("field", ["meta", "context_reference", "selected"])
def test_fix4_snapshot_actual_references_sealed(tmp_path, field):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "snapshots/round_1_attempt_1/snapshot_receipt.json"
    value = json.loads(path.read_text())
    if field == "selected":
        alias = path.parent / "unsealed.json"
        alias.write_text(Path(value["combinations"]["selected"]["state_path"]).read_text())
        value["combinations"]["selected"]["state_path"] = str(alias)
    else:
        value[field] = {"path": "/missing", "sha256": "0" * 64}
    path.write_text(json.dumps(value))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


def test_fix4_production_selected_only_a0(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = run / "snapshots/A0/snapshot_receipt.json"
    value = json.loads(path.read_text())
    for role in ("parent", "candidate"):
        value["combinations"].pop(role, None)
        rel = f"task/{role}_state.json"
        value["files_sha256"].pop(rel, None)
        (path.parent / rel).unlink(missing_ok=True)
    path.write_text(json.dumps(value))
    assert _summarize(run, ctx)["protocol_complete"]


def test_fix4_artifacts_identity_idempotent_and_final_chain(tmp_path):
    m = _load_summarizer()
    h = _harness_file(tmp_path, 0)
    state = _task_state(0, h)
    state["artifacts"]["manifest"] = [{"path": "a", "bytes": 1, "sha256": "a" * 64}]
    identity = m._task_identity(state)
    assert m._task_identity(identity) == identity
    other = copy.deepcopy(state)
    other["artifacts"]["manifest"][0]["sha256"] = "b" * 64
    summary = {"round": 1, "parent_state": state, "candidate_state": state,
               "snapshot_states": {"selected": state}, "selection": {"decision": "retain_parent"}}
    assert m._active_chain([summary], other, identity)


@pytest.mark.parametrize("mutation", ["request", "empty_turn", "invented_milestones"])
def test_fix4_job_and_milestone_binding(tmp_path, mutation):
    run, _, ctx = _build_run(tmp_path, complete=True)
    if mutation == "invented_milestones":
        path = run / "meta_session/workflow_state.json"
        value = json.loads(path.read_text())
        for stage in value["stages"]:
            stage.update(required_milestones=["INVENTED"], completed_milestones=["INVENTED"],
                         evidence=["MISSING_PATH"])
    else:
        path = run / "meta_session/jobs/run_parent_1/job.json"
        value = json.loads(path.read_text())
        if mutation == "request":
            value["request"]["output_dir"] = "/WRONG"
        else:
            path = Path(value["turn_dir"]) / "receipt.json"
            value = {}
    path.write_text(json.dumps(value))
    assert not _summarize(run, ctx)["protocol_complete"]


@pytest.mark.parametrize("mutation", ["launch_hash", "thread", "weights", "owner",
                                      "gpu", "missing_compute_apps"])
def test_fix4_cleanup_actual_evidence(tmp_path, mutation):
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = ctx["cleanup_path"]
    value = json.loads(path.read_text())
    if mutation == "launch_hash":
        value["binding"]["launch_receipt_hash"] = "0" * 64
    elif mutation == "thread":
        value["binding"]["thread_id"] = "UNRELATED"
    elif mutation == "weights":
        value["registry"]["weights"] = [{"path": "wrong"}]
    elif mutation == "owner":
        value["owners"]["jobs"] = value["owners"]["jobs"][:1]
    elif mutation == "gpu":
        value["gpu_observations"][-1]["expected_uuids"] = ["DUPLICATE"] * 4
        value["gpu_observations"][-1]["per_gpu"] = [{}] * 4
    else:
        value["gpu_observations"][-1].pop("compute_apps", None)
    path.write_text(json.dumps(value))
    assert not _summarize(run, ctx)["external_cleanup"]["ok"]


@pytest.mark.parametrize("mutation", ["out_of_range", "rank_steps", "count", "positive",
                                      "served", "trained"])
def test_fix4_model_sampling_and_receipts(tmp_path, mutation):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    root = run / "round_2/attempts/attempt_1/candidate/model_update"
    path = root / "training_metrics.json"
    value = json.loads(path.read_text())
    if mutation == "out_of_range":
        value["sampled_row_indices"] = [999] * 4
        for rank in value["rank_records"]:
            rank["sampled_indices"] = [999]
    elif mutation == "rank_steps":
        value["rank_records"][0]["optimizer_steps"] += 1
    elif mutation == "count":
        value["sampled_training_examples"] += 1
    elif mutation == "positive":
        path = root / "sft_positive.jsonl"
        value = {"_parse_error": True}
    elif mutation == "served":
        path = root / "checkpoint_verified.json"
        value = json.loads(path.read_text())
        value["served_binding"] = {"checkpoint_path": "/WRONG", "weights": []}
    else:
        path = root / "checkpoint_trained.json"
        value = json.loads(path.read_text())
        value["checkpoint_path"] = "/WRONG"
    path.write_text(json.dumps(value))
    assert _summarize(run, ctx)["model_sft"]["status"] != "completed"


def test_fix4_production_model_round_completes_entire_protocol(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    rows = [json.loads(line) for line in (run / "round_2/attempts/attempt_1/candidate/train_trajectories.jsonl").read_text().splitlines()]
    assert all("checkpoint_manifest" not in row for row in rows)
    result = _summarize(run, ctx)
    assert result["protocol_complete"], result["protocol_complete_blockers"]
    assert result["run_complete"], result["run_complete_blockers"]
    assert _rounds(result)[2]["products_complete"]


def test_fix4_task_hash_even_with_consistent_scope_fingerprint(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    root = run / "round_1/rollout_cache"
    old = next(root.iterdir())
    sample = json.loads(next((old / "train_rollouts").glob("*.json")).read_text())
    scope = sample["binding"]["round_scope"]
    scope["task_hash"] = "0" * 64
    new_hash = _fingerprint_fixture(scope)
    old.rename(root / new_hash)
    for path in (root / new_hash).rglob("*.json"):
        value = json.loads(path.read_text())
        value["binding"].update(state_hash=new_hash, round_scope=scope)
        if "row" in value:
            value["row"]["state_hash"] = new_hash
        path.write_text(json.dumps(value))
    path = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["state_hash"] = new_hash
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


def test_fix4_actual_artifact_snapshot_relocation(tmp_path):
    from rsiflow.task_meta.storage import artifact_manifest
    m = _load_summarizer()
    run = tmp_path / "run"
    run.mkdir()
    directory = run / "artifacts"
    directory.mkdir()
    (directory / "answer.txt").write_text("actual task content")
    state = _task_state(0, _harness_file(run, 0))
    state["artifacts"] = {"directory": str(directory), "manifest": artifact_manifest(directory)}
    snap = run / "snapshots/A0"
    _fixture_snapshot(run, snap, None, None, state)
    ok, saved = m._snapshot_valid(snap / "snapshot_receipt.json", snap)
    assert ok
    assert saved["selected"]["artifacts"]["directory"] != str(directory)
    assert m._identity_equal(state, saved["selected"])
    (Path(saved["selected"]["artifacts"]["directory"]) / "answer.txt").write_text("tamper")
    assert not m._snapshot_valid(snap / "snapshot_receipt.json", snap)[0]


def test_fix4_model_attempt_10_is_not_attempt_1(tmp_path):
    import shutil
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    attempt = run / "round_2/attempts/attempt_1"
    foreign = attempt.with_name("attempt_10") / "candidate"
    shutil.copytree(attempt / "candidate", foreign)
    path = run / "controller_transport/sft_2/result.json"
    value = json.loads(path.read_text())
    value.update(training_metrics_path=str(foreign / "model_update/training_metrics.json"),
                 candidate_state_path=str(foreign / "candidate_state.json"))
    path.write_text(json.dumps(value))
    parent = json.loads((attempt / "before/parent_task.json").read_text())
    assert not _load_summarizer()._model_round_evidence(run, 2, parent)[0]


def test_fix4_production_compact_projection_keeps_call_evidence(tmp_path):
    from rsiflow.task_meta.runtime_extensions import compact_rollout
    run, _, ctx = _build_run(tmp_path, complete=True)
    cache = run / "round_1/rollout_cache"
    rows = []
    for task_id in TASK_IDS:
        receipt_path = next(p for p in cache.glob("*/train_rollouts/*.json")
                            if json.loads(p.read_text())["row"]["task_id"] == task_id)
        full = json.loads(receipt_path.read_text())["row"]
        compact = compact_rollout(full, receipt_path)
        for key in ("round_id", "source_role", "purpose", "collection_stage", "manifest_hash"):
            compact[key] = full.get(key)
        rows.append(compact)
    path = run / "round_1/parent/train_trajectories.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = _summarize(run, ctx)
    assert _rounds(result)[1]["products_complete"]
    assert _rounds(result)[1]["calls"]["parent"]["transport_calls"] > 0


@pytest.mark.parametrize("field", ["model_ref", "harness_sha256", "artifact_input_manifest",
                                   "parent_policy_hash", "success_verifier_version", "trajectory_id"])
def test_fix4_consistently_wrong_task_row_rejected(tmp_path, field):
    run, _, ctx = _build_run(tmp_path, complete=True)
    trajectory = run / "round_1/parent/train_trajectories.jsonl"
    rows = [json.loads(line) for line in trajectory.read_text().splitlines()]
    rows[0][field] = "WRONG"
    trajectory.write_text("".join(json.dumps(row) + "\n" for row in rows))
    path = next(p for p in (run / "round_1/rollout_cache").glob("*/train_rollouts/*.json")
                if json.loads(p.read_text())["row"]["task_id"] == rows[0]["task_id"])
    value = json.loads(path.read_text())
    value["row"] = rows[0]
    path.write_text(json.dumps(value))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


def test_fix4_request_call_envelope_rehashed_still_rejected(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    _, path = _first_parent_call(run)
    value = json.loads(path.read_text())
    value["request"]["call_id"] = 999
    value["request_sha256"] = _value_hash(value["request"])
    path.write_text(json.dumps(value))
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


def _fix4_padding_metrics():
    # Accelerate shards batches across ranks; tail padding repeats the head of
    # the same shuffled epoch, it cannot replace arbitrary training examples.
    ranks = [{"rank": rank, "local_rank": rank, "sampled_indices": indices, "optimizer_steps": 1}
             for rank, indices in enumerate(([0, 4], [1, 0], [2, 1], [3, 2]))]
    return {"rank_records": ranks, "sampled_row_indices": [i for r in ranks for i in r["sampled_indices"]],
            "sampled_training_examples": 8, "positive_trajectories": 5, "optimizer_steps": 1,
            "world_size": 4, "num_train_epochs_completed": 1.0, "gradient_accumulation_steps": 8, "batch_size": 1}


@pytest.mark.parametrize("mutation", ["padding", "epoch_bool", "steps_bool"])
def test_fix4_padding_and_numeric_schema_fail_closed(mutation):
    m = _load_summarizer()
    metrics = _fix4_padding_metrics()
    if mutation == "padding":
        for rank, extra in zip(metrics["rank_records"][1:], (1, 2, 3)):
            rank["sampled_indices"][-1] = extra
        metrics["sampled_row_indices"] = [i for r in metrics["rank_records"] for i in r["sampled_indices"]]
    elif mutation == "epoch_bool":
        metrics["num_train_epochs_completed"] = True
    else:
        metrics["optimizer_steps"] = True
    assert not m._sampling_valid(metrics, 5)


def test_fix4_legal_sampler_padding_passes():
    assert _load_summarizer()._sampling_valid(_fix4_padding_metrics(), 5)


def test_fix4_real_task5_sampling_readonly():
    path = ROOT.parent / "Rollout_logs/deepcoder_model_smoke_v2/runs/deepcoder_model_smoke/sft/model_update/training_metrics.json"
    if not path.is_file():
        pytest.skip("real Task5 training metrics unavailable")
    metrics = json.loads(path.read_text())
    assert _load_summarizer()._sampling_valid(metrics, metrics["positive_trajectories"])


def test_fix4_final_artifact_directory_must_match_claimed_manifest(tmp_path):
    from rsiflow.task_meta.storage import artifact_manifest
    m = _load_summarizer()
    directory = tmp_path / "active_artifacts"
    directory.mkdir()
    (directory / "answer.txt").write_text("valid content")
    state = _task_state(1, _harness_file(tmp_path, 1))
    state["artifacts"] = {"directory": str(directory), "manifest": artifact_manifest(directory)}
    summary = {"round": 1, "parent_state": state, "candidate_state": state,
               "snapshot_states": {"selected": state}, "selection": {"decision": "retain_parent"}}
    assert not m._active_chain([summary], state, m._task_identity(state))
    (directory / "answer.txt").write_text("unrelated content")
    assert m._active_chain([summary], state, m._task_identity(state))


def test_fix4_unaccounted_persisted_calls_block_completion(tmp_path):
    import shutil
    run, _, ctx = _build_run(tmp_path, complete=True)
    directory, call_path = _first_parent_call(run)
    original = call_path.parent.with_suffix(".json")
    foreign = json.loads(original.read_text())
    foreign["binding"]["task"] = "UNLISTED_TASK"
    (directory / "foreign.json").write_text(json.dumps(foreign))
    shutil.copytree(call_path.parent, directory / "foreign.calls")
    assert not _rounds(_summarize(run, ctx))[1]["products_complete"]


def test_fix4_cleanup_rejects_gpu_raw_reading_disagreement(tmp_path):
    import hashlib
    run, _, ctx = _build_run(tmp_path, complete=True)
    path = ctx['cleanup_path']
    receipt = json.loads(path.read_text())
    obs = receipt['gpu_observations'][0]
    obs['raw_stdout'] = obs['raw_stdout'].replace('100, 0', '70000, 99')
    obs['stdout_hash'] = hashlib.sha256(obs['raw_stdout'].encode()).hexdigest()
    evidence_path = Path(obs['evidence_path'])
    evidence_path.write_text(json.dumps({k: v for k, v in obs.items() if k not in {'evidence_path', 'evidence_sha256'}}))
    obs['evidence_sha256'] = file_hash(evidence_path)
    receipt['evidence_files_sha256'][str(evidence_path)] = file_hash(evidence_path)
    path.write_text(json.dumps(receipt))
    result = _summarize(run, ctx)
    assert result['protocol_complete'] is True
    assert result['external_cleanup']['ok'] is False
    assert any('raw_reading' in b for b in result['external_cleanup']['blockers'])


def test_fix4_cleanup_requires_runtime_attachment(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    attachment = run / 'meta_session/supervision/0001/post-launch-identity.json'
    if attachment.exists():
        attachment.unlink()
    result = _summarize(run, ctx)
    assert result['protocol_complete'] is True
    assert result['external_cleanup']['ok'] is False


def test_fix4_cleanup_rejects_unbound_job_turn(tmp_path):
    run, _, ctx = _build_run(tmp_path, complete=True)
    # The protocol separately rejects the altered job. Resource cleanup must reject it too.
    jobpath = next((run / 'meta_session/jobs').glob('*/job.json'))
    job = json.loads(jobpath.read_text())
    turn = Path(job['turn_dir']) / 'command.json'
    command = json.loads(turn.read_text()); command['request'] = {'operation': 'foreign'}
    turn.write_text(json.dumps(command))
    result = _summarize(run, ctx)
    assert result['external_cleanup']['ok'] is False


def _fix4_resume_worker(run_path, config_path, replay_path):
    """Child-process test transport; production controller/TaskAdapter execute MODEL reuse.

    Only model generation and later unrelated component operations use prerecorded
    offline products. The SFT spy lives in this process, where dispatch would occur.
    """
    import os
    import subprocess
    import sys
    import time
    from types import SimpleNamespace
    from controller_tools import ControllerTools
    import persistent_meta
    import rsiflow.task_meta.gpu_phases as gpu_phases
    run = Path(run_path)
    replay = json.loads(Path(replay_path).read_text())
    calls = []
    def forbidden_sft(self, arguments):
        calls.append(arguments)
        raise AssertionError('SFT re-dispatched after completed training')
    TaskAdapter.sft = forbidden_sft
    gpu_phases.ensure_services = lambda *args: None
    adapter = TaskAdapter(ROOT, run / 'controller_receipts')
    reused = []
    def model_executor(config, state, number, stage, output):
        assert number == 2 and stage == 'candidate'
        weight = Path(state.checkpoint_path) / 'model.safetensors'
        assert weight.is_file() and weight.stat().st_size > 0
        reused.append(str(weight))
        return SimpleNamespace(execute=lambda task, directory: SimpleNamespace(
            performance=json.loads((output / 'performance.json').read_text()), cost={},
            trajectories=[json.loads(line) for line in (output / 'train_trajectories.jsonl').read_text().splitlines()]))
    adapter._executor = model_executor
    controller = ControllerTools(ROOT, run / 'controller_receipts', adapter=adapter)
    pending = {json.dumps(item['request'], sort_keys=True): item for item in replay}
    class Executor:
        def execute(self, request):
            if request['operation'] == 'sft':
                return controller.execute(request)
            item = pending[json.dumps(request, sort_keys=True)]
            if request['operation'] == 'run_candidate' and request['round_number'] == 2:
                result = controller.execute(request)
                assert result['status'] == 'completed', result
                # Refresh the actual transport result, not a fabricated success flag.
                Path(item['result_path']).write_text(json.dumps(result))
                return result
            return item['receipt']
    loop = PersistentMeta(project=run.parent.parent.parent / 'project', run=run,
                          codex_home=run / 'meta_session/codex_home', executor=Executor(),
                          meta_config={'model': 'offline', 'provider': 'offline'}, rounds=5)
    def inline_job(request, turn_dir):
        item = pending[json.dumps(request, sort_keys=True)]
        result = loop.tools.executor.execute(request)
        if item.get('job_path'):
            path = Path(item['job_path']); job = item['job']
            path.parent.mkdir(parents=True, exist_ok=True)
            for label in ('stdout', 'stderr'):
                log = Path(job['result_path']).parent / (label + '.log'); log.touch()
                job[label + '_path'] = str(log)
            job['created_at'] = 1.0
            job['turn_dir'] = str(turn_dir); path.write_text(json.dumps(job))
        return {'status': 'completed', 'result': result}
    loop.tools.jobs.start = inline_job
    class Server:
        def __init__(self, **kwargs):
            self.process = None
        def start(self):
            self.process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)', str(run)], start_new_session=True)
        def request(self, method, params):
            assert method == 'thread/resume'
            assert params['threadId'] == 'thread-1'
            return {'thread': {'id': params['threadId']}}
        def turn(self, thread_id, prompt, environments):
            deadline = time.monotonic() + 30
            while not (run / 'release-test-transport').exists():
                if time.monotonic() > deadline:
                    raise RuntimeError('collector did not release test transport')
                time.sleep(.02)
            for item in replay:
                request = item['request']
                if request['operation'] == 'finish_experiment':
                    loop.tools('finish_experiment', {'summary': 'completed fixture protocol'})
                else:
                    loop.tools('experiment', {'request': request})
            assert loop.tools.finished, loop.progress.rebuild()
            assert calls == [] and len(reused) == 1
            (run / 'resume-spy.json').write_text(json.dumps({'calls': calls, 'reused': reused}))
            return {'id': 'resume-turn', 'status': 'completed'}
        def close(self):
            if self.process:
                self.process.terminate(); self.process.wait(timeout=10)
    persistent_meta.CodexAppServer = Server
    loop.run_experiment('Continue from the recorded completed SFT; do not repeat it.')


def test_fix4_same_run_model_resume_collector_writer_consumer(tmp_path, monkeypatch):
    import shutil
    import sys
    from test_five_round_lifecycle import _load, _wait, _gpu_observer
    sup, writer = _load('deepcoder_five_round_supervisor'), _load('reconcile_deepcoder_run')
    sup.GLOBAL_GPU_LIFECYCLE_LOCK = writer.GLOBAL_GPU_LIFECYCLE_LOCK = tmp_path / 'lifecycle.lock'
    run, _, ctx = _build_run(tmp_path, complete=True)
    _add_model_round(run, ctx, 2)
    model = run / 'round_2/attempts/attempt_1/candidate/model_update'
    weights = model / 'checkpoint/model.safetensors'
    assert weights.is_file() and weights.stat().st_size > 0
    original = {str(p): p.read_bytes() for p in model.rglob('*') if p.is_file()}
    turns = sorted((run / 'meta_session/turns').iterdir())
    cutoff = next(i for i, t in enumerate(turns) if json.loads((t/'command.json').read_text())['request'].get('operation') == 'sft')
    replay = []
    for turn in turns[cutoff + 1:]:
        request = json.loads((turn/'command.json').read_text())['request']
        item = {'request': request, 'receipt': json.loads((turn/'receipt.json').read_text())}
        for jobpath in (run/'meta_session/jobs').glob('*/job.json'):
            job = json.loads(jobpath.read_text())
            if job['turn_dir'] == str(turn):
                item.update(job_path=str(jobpath), result_path=job['result_path'], job=job)
                jobpath.unlink(); jobpath.parent.rmdir()
        replay.append(item)
        shutil.rmtree(turn)
    for jobpath in (run/'meta_session/jobs').glob('*/job.json'):
        job = json.loads(jobpath.read_text())
        for label in ('stdout', 'stderr'):
            log = Path(job['result_path']).parent / (label + '.log')
            log.write_text('')
            job[label + '_path'] = str(log)
        job['created_at'] = 1.0
        jobpath.write_text(json.dumps(job))
    replay_path = tmp_path / 'replay.json'; replay_path.write_text(json.dumps(replay))
    session = run / 'meta_session'
    state = json.loads((session/'state.json').read_text())
    state.update(status='disconnected', sandbox_backend='legacy_host', capability_revision='native_coding_v1')
    (session/'state.json').write_text(json.dumps(state))
    old = session/'supervision/0001'
    (old/'exit').write_text('143')
    prelaunch = json.loads((old/'prelaunch-seal.json').read_text())
    prelaunch['binding']['supervision_attempt'] = '0002'
    code = "from test_deepcoder_five_round import _fix4_resume_worker; import sys; _fix4_resume_worker(*sys.argv[1:])"
    monkeypatch.setenv('PYTHONPATH', ':'.join([str(ROOT/'tests'), str(ROOT), str(ROOT/'runtime')]))
    sup.launch(ctx['config_path'], '0002', command=[sys.executable, '-B', '-c', code, str(run), str(ctx['config_path']), str(replay_path)],
               timeout='60s', kill_after='2s', skip_asset_gates=True, mode='resume', resumed_from='0001', prelaunch_evidence=prelaunch)
    attempt = session/'supervision/0002'
    try:
        sup.collect_post_launch_identity(ctx['config_path'], '0002', timeout=20, expected_thread='thread-1',
                                        gpu_observer=lambda: {'gpus': prelaunch['expected_gpus']})
    finally:
        (run/'release-test-transport').write_text('continue')
    assert _wait(lambda: (attempt/'exit').is_file(), timeout=60)
    assert (attempt/'exit').read_text().strip() == '0', (attempt/'launcher.log').read_text()
    assert json.loads((run/'resume-spy.json').read_text())['calls'] == []
    assert all(Path(p).read_bytes() == data for p, data in original.items())
    assert (old/'exit').read_text() == '143'
    # Controlled GPU measurement only. Owner observations use real /proc for worker/app.
    previous = json.loads(ctx['cleanup_path'].read_text())
    observation = previous['gpu_observations'][0]
    gpu = {k: v for k, v in observation.items() if k not in {'evidence_path','evidence_sha256'}}
    cleanup = writer.cleanup_run(ctx['config_path'], run, '0002', gpu_observer=_gpu_observer(gpu))
    receipt = json.loads(cleanup.read_text())
    assert receipt['status'] == 'completed', receipt['blockers']
    result = _load_summarizer().summarize(run, config_path=ctx['config_path'], cleanup_receipt_path=cleanup)
    assert result['protocol_complete'] and result['run_complete'], result['run_complete_blockers']
