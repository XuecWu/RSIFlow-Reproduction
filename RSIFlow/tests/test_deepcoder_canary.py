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
from rsiflow.task_meta.data import DOMAINS
from rsiflow.task_meta.pipeline import PipelineConfig, active_domains
from rsiflow.task_meta.types import TaskAgentState
from task_adapter import TaskAdapter


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
        "rounds": 1, "evaluation_schedule": "none", "retry_rejected_from_round": 2,
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
        "rounds": 1, "evaluation_schedule": "none", "retry_rejected_from_round": 2}))
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


def test_canary_prompt_has_terminal_retain_and_no_validation_or_retry(tmp_path):
    config = _canary_launcher_value(tmp_path)
    config.update(evaluation_schedule="none", retry_rejected_from_round=2)

    prompt = experiment_prompt(tmp_path / "launcher.json", config, tmp_path / "run")

    assert "single-domain engineering canary" in prompt
    assert "one attempt" in prompt
    assert "retain_parent is terminal" in prompt
    assert "completed_retained" in prompt
    assert "without activation or reselection" in prompt
    for forbidden in (
        "complete independent baseline validation",
        "Begin with A0 independent evaluation",
        "round=0, then evaluate",
        "After acceptance run the configured independent validation",
        "Only after acceptance request prepare_validation_snapshot",
        "Only after FINAL-round acceptance",
        "744-task cohort",
        "After rejection keep the parent, read the failure evidence and choose again",
        "Rejection returns to component selection",
        "return to step 3 on this SAME round/batch",
        "Meta chooses again after rejection",
        "Continue to the next round",
        "then advance to fresh next-round tasks",
        "Run independent validation only when due under the configured evaluation schedule",
    ):
        assert forbidden not in prompt


@pytest.mark.parametrize("configured_retry, expected", [(2, 2), (None, 1)])
def test_launcher_preserves_retry_boundary_and_defaults_to_one(
        tmp_path, configured_retry, expected):
    from unittest.mock import patch

    value = _canary_launcher_value(tmp_path)
    value["evaluation_schedule"] = "none"
    if configured_retry is not None:
        value["retry_rejected_from_round"] = configured_retry
    config_path = tmp_path / f"launcher-{expected}.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    run_dir = tmp_path / f"run-{expected}"

    class FakeLoop:
        def __init__(self, **kwargs):
            pass

        def run_experiment(self, prompt):
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
        "retry_rejected_from_round": expected,
        "evaluation_schedule": "none",
    }
