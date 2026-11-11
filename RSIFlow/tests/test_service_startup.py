from types import SimpleNamespace
from unittest.mock import patch

from rsiflow.task_meta import gpu_phases


def test_service_health_wait_allows_cold_shared_imports():
    assert gpu_phases.SERVICE_START_TIMEOUT_SECONDS == 1800


def test_model_load_logs_tokenizer_and_weight_stages():
    from rsiflow.task_meta import serve_gpu

    model = SimpleNamespace(
        parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type="cuda"))]),
        eval=lambda: model,
    )
    with (
        patch.object(serve_gpu.AutoTokenizer, "from_pretrained", return_value=object()),
        patch.object(serve_gpu.AutoModelForCausalLM, "from_pretrained", return_value=model),
        patch.object(serve_gpu, "checkpoint_manifest", return_value=[]),
        patch.object(serve_gpu.torch.cuda, "get_device_name", return_value="A800"),
        patch.object(serve_gpu.log, "info") as log,
    ):
        serve_gpu.load_model("model", "/checkpoint")
    messages = [call.args[0] for call in log.call_args_list]
    assert any("tokenizer" in message.lower() for message in messages)
    assert any("weights" in message.lower() for message in messages)


def test_model_load_registers_checkpoint_directory_alias():
    from rsiflow.task_meta import serve_gpu

    model = SimpleNamespace(
        parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type="cuda"))]),
        eval=lambda: model,
    )
    with (
        patch.dict(serve_gpu.state, {}, clear=True),
        patch.dict(serve_gpu.bindings, {}, clear=True),
        patch.object(serve_gpu.AutoTokenizer, "from_pretrained", return_value=object()),
        patch.object(serve_gpu.AutoModelForCausalLM, "from_pretrained", return_value=model),
        patch.object(serve_gpu, "checkpoint_manifest", return_value=[]),
        patch.object(serve_gpu.torch.cuda, "get_device_name", return_value="A800"),
    ):
        serve_gpu.load_model("/models/Qwen3-4B", "/models/Qwen3-4B")
        assert serve_gpu.state["/models/Qwen3-4B"] is serve_gpu.state["Qwen3-4B"]
        assert serve_gpu.bindings["Qwen3-4B"]["checkpoint_path"] == "/models/Qwen3-4B"


def test_checkpoint_eviction_retains_base_directory_alias(tmp_path):
    from rsiflow.task_meta import serve_gpu

    base = tmp_path / "base"
    new = tmp_path / "new"
    old = tmp_path / "old"
    for checkpoint in (base, new, old):
        checkpoint.mkdir()
    state = {str(base): object(), "base": object(), str(old): object(), "old": object()}
    bindings = {name: {} for name in state}

    def load_new(model_ref, checkpoint):
        for name in (model_ref, "new"):
            serve_gpu.state[name] = object()
            serve_gpu.bindings[name] = {}

    with (
        patch.dict(serve_gpu.state, state, clear=True),
        patch.dict(serve_gpu.bindings, bindings, clear=True),
        patch.object(serve_gpu, "RUNS_ROOT", tmp_path),
        patch.object(serve_gpu, "BASE_MODEL", str(base)),
        patch.object(serve_gpu, "load_model", side_effect=load_new),
        patch.object(serve_gpu.torch.cuda, "empty_cache"),
    ):
        serve_gpu.register_checkpoint(serve_gpu.CheckpointRequest(model_ref=str(new), checkpoint_path=str(new)))
        assert "base" in serve_gpu.state
        assert str(base) in serve_gpu.state
        assert str(new) in serve_gpu.state
        assert str(old) not in serve_gpu.state
        assert "old" not in serve_gpu.state
