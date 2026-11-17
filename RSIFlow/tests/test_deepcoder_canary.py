"""Offline configuration and executor contracts for the DeepCoder canary."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(ROOT))

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


def test_task_adapter_config_passes_canary_scope(tmp_path, monkeypatch):
    value = json.loads((ROOT / "configs/train_4b.json").read_text(encoding="utf-8"))
    value.update(
        output_root=str(tmp_path / "output"),
        experiment_scope="deepcoder_canary",
        scope_train_limit=16,
        scope_probe_limit=16,
    )
    config_path = tmp_path / "launcher.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(
        "evolution_protocol.training_quotas",
        lambda value: {"envscaler": 0, "deepcoder_taco": 16},
    )
    config = TaskAdapter(ROOT, tmp_path / "receipts")._config({"config_path": str(config_path)})
    assert config.experiment_scope == "deepcoder_canary"
    assert config.scope_train_limit == 16
    assert config.scope_probe_limit == 16
    assert config.window_quotas == CANARY_QUOTAS
    assert active_domains(config) == ("code",)
