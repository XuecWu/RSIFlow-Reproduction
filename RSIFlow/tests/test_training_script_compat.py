from __future__ import annotations

import importlib.util
import inspect
import json
import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import ClassVar

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


class ProductionTrainingArgumentsV4(FakeTrainingArgumentsV4):
    def __init__(self, *, warmup_ratio=0.0, warmup_steps=0, **kwargs):
        super().__init__(warmup_ratio=warmup_ratio, warmup_steps=warmup_steps)
        self.kwargs = kwargs

    def get_warmup_steps(self, num_training_steps):
        if self.warmup_steps > 0:
            return self.warmup_steps
        return math.ceil(num_training_steps * self.warmup_ratio)


class ProductionTrainingArgumentsV5(FakeTrainingArgumentsV5):
    def __init__(self, *, warmup_steps=0, **kwargs):
        super().__init__(warmup_steps=warmup_steps)
        self.kwargs = kwargs

    def get_warmup_steps(self, num_training_steps):
        if self.warmup_steps >= 1:
            return int(self.warmup_steps)
        return math.ceil(num_training_steps * self.warmup_steps)


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
def test_installed_transformers_warmup_kwargs_match_signature(tmp_path):
    from transformers import TrainingArguments

    parameters = inspect.signature(TrainingArguments).parameters
    kwargs, scheduler_ratio = trainer.warmup_argument_contract(TrainingArguments, 0.03)
    arguments = TrainingArguments(output_dir=str(tmp_path), **kwargs)

    assert arguments.warmup_steps == 0
    if "warmup_ratio" in parameters:
        assert kwargs == {"warmup_ratio": 0.03}
        assert arguments.warmup_ratio == 0.03
        assert scheduler_ratio is None
    else:
        assert "warmup_steps" in parameters
        assert kwargs == {"warmup_steps": 0}
        assert scheduler_ratio == 0.03


@pytest.mark.filterwarnings("ignore:Can't initialize NVML:UserWarning")
@pytest.mark.parametrize(("num_training_steps", "ratio", "warmup_steps", "step1_lr", "step2_lr"), [
    (5, 0.4, 2, 0.5, 1.0),
    (101, 0.03, 4, 0.25, 0.5),
])
def test_installed_v5_scheduler_observes_translated_warmup(
        tmp_path, num_training_steps, ratio, warmup_steps, step1_lr, step2_lr):
    import torch
    from transformers import Trainer, TrainingArguments

    if "warmup_ratio" in inspect.signature(TrainingArguments).parameters:
        pytest.skip("Real v5 scheduler contract requires a v5-only TrainingArguments signature")

    warmup_kwargs, scheduler_ratio = trainer.warmup_argument_contract(TrainingArguments, ratio)
    arguments = TrainingArguments(
        output_dir=str(tmp_path), lr_scheduler_type="linear", report_to="none", use_cpu=True,
        **warmup_kwargs,
    )
    model = torch.nn.Linear(1, 1)
    optimizer = torch.optim.SGD(model.parameters(), lr=1.0)
    audited_type = trainer.create_audited_trainer_class(Trainer, [], scheduler_ratio)
    audited = audited_type(model=model, args=arguments, optimizers=(optimizer, None))

    scheduler = audited.create_scheduler(num_training_steps=num_training_steps)

    assert audited.args.warmup_steps == warmup_steps
    assert scheduler.optimizer is optimizer
    optimizer.step()
    scheduler.step()
    assert scheduler.get_last_lr() == [pytest.approx(step1_lr)]
    optimizer.step()
    scheduler.step()
    assert scheduler.get_last_lr() == [pytest.approx(step2_lr)]


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


class ToyModel:
    def __init__(self):
        import torch

        self.q_proj_weight = torch.nn.Parameter(torch.zeros(1), requires_grad=False)
        self.lora_weight = torch.nn.Parameter(torch.zeros(1))
        self.config = SimpleNamespace(use_cache=True)

    def named_parameters(self):
        return [("q_proj.weight", self.q_proj_weight), ("lora_weight", self.lora_weight)]

    def merge_and_unload(self, safe_merge=False):
        return self

    def save_pretrained(self, path, **kwargs):
        Path(path).mkdir()


class FakeProductionTrainer:
    instances: ClassVar[list] = []

    def __init__(self, *, model, args, train_dataset, data_collator, processing_class):
        self.model = model
        self.args = args
        self.train_dataset = train_dataset
        self.data_collator = data_collator
        self.processing_class = processing_class
        self.instances.append(self)

    def create_scheduler(self, num_training_steps, optimizer=None):
        self.scheduler_num_training_steps = num_training_steps
        self.scheduler_warmup_steps = self.args.get_warmup_steps(num_training_steps)
        return "scheduler"

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        return 0

    def train(self):
        import torch

        self.compute_loss(self.model, {"sample_index": torch.tensor([0])})
        self.state = SimpleNamespace(epoch=1, global_step=5, max_steps=5)
        self.create_scheduler(num_training_steps=self.state.max_steps)
        with torch.no_grad():
            self.model.q_proj_weight.add_(1)
            self.model.lora_weight.add_(1)
        return SimpleNamespace(training_loss=0.25)


def install_controlled_training_dependencies(monkeypatch, arguments_type):
    import torch
    from rsiflow.task_meta import round_evolution, runtime_extensions

    class FakeAutoModel:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return ToyModel()

    class FakeTokenizer:
        pad_token_id = 0
        eos_token_id = 0

        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeTokenizer()

        def save_pretrained(self, path):
            Path(path).mkdir(exist_ok=True)

    fake_transformers = ModuleType("transformers")
    fake_transformers.AutoModelForCausalLM = FakeAutoModel
    fake_transformers.AutoTokenizer = FakeTokenizer
    fake_transformers.Trainer = FakeProductionTrainer
    fake_transformers.TrainingArguments = arguments_type
    fake_transformers.set_seed = lambda seed: None
    fake_peft = ModuleType("peft")
    fake_peft.LoraConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    fake_peft.get_peft_model = lambda model, config: model
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)
    monkeypatch.setitem(sys.modules, "peft", fake_peft)
    monkeypatch.setattr(round_evolution, "check_lora_modules", lambda model, options: None)
    monkeypatch.setattr(runtime_extensions, "epoch_complete", lambda *args: None)
    monkeypatch.setattr(trainer, "read_positive_rows", lambda *args: [{
        "question_id": "q1", "rollout_id": "r1", "messages": [],
    }])
    monkeypatch.setattr(trainer, "deduplicate_rows", lambda rows: (rows, {
        "input_rows": len(rows), "unique_rows": len(rows), "duplicates": [],
    }))
    monkeypatch.setattr(trainer, "encode_messages", lambda *args, **kwargs: {
        "input_ids": [1], "labels": [1],
    })
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(torch.cuda, "reset_peak_memory_stats", lambda: None)
    monkeypatch.setattr(torch.cuda, "max_memory_allocated", lambda: 0)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda device: "controlled-test-device")


@pytest.mark.parametrize(("arguments_type", "expected_scheduler_ratio"), [
    (ProductionTrainingArgumentsV4, None),
    (ProductionTrainingArgumentsV5, 0.4),
])
def test_train_wires_warmup_contract_scheduler_and_effective_metrics(
        tmp_path, monkeypatch, arguments_type, expected_scheduler_ratio):
    FakeProductionTrainer.instances.clear()
    install_controlled_training_dependencies(monkeypatch, arguments_type)
    monkeypatch.setenv("WORLD_SIZE", "1")
    monkeypatch.setenv("RANK", "0")
    monkeypatch.setenv("LOCAL_RANK", "0")
    contract_calls = []
    factory_calls = []
    real_contract = trainer.warmup_argument_contract
    real_factory = trainer.create_audited_trainer_class

    def recording_contract(*args):
        contract_calls.append(args)
        return real_contract(*args)

    def recording_factory(*args):
        factory_calls.append((args[0], args[2]))
        return real_factory(*args)

    monkeypatch.setattr(trainer, "warmup_argument_contract", recording_contract)
    monkeypatch.setattr(trainer, "create_audited_trainer_class", recording_factory)
    runs_dir = tmp_path / "runs"
    request_dir = runs_dir / "request"
    request_dir.mkdir(parents=True)
    base = tmp_path / "base"
    base.mkdir()
    (base / "config.json").write_text("{}", encoding="utf-8")
    request = {
        "model_ref": str(base), "checkpoint_path": str(base), "sft_profile": "multidomain",
        "training": {"num_train_epochs": 1, "max_steps": -1, "warmup_ratio": 0.4},
        "supervision": "assistant_all",
    }
    (request_dir / "training_request.json").write_text(json.dumps(request), encoding="utf-8")
    args = SimpleNamespace(
        **vars(training_options_args()), runs_dir=runs_dir, request_dir=request_dir,
        base_model=base, base_url="http://127.0.0.1:8001/v1", defer_serving=True,
    )

    report = trainer.train(args)

    assert contract_calls == [(arguments_type, 0.4)]
    assert factory_calls == [(FakeProductionTrainer, expected_scheduler_ratio)]
    audited = FakeProductionTrainer.instances[0]
    assert audited.scheduler_num_training_steps == 5
    assert audited.scheduler_warmup_steps == 2
    if expected_scheduler_ratio is None:
        assert audited.args.warmup_ratio == 0.4
        assert audited.args.warmup_steps == 0
    else:
        assert audited.args.warmup_steps == 2
    assert report["warmup_steps"] == 2
    assert report["resolved_training"]["warmup_ratio"] == 0.4
    metrics = json.loads((request_dir / "training_metrics.json").read_text(encoding="utf-8"))
    assert metrics["warmup_steps"] == 2
    assert metrics["resolved_training_config"]["warmup_ratio"] == 0.4
