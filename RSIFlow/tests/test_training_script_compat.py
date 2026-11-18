from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "runtime/scripts/train_task_meta_sft.py"
SPEC = importlib.util.spec_from_file_location("train_task_meta_sft_compat_test", SCRIPT)
trainer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trainer)


class FakeTrainingArgumentsV4:
    def __init__(self, *, warmup_ratio=0.0, warmup_steps=0):
        self.warmup_ratio = warmup_ratio
        self.warmup_steps = warmup_steps


class FakeTrainingArgumentsV5:
    def __init__(self, *, warmup_steps=0):
        self.warmup_steps = warmup_steps


def training_options_args():
    return SimpleNamespace(steps=None, learning_rate=None, max_length=None, lora_rank=None, seed=None)


@pytest.mark.parametrize("ratio", [0.0, 0.03, 1.0])
def test_v4_warmup_contract_preserves_ratio(ratio):
    kwargs, scheduler_ratio = trainer.warmup_argument_contract(FakeTrainingArgumentsV4, ratio)

    arguments = FakeTrainingArgumentsV4(**kwargs)
    assert kwargs == {"warmup_ratio": ratio}
    assert arguments.warmup_ratio == ratio
    assert arguments.warmup_steps == 0
    assert scheduler_ratio is None


@pytest.mark.parametrize("ratio", [0.0, 0.03, 1.0])
def test_v5_warmup_contract_defers_ratio_to_scheduler(ratio):
    kwargs, scheduler_ratio = trainer.warmup_argument_contract(FakeTrainingArgumentsV5, ratio)

    arguments = FakeTrainingArgumentsV5(**kwargs)
    assert kwargs == {"warmup_steps": 0}
    assert arguments.warmup_steps == 0
    assert scheduler_ratio == ratio


@pytest.mark.filterwarnings("ignore:Can't initialize NVML:UserWarning")
def test_installed_transformers_v5_warmup_kwargs_are_constructible(tmp_path):
    from transformers import TrainingArguments

    assert "warmup_ratio" not in inspect.signature(TrainingArguments).parameters
    kwargs, scheduler_ratio = trainer.warmup_argument_contract(TrainingArguments, 0.03)

    arguments = TrainingArguments(output_dir=str(tmp_path), **kwargs)
    assert arguments.warmup_steps == 0
    assert scheduler_ratio == 0.03


@pytest.mark.parametrize(("ratio", "num_training_steps", "expected"), [
    (0.0, 101, 0),
    (0.03, 101, 4),
    (1.0, 101, 101),
])
def test_warmup_steps_for_ratio_uses_ceiling(ratio, num_training_steps, expected):
    assert trainer.warmup_steps_for_ratio(ratio, num_training_steps) == expected


@pytest.mark.parametrize("ratio", [-0.01, 1.01, float("nan"), float("inf"), True, "0.03"])
def test_training_option_validation_rejects_invalid_warmup_ratio(ratio):
    request = {
        "sft_profile": "multidomain",
        "training": {"max_steps": 1, "warmup_ratio": ratio},
        "supervision": "assistant_all",
    }

    with pytest.raises(ValueError, match="warmup_ratio"):
        trainer.resolve_training_options(request, training_options_args())


def test_multidomain_default_warmup_ratio_remains_in_resolved_options():
    request = {
        "sft_profile": "multidomain",
        "training": {"max_steps": 1},
        "supervision": "assistant_all",
    }

    options = trainer.resolve_training_options(request, training_options_args())
    assert options["warmup_ratio"] == 0.03


class FakeTrainer:
    def __init__(self):
        self.args = SimpleNamespace(warmup_steps=17)
        self.scheduler_calls = []

    def create_scheduler(self, num_training_steps, optimizer=None):
        self.scheduler_calls.append((num_training_steps, optimizer))
        return "scheduler"


def test_v5_scheduler_sets_warmup_steps_from_actual_training_steps():
    audited_type = trainer.create_audited_trainer_class(FakeTrainer, [], 0.03)
    audited = audited_type()

    result = audited.create_scheduler(101, optimizer="optimizer")

    assert result == "scheduler"
    assert audited.args.warmup_steps == 4
    assert audited.scheduler_calls == [(101, "optimizer")]


def test_v4_scheduler_does_not_rewrite_warmup_steps():
    audited_type = trainer.create_audited_trainer_class(FakeTrainer, [], None)
    audited = audited_type()

    result = audited.create_scheduler(101, optimizer="optimizer")

    assert result == "scheduler"
    assert audited.args.warmup_steps == 17
    assert audited.scheduler_calls == [(101, "optimizer")]
