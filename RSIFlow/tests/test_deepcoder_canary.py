"""Offline configuration and executor contracts for the DeepCoder canary."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT))

from experiment_progress import ExperimentProgress
from launch_meta import experiment_prompt, main
import persistent_meta
from rsiflow.task_meta.data import DOMAINS
from rsiflow.task_meta.pipeline import PipelineConfig, active_domains
from rsiflow.task_meta.types import TaskAgentState
from task_adapter import TaskAdapter

PROJECT = ROOT.parent
for entry in (PROJECT / "reproduction/scripts",):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import prepare_deepcoder_canary as canary
from data_protocol import public_text, text_hash
from evolution_protocol import file_hash, fingerprint, freeze, make_manifest


CANARY_QUOTAS = {"tool_use": 0, "code": 16, "searchqa": 0}


@pytest.fixture
def canary_config(tmp_path):
    value = json.loads((ROOT / "runtime/configs/base_4b.json").read_text(encoding="utf-8"))
    value.update(
        output_root=str(tmp_path / "output"),
        training_schedule="round_disjoint",
        search_dev_fraction=0,
        mode="full",
        task_replicas=[
            {"gpu": gpu, "base_url": f"http://127.0.0.1:{8371 + gpu}/v1"}
            for gpu in range(4)
        ],
    )
    value["meta"]["run_mode"] = "full"
    return PipelineConfig.model_validate(value)


def test_deepcoder_canary_is_the_only_zero_quota_scope(canary_config):
    canary_config.experiment_scope = "deepcoder_canary"
    canary_config.window_quotas = {"tool_use": 0, "code": 16, "searchqa": 0}
    canary_config.probe_per_domain = dict(canary_config.window_quotas)
    assert active_domains(canary_config.checked()) == ("code",)

    canary_config.experiment_scope = "multidomain"
    with pytest.raises(ValueError, match="All three domains require positive fixed quotas"):
        canary_config.checked()


@pytest.mark.parametrize("quotas", [
    {"tool_use": 0, "code": 7, "searchqa": 0},
    {"code": 16},
    {"tool_use": 1, "code": 16, "searchqa": 0},
])
def test_deepcoder_canary_rejects_wrong_fixed_quotas(canary_config, quotas):
    canary_config.experiment_scope = "deepcoder_canary"
    canary_config.window_quotas = dict(quotas)
    canary_config.probe_per_domain = dict(quotas)
    with pytest.raises(ValueError):
        canary_config.checked()


@pytest.mark.parametrize("field", ["window_quotas", "probe_per_domain"])
def test_deepcoder_canary_requires_both_fixed_quota_maps(canary_config, field):
    canary_config.experiment_scope = "deepcoder_canary"
    canary_config.window_quotas = dict(CANARY_QUOTAS)
    canary_config.probe_per_domain = dict(CANARY_QUOTAS)
    setattr(canary_config, field, {**CANARY_QUOTAS, "code": 7})
    with pytest.raises(ValueError):
        canary_config.checked()


def test_active_domains_preserves_existing_scopes(canary_config):
    canary_config.experiment_scope = "multidomain"
    assert active_domains(canary_config) == DOMAINS
    canary_config.experiment_scope = "envscaler_validation"
    assert active_domains(canary_config) == ("tool_use",)


def test_task_adapter_executor_uses_code_only_domains(canary_config, tmp_path, monkeypatch):
    canary_config.experiment_scope = "deepcoder_canary"
    canary_config.window_quotas = dict(CANARY_QUOTAS)
    canary_config.probe_per_domain = dict(CANARY_QUOTAS)
    config = canary_config.checked()
    captured = {}

    class Executor:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    class Store:
        current = {"manifest_hash": "offline-fixture"}
        members = {}

        def __init__(self, path):
            self.path = path

        def select_round(self, round_number):
            self.round_number = round_number

    monkeypatch.setattr("rsiflow.task_meta.pipeline_execution.MultiDomainExecutor", Executor)
    monkeypatch.setattr("rsiflow.task_meta.round_evolution.RoundStore", Store)
    monkeypatch.setattr(
        "rsiflow.task_meta.pipeline.adapter_factory",
        lambda config: (lambda domain: None, None),
    )
    adapter = TaskAdapter(ROOT, tmp_path / "receipts")
    state = TaskAgentState(
        0, "Qwen3-4B", str(ROOT / "seed_harness/rsiflow_base_manifest.json"),
    )
    adapter._executor(config, state, 1, "parent", tmp_path / "round_1/parent")
    assert captured["expected_domains"] == ("code",)


def _canary_launcher_value(tmp_path):
    value = json.loads((ROOT / "configs/train_4b.json").read_text(encoding="utf-8"))
    value.update(
        output_root=str(tmp_path / "output"),
        experiment_scope="deepcoder_canary",
        scope_train_limit=16,
        scope_probe_limit=16,
        rounds=1,
        allocated_tasks=16,
        training_tasks_per_pass=16,
        train_quotas_per_round={
            "envscaler": 0, "deepcoder_taco": 16, "hotpotqa": 0, "2wiki": 0,
        },
    )
    return value


def test_task_adapter_config_passes_canary_scope(tmp_path):
    value = _canary_launcher_value(tmp_path)
    config_path = tmp_path / "launcher.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    config = TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(config_path)})
    assert config.experiment_scope == "deepcoder_canary"
    assert config.scope_train_limit == 16
    assert config.scope_probe_limit == 16
    assert config.window_quotas == CANARY_QUOTAS
    assert config.round_protocol["train_quotas_per_round"] == CANARY_QUOTAS
    assert config.max_generations == 1
    assert active_domains(config) == ("code",)


@pytest.mark.parametrize("scope", ["multidomain", "envscaler_validation"])
def test_non_canary_launcher_still_rejects_zero_source_quotas(tmp_path, scope):
    value = _canary_launcher_value(tmp_path)
    value["experiment_scope"] = scope
    config_path = tmp_path / f"{scope}.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="Training quotas must be positive integer task counts"):
        TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(config_path)})


@pytest.mark.parametrize("override, message", [
    ({"training_tasks_per_pass": 15}, "Training task count does not match source quotas"),
    ({"rounds": 2}, "Disjoint round count must match allocated_tasks"),
])
def test_canary_launcher_preserves_training_count_consistency(tmp_path, override, message):
    value = _canary_launcher_value(tmp_path)
    value.update(override)
    config_path = tmp_path / "inconsistent.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(config_path)})


def test_canary_schedule_excludes_all_validation_milestones(tmp_path):
    (tmp_path / "workflow_policy.json").write_text(json.dumps({
        "rounds": 1, "experiment_scope": "deepcoder_canary",
        "evaluation_schedule": "none", "retry_rejected_from_round": 2,
    }), encoding="utf-8")

    state = ExperimentProgress(tmp_path, rounds=1).rebuild()

    for stage in state["stages"]:
        assert "validation_snapshot" not in stage["required_milestones"]
        assert "independent_validation" not in stage["required_milestones"]
    assert "one attempt" in state["mainline_goal"]
    assert "completed_retained" in state["mainline_goal"]


def test_final_only_schedule_retains_validation_milestones_only_at_b5(tmp_path):
    (tmp_path / "workflow_policy.json").write_text(json.dumps({
        "rounds": 5, "evaluation_schedule": "final_only",
    }), encoding="utf-8")

    state = ExperimentProgress(tmp_path, rounds=5).rebuild()

    for stage in state["stages"]:
        required = stage["required_milestones"]
        if stage["id"] == "B5":
            assert "validation_snapshot" in required
            assert "independent_validation" in required
        else:
            assert "validation_snapshot" not in required
            assert "independent_validation" not in required


def test_canary_rejection_can_finish_without_reselection(tmp_path):
    (tmp_path / "workflow_policy.json").write_text(json.dumps({
        "rounds": 1, "experiment_scope": "deepcoder_canary",
        "evaluation_schedule": "none", "retry_rejected_from_round": 2}))
    progress = ExperimentProgress(tmp_path, rounds=1)

    def record(number, operation, round_number, **request):
        turn = tmp_path / "turns" / f"{number:04d}"
        turn.mkdir(parents=True)
        request = {"operation": operation, "round_number": round_number, **request}
        receipt = {"operation": operation, "status": {
            "compare_scores": "compared", "snapshot_task_meta": "snapshotted"
        }.get(operation, "completed")}
        if operation == "sft":
            receipt["candidate_state_path"] = str(tmp_path / "candidate.json")
        if operation == "append_skills":
            receipt["appended_count"] = 1
        (turn / "command.json").write_text(json.dumps({"action": "tool", "request": request}))
        (turn / "receipt.json").write_text(json.dumps(receipt))
        progress.record_turn(turn)

    operations = [
        ("bootstrap", 0), ("snapshot_task_meta", 0),
        ("run_parent", 1), ("prepare_meta_evidence", 1),
        ("write_text", 1), ("sft", 1), ("run_candidate", 1),
        ("compare_scores", 1), ("write_text", 1), ("append_skills", 1),
        ("write_text", 1), ("snapshot_task_meta", 1),
    ]
    paths = {4: "round_1/attempts/attempt_1/decision.json",
             8: "round_1/attempts/attempt_1/selection.json",
             10: "meta/context.json"}
    for index, (operation, round_number) in enumerate(operations):
        extra = {"path": paths[index]} if index in paths else {}
        if index == 8:
            extra["content"] = json.dumps({"accept_or_retain": "retain_parent"})
        record(index, operation, round_number, **extra)
    assert progress.rebuild()["finish_allowed"] is True


def _write_canary_policy(run):
    session = run / "meta_session"
    session.mkdir(parents=True, exist_ok=True)
    (session / "workflow_policy.json").write_text(json.dumps({
        "rounds": 1, "experiment_scope": "deepcoder_canary",
        "evaluation_schedule": "none", "retry_rejected_from_round": 2,
    }), encoding="utf-8")
    return session


def _prompt_protocol(prompt):
    payload = prompt.split("Experiment facts and required sequence:\n", 1)[1]
    return json.loads(payload.split("\nMainline stages:\n", 1)[0])


def _assert_canary_runtime_instructions(text):
    assert "completed_retained" in text
    for forbidden in (
        "Rejection retains the parent and returns to component selection",
        "There is no fixed retry limit",
        "The next attempt/round reads relevant entries",
        "Finish only through finish_experiment after all required rounds, snapshots and validations",
        "Rejection returns to component selection",
        "return to step 3 on this SAME round/batch",
        "Meta chooses again after rejection",
        "Continue to the next round",
        "then advance to fresh next-round tasks",
        "Run independent validation only when due under the configured evaluation schedule",
        "Reuse unchanged parent results across rejected attempts",
        "handoff for the next round",
    ):
        assert forbidden not in text


def test_canary_prompt_has_terminal_retain_and_no_validation_or_retry(tmp_path):
    config = _canary_launcher_value(tmp_path)
    config.update(evaluation_schedule="none", retry_rejected_from_round=2)
    run = tmp_path / "run"
    _write_canary_policy(run)

    prompt = experiment_prompt(tmp_path / "launcher.json", config, run)

    assert _prompt_protocol(prompt)["workflow_policy"]["retry_rejected_from_round"] == 2
    assert "single-domain engineering canary" in prompt
    assert "one attempt" in prompt
    assert "retain_parent is terminal" in prompt
    assert "completed_retained" in prompt
    assert "without activation or reselection" in prompt
    _assert_canary_runtime_instructions(prompt)
    for forbidden in (
        "complete independent baseline validation",
        "Begin with A0 independent evaluation",
        "round=0, then evaluate",
        "After acceptance run the configured independent validation",
        "Only after acceptance request prepare_validation_snapshot",
        "Only after FINAL-round acceptance",
        "744-task cohort",
        "After rejection keep the parent, read the failure evidence and choose again",
        "structured handoff for the next round",
    ):
        assert forbidden not in prompt


def test_five_round_multidomain_none_prompt_fails_closed(tmp_path):
    config_path = PROJECT / "reproduction/configs/train_4b_v5.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["evaluation_schedule"] = "none"

    with pytest.raises(ValueError, match="evaluation_schedule=none.*deepcoder_canary"):
        experiment_prompt(config_path, config, tmp_path / "run")


def test_five_round_multidomain_none_progress_fails_closed(tmp_path):
    (tmp_path / "workflow_policy.json").write_text(json.dumps({
        "rounds": 5,
        "experiment_scope": "multidomain",
        "evaluation_schedule": "none",
        "retry_rejected_from_round": 1,
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="evaluation_schedule=none.*deepcoder_canary"):
        ExperimentProgress(tmp_path, rounds=5).rebuild()
    assert not (tmp_path / "workflow_state.json").exists()


@pytest.mark.parametrize("scope", ["multidomain", "envscaler_validation"])
def test_non_canary_none_launcher_check_fails_closed(tmp_path, scope):
    value = json.loads((PROJECT / "reproduction/configs/train_4b_v5.json").read_text(
        encoding="utf-8"))
    value.update(experiment_scope=scope, evaluation_schedule="none")
    config_path = tmp_path / f"{scope}.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ValueError, match="evaluation_schedule=none.*deepcoder_canary"):
        main(["--config", str(config_path), "--check"])


def test_one_round_multidomain_none_retry_two_does_not_unlock_canary_instructions():
    policy = {
        "rounds": 1,
        "experiment_scope": "multidomain",
        "evaluation_schedule": "none",
        "retry_rejected_from_round": 2,
    }

    assert not persistent_meta._single_attempt_canary(policy)
    assert persistent_meta.persistent_instructions(policy) == persistent_meta.PERSISTENT_META_INSTRUCTIONS
    assert persistent_meta.mainline_reminder(policy) == persistent_meta.MAINLINE_REMINDER


@pytest.mark.parametrize("configured_retry, expected, schedule", [
    (2, 2, "none"),
    (None, 1, "after_each_committed_round"),
])
def test_launcher_preserves_retry_boundary_and_defaults_to_one(
        tmp_path, configured_retry, expected, schedule):
    from unittest.mock import patch

    value = _canary_launcher_value(tmp_path)
    value["evaluation_schedule"] = schedule
    if configured_retry is not None:
        value["retry_rejected_from_round"] = configured_retry
    config_path = tmp_path / f"launcher-{expected}.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    run_dir = tmp_path / f"run-{expected}"
    captured = {}

    class FakeLoop:
        def __init__(self, **kwargs):
            pass

        def run_experiment(self, prompt):
            captured["prompt"] = prompt
            return {"status": "complete", "thread_id": "offline", "summary": "done"}

    with (patch("launch_meta._autodl_key", return_value="offline-test"),
          patch("launch_meta.prepare_meta_storage"),
          patch("launch_meta.prepare_skills_path"),
          patch("launch_meta.prepare_context_path"),
          patch("launch_meta.prepare_codex_home", return_value=tmp_path / "codex-home"),
          patch("launch_meta.SubprocessToolExecutor", return_value=object()),
          patch("launch_meta.PersistentMeta", FakeLoop)):
        assert main(["--config", str(config_path), "--run-dir", str(run_dir)]) == 0

    policy = json.loads((run_dir / "meta_session/workflow_policy.json").read_text())
    assert policy == {
        "rounds": 1,
        "experiment_scope": "deepcoder_canary",
        "retry_rejected_from_round": expected,
        "evaluation_schedule": schedule,
    }
    assert _prompt_protocol(captured["prompt"])["workflow_policy"] == policy


class _OfflineExecutor:
    def execute(self, request):
        operation = request["operation"]
        receipt = {"operation": operation, "status": {
            "compare_scores": "compared", "snapshot_task_meta": "snapshotted",
            "write_text": "written",
        }.get(operation, "completed")}
        if operation == "sft":
            receipt["candidate_state_path"] = "candidate.json"
        if operation == "append_skills":
            receipt["appended_count"] = 1
        return receipt


def _real_persistent_meta(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    run = tmp_path / "run"
    _write_canary_policy(run)
    return persistent_meta.PersistentMeta(
        project=project, run=run, codex_home=run / "meta_session/codex_home",
        executor=_OfflineExecutor(),
        meta_config={"model": "offline-model", "provider": "offline-provider"}, rounds=1,
    )


def _advance_to_context_updated(loop):
    assert isinstance(loop.tools, persistent_meta.NativeExperimentTools)

    def complete_job(request, turn_dir):
        return {"status": "completed", "result": loop.tools.executor.execute(request)}

    loop.tools.jobs.start = complete_job
    operations = [
        ("bootstrap", 0, {}), ("snapshot_task_meta", 0, {}),
        ("run_parent", 1, {}), ("prepare_meta_evidence", 1, {}),
        ("write_text", 1, {"path": str(loop.run / "round_1/attempts/attempt_1/decision.json")}),
        ("sft", 1, {}), ("run_candidate", 1, {}), ("compare_scores", 1, {}),
    ]
    for operation, round_number, extra in operations:
        loop.tools("experiment", {"request": {
            "operation": operation, "round_number": round_number, **extra,
        }})
    assert loop.progress.rebuild()["next_milestone"] == "selection_recorded"

    selection_response = loop.tools("experiment", {"request": {
        "operation": "write_text", "round_number": 1,
        "path": str(loop.run / "round_1/attempts/attempt_1/selection.json"),
        "content": json.dumps({"accept_or_retain": "retain_parent"}),
    }})
    assert loop.progress.rebuild()["next_milestone"] == "skills_appended"

    context_response = loop.tools("experiment", {"request": {
        "operation": "append_skills", "round_number": 1,
        "path": str(loop.run / "meta/skills.jsonl"),
    }})
    assert loop.progress.rebuild()["next_milestone"] == "context_updated"
    return selection_response, context_response


def test_real_persistent_meta_initial_developer_and_resume_instructions(tmp_path, monkeypatch):
    loop = _real_persistent_meta(tmp_path)
    _advance_to_context_updated(loop)
    config = _canary_launcher_value(tmp_path)
    config.update(evaluation_schedule="none", retry_rejected_from_round=2)
    initial = experiment_prompt(tmp_path / "launcher.json", config, loop.run)
    captured = {"requests": [], "turns": []}

    class Server:
        def __init__(self, **kwargs):
            self.process = type("Process", (), {"pid": 12345})()

        def start(self):
            return self

        def request(self, method, params):
            captured["requests"].append((method, params))
            return {"thread": {"id": "thread-offline"}}

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
    assert captured["requests"][0][0] == "thread/start"
    developer = captured["requests"][0][1]["developerInstructions"]
    assert len(captured["turns"]) == 2
    for text in (developer, captured["turns"][0], captured["turns"][1]):
        _assert_canary_runtime_instructions(text)
    resume = captured["turns"][1]
    assert "Next required milestone: context_updated" in resume
    assert "final handoff" in resume
    assert _prompt_protocol(captured["turns"][0])["workflow_policy"][
        "retry_rejected_from_round"] == 2


def test_real_native_tool_retain_response_uses_canary_reminder(tmp_path):
    loop = _real_persistent_meta(tmp_path)
    selection_response, context_response = _advance_to_context_updated(loop)

    for response in (selection_response, context_response):
        assert "<MAINLINE_PROGRESS>" in response
        _assert_canary_runtime_instructions(response)
    assert "Next required milestone: context_updated" in context_response
    assert "final handoff" in context_response


def _canary_task(tmp_path, index, source="deepcoder_taco", interface="function"):
    native_id = f"{source}-{interface}-{index}"
    row = {"task_id": native_id, "prompt": f"def task_{index}():\n    ",
           "problem": f"def task_{index}():\n    ", "interface": interface}
    if source != "deepcoder_taco":
        row = {"_id": native_id, "question": f"question {index}", "answer": "answer"}
    pack = tmp_path / f"source/B1/{source}-{interface}-{index}.jsonl"
    pack.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(row) + "\n").encode()
    pack.write_bytes(payload)
    task_id = f"{source}:{native_id}"
    return {"source": source, "domain": "code" if source == "deepcoder_taco" else "searchqa",
            "task_id": task_id, "native_id": native_id,
            "content_hash": text_hash(public_text(row, source)),
            "group_id": fingerprint([task_id]), "strata": {"interface": interface},
            "version": "fixture-v1", "original_split": "train", "role": "train_evolution",
            "round_id": 1, "purpose": "evolution_train", "exposure": "exposure_unknown",
            "date": None, "record_file": str(pack), "record_offset": 0,
            "record_length": len(payload), "record_sha256": file_hash(pack),
            "source_sha256": file_hash(pack)}


@pytest.fixture
def release_world(tmp_path, monkeypatch):
    monkeypatch.setattr(canary.smoke, "PROJECT", tmp_path)
    (tmp_path / ".gitignore").write_text("**/logs/\n", encoding="utf-8")
    tasks = []
    for index in range(18):
        tasks.append(_canary_task(tmp_path, index))
        if index < 3:
            tasks.append(_canary_task(tmp_path, index, interface="stdio"))
            tasks.append(_canary_task(tmp_path, index, source="hotpotqa", interface=None))
    parent = make_manifest("train_evolution", 1, tasks, split_seed=42)
    parent_path = tmp_path / "source/B1/manifest.json"
    freeze(parent_path, parent)
    retrieval = tmp_path / "retrieval/search.sqlite"
    retrieval.parent.mkdir(parents=True)
    retrieval.write_bytes(b"fixture-retrieval")
    freeze(retrieval.with_suffix(".sqlite.manifest.json"),
           {"sha256": file_hash(retrieval), "sources": {}})
    launcher_path = tmp_path / "launcher.json"
    freeze(launcher_path, {"split_seed": 42, "retrieval_index": str(retrieval)})
    return {"parent": parent, "parent_path": parent_path, "launcher_path": launcher_path,
            "retrieval": retrieval}


def test_selection_takes_first_sixteen_deepcoder_function_records(release_world):
    selection = canary.select_deepcoder_function(release_world["parent"])
    expected = [f"deepcoder_taco:deepcoder_taco-function-{index}" for index in range(16)]

    assert selection["task_ids"] == expected
    assert [task["task_id"] for task in selection["tasks"]] == expected
    assert [record["task_id"] for record in selection["records"]] == expected
    assert {task["source"] for task in selection["tasks"]} == {"deepcoder_taco"}
    assert {task["strata"]["interface"] for task in selection["tasks"]} == {"function"}


def test_selection_rejects_fewer_than_sixteen_eligible_records(release_world):
    parent = dict(release_world["parent"])
    parent["tasks"] = [task for task in parent["tasks"]
                       if task["source"] == "deepcoder_taco"
                       and task["strata"]["interface"] == "function"][:15]

    with pytest.raises(ValueError, match="fewer than sixteen"):
        canary.select_deepcoder_function(parent)


def test_release_is_deterministic_and_preserves_source_bytes_and_hashes(release_world, tmp_path):
    source_paths = {task["record_file"] for task in release_world["parent"]["tasks"]}
    source_paths.update({str(release_world["retrieval"]),
                         str(release_world["retrieval"].with_suffix(".sqlite.manifest.json"))})
    hashes_before = {path: file_hash(Path(path)) for path in sorted(source_paths)}

    first = canary.build_release(tmp_path / "logs/first", release_world["launcher_path"],
                                 release_world["parent_path"])
    second = canary.build_release(tmp_path / "logs/second", release_world["launcher_path"],
                                  release_world["parent_path"])
    hashes_after = {path: file_hash(Path(path)) for path in sorted(source_paths)}
    release_tasks = json.loads(Path(first["release"]["manifest_path"]).read_text())["tasks"]
    first_selection = json.loads((tmp_path / "logs/first/selection.json").read_text())
    second_selection = json.loads((tmp_path / "logs/second/selection.json").read_text())
    ready = json.loads((tmp_path / "logs/first/READY.json").read_text())

    assert first["release"]["task_count"] == 16
    assert {task["source"] for task in release_tasks} == {"deepcoder_taco"}
    assert {task["strata"]["interface"] for task in release_tasks} == {"function"}
    assert all(check["bytes_identical"] for check in first["release"]["record_checks"])
    assert first["release"]["source_assets_unchanged"] is True
    assert hashes_after == hashes_before
    assert first["source_asset_hashes_before"] == hashes_before
    assert first["source_asset_hashes_after"] == hashes_before
    assert first_selection["task_ids"] == second_selection["task_ids"]
    assert [task["content_hash"] for task in first_selection["tasks"]] == [
        task["content_hash"] for task in second_selection["tasks"]]
    assert first_selection["rule"] == second_selection["rule"] == canary.SELECTION_RULE
    assert (tmp_path / "logs/first/selection.json").read_bytes() == (
        tmp_path / "logs/second/selection.json").read_bytes()
    assert ready["smoke_only"] is True
    assert ready["feedback_role"] == "train_evolution"
    assert ready["explicit_full_benchmark"] is False
    assert ready["model_calls"] == 0


def test_release_refuses_existing_or_symlink_output_root(release_world, tmp_path):
    existing = tmp_path / "logs/existing"
    existing.mkdir(parents=True)
    with pytest.raises(canary.SmokeBlocked, match="must not exist"):
        canary.build_release(existing, release_world["launcher_path"], release_world["parent_path"])

    linked = tmp_path / "logs/linked"
    linked.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(canary.SmokeBlocked, match="must not be a symlink"):
        canary.build_release(linked, release_world["launcher_path"], release_world["parent_path"])


def test_machine_config_changes_only_canary_fields_and_is_accepted(tmp_path):
    parent_path = PROJECT / "reproduction/configs/train_4b_v5.json"
    canary_path = PROJECT / "reproduction/configs/train_4b_deepcoder_canary.json"
    parent = json.loads(parent_path.read_text())
    value = json.loads(canary_path.read_text())
    expected = {
        "rounds": 1,
        "allocated_tasks": 16,
        "training_tasks_per_pass": 16,
        "train_quotas_per_round": {
            "envscaler": 0, "deepcoder_taco": 16, "hotpotqa": 0, "2wiki": 0,
        },
        "experiment_scope": "deepcoder_canary",
        "evaluation_schedule": "none",
        "retry_rejected_from_round": 2,
        "scope_train_limit": 16,
        "scope_probe_limit": 16,
        "meta_skill_seed_path": None,
        "data_release": "deepcoder_function_16_canary_v1",
        "frozen_data_dir": "/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/reproduction/logs/deepcoder-canary-16-v1/release",
        "output_root": "/data/wuxuecheng/Hierarchical_level/RSI-Flow/Rollout_logs/deepcoder_canary_16_v1",
        "run_name": "deepcoder_function_16_canary_v1",
    }

    assert {key: value[key] for key in expected} == expected
    assert set(value) == set(parent) | {"experiment_scope", "retry_rejected_from_round",
                                       "scope_train_limit", "scope_probe_limit"}
    for key in set(parent) - set(expected):
        assert value[key] == parent[key]
    assert value["output_root"] != parent["output_root"]
    assert value["run_name"] != parent["run_name"]

    adapter = TaskAdapter(ROOT, tmp_path / "receipts")
    effective = adapter._config({"config_path": str(canary_path)})
    parent_effective = adapter._config({"config_path": str(parent_path)})
    assert effective.window_quotas == CANARY_QUOTAS
    assert active_domains(effective) == ("code",)
    assert effective.task_checkpoint == parent_effective.task_checkpoint
    assert effective.meta == parent_effective.meta
    assert effective.training == parent_effective.training
    assert effective.seed == parent_effective.seed == 42


def test_workspace_canary_config_preserves_protocol_and_output_boundaries(tmp_path):
    from rsiflow.task_meta.pipeline import PROJECT as runtime_project

    original_path = PROJECT / "reproduction/configs/train_4b_deepcoder_canary.json"
    workspace_path = PROJECT / "reproduction/configs/train_4b_deepcoder_canary_workspace.json"
    original = json.loads(original_path.read_text())
    value = json.loads(workspace_path.read_text())
    expected_output = Path(
        "/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight/"
        "Rollout_logs/deepcoder_canary_16_v1"
    )
    expected_run = "deepcoder_function_16_canary_workspace_v1"

    assert value == {
        **original,
        "output_root": str(expected_output),
        "run_name": expected_run,
    }
    assert value["rounds"] == 1
    assert value["allocated_tasks"] == value["training_tasks_per_pass"] == 16
    assert value["train_quotas_per_round"] == {
        "envscaler": 0, "deepcoder_taco": 16, "hotpotqa": 0, "2wiki": 0,
    }
    assert value["evaluation_schedule"] == "none"
    assert value["retry_rejected_from_round"] == 2

    adapter = TaskAdapter(ROOT, tmp_path / "receipts")
    effective = adapter._config({"config_path": str(workspace_path)})
    original_effective = adapter._config({"config_path": str(original_path)})
    assert effective.model_dump(exclude={"output_root"}) == original_effective.model_dump(
        exclude={"output_root"}
    )
    assert effective.window_quotas == effective.probe_per_domain == CANARY_QUOTAS
    assert effective.round_protocol["train_quotas_per_round"] == CANARY_QUOTAS
    assert effective.meta == original_effective.meta
    assert effective.training == original_effective.training
    assert effective.seed == original_effective.seed == 42

    output = Path(effective.output_root)
    run = output / "runs" / value["run_name"]
    native_workspace = ROOT.resolve().parent
    configured_workspace = expected_output.parent.parent
    assert native_workspace == PROJECT.resolve()
    assert output == expected_output
    assert output.is_relative_to(configured_workspace)
    assert run.is_relative_to(configured_workspace)
    assert not output.is_relative_to(runtime_project.resolve())
