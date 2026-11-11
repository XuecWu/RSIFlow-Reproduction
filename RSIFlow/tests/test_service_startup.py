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
