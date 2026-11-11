import argparse
import json
from pathlib import Path

from task_adapter import TaskAdapter
from rsiflow.task_meta.durable import load_task
from rsiflow.task_meta.gpu_phases import registry_path, start_services, stop_services, verify_services
from rsiflow.task_meta.storage import checkpoint_manifest, save_json
from rsiflow.task_meta.task_client import LocalTaskClient

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "reproduction/configs/train_4b.json"


def run_smoke(output_root):
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    adapter = TaskAdapter(ROOT / "RSIFlow", output_root / "receipts")
    config = adapter._config({"config_path": str(CONFIG)})
    config.output_root = str(output_root)
    save_json(output_root / "effective_config.json", config.model_dump())
    try:
        start_services(config, config.task_checkpoint)
        verify_services(config, config.task_checkpoint)
        task = load_task(output_root / "smoke_task.json") if (output_root / "smoke_task.json").exists() else None
        if task is None:
            from rsiflow.task_meta.types import TaskAgentState
            task = TaskAgentState(0, "Qwen3-4B", str(ROOT / "RSIFlow/seed_harness/rsiflow_base_manifest.json"),
                                  checkpoint_path=config.task_checkpoint,
                                  checkpoint_manifest=checkpoint_manifest(config.task_checkpoint))
        client = LocalTaskClient(task, config.task_base_url, timeout=config.task_timeout,
                                 enable_thinking=config.task_enable_thinking)
        response = client([{"role": "user", "content": "Reply with exactly RSIFLOW_SMOKE_OK"}],
                          seed=42, max_tokens=16, temperature=0)
        answer = response["message"].get("content") or ""
        if "RSIFLOW_SMOKE_OK" not in answer:
            raise RuntimeError(f"Unexpected short-generation response: {answer!r}")
    finally:
        if registry_path(config).exists():
            stop_services(config)
    result = {
        "status": "completed",
        "checkpoint": config.task_checkpoint,
        "replicas": config.task_replicas,
        "answer": answer,
        "usage": response.get("usage"),
        "services_stopped_and_training_memory_released": True,
        "scope": "four_real_bf16_services_and_one_short_generation_not_a_benchmark",
    }
    save_json(output_root / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run_smoke(args.output_root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
