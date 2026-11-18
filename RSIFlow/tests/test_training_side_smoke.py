"""Offline tests for the training-side minimal real smoke runner.

Fixtures reproduce the *production* contracts (real PipelineConfig, persisted rollout receipts
with transport_calls, production trainer/GPU metric field names, clone_task Harness copies and
the normalized LocalTaskClient response). No GPU/model/API/SCO is started; synthetic receipts
are test-only and never real smoke evidence.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
for entry in (ROOT, ROOT / "runtime", PROJECT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import training_side_smoke as smoke
from evolution_protocol import file_hash, fingerprint, freeze, make_manifest
from data_protocol import public_text, text_hash
from rsiflow.task_meta.pipeline import PipelineConfig

SOURCES = ("envscaler", "deepcoder_taco", "hotpotqa", "2wiki")
PRESET = {"num_train_epochs": 1, "max_steps": -1, "learning_rate": 2e-6, "lora_rank": 8,
          "lora_alpha": 16, "lora_dropout": 0.05, "batch_size": 1,
          "gradient_accumulation_steps": 8, "max_length": 16384, "seed": 42,
          "lora_target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]}
MOCK_CONTROL = {"write_receipts": True, "transport_status": "completed", "client_contract": "message",
                "harness_clone": True, "sft_mutator": None, "signal_on_ensure": None,
                "sft_training_pid": None, "sft_signal": None, "tool_calls_only": False,
                "sft_dedup": False, "sft_skip_training_receipt": False}
SERVICE_CHECKPOINTS = []


def _row(source, index, interface=None):
    if source == "envscaler":
        return {"id": f"env-task-{index}", "canonical_env_id": f"env-{index}",
                "task": f"envscaler task {index}", "question": f"envscaler task {index}"}
    if source == "deepcoder_taco":
        return {"task_id": f"code/{interface}_{index}", "prompt": f"def {interface}_{index}():\n    ",
                "problem": f"def {interface}_{index}():\n    ", "interface": interface}
    return {"_id": f"{source}-id-{index}", "question": f"{source} question {index}", "answer": f"answer {index}"}


def _pack(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with path.open("wb") as stream:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False) + "\n").encode()
            entries.append((stream.tell(), len(line), row))
            stream.write(line)
    return entries, file_hash(path)


def _entry(source, row, pack, offset, length, pack_hash, interface=None):
    native_id = row.get("id") or row.get("task_id") or row.get("_id")
    if source == "envscaler":
        native_id = f"{row['canonical_env_id']}:{native_id}"
    task_id = f"{source}:{native_id}"
    domain = {"envscaler": "tool_use", "deepcoder_taco": "code"}.get(source, "searchqa")
    return {"source": source, "domain": domain, "task_id": task_id, "native_id": str(native_id),
            "content_hash": text_hash(public_text(row, source)), "group_id": fingerprint([task_id]),
            "strata": {"interface": interface} if interface else {}, "version": "fixture-v1",
            "original_split": "train", "role": "train_evolution", "round_id": 1,
            "purpose": "evolution_train", "exposure": "exposure_unknown", "date": None,
            "record_file": str(pack), "record_offset": offset, "record_length": length,
            "record_sha256": pack_hash, "source_sha256": pack_hash}


@pytest.fixture(autouse=True)
def _reset_control():
    MOCK_CONTROL.update(write_receipts=True, transport_status="completed", client_contract="message",
                        harness_clone=True, sft_mutator=None, signal_on_ensure=None,
                        sft_training_pid=None, sft_signal=None, tool_calls_only=False,
                        sft_dedup=False, sft_skip_training_receipt=False)
    SERVICE_CHECKPOINTS.clear()
    yield


@pytest.fixture(autouse=True)
def _project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "PROJECT", tmp_path)
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    (tmp_path / ".gitignore").write_text("**/logs/\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path):
    project = tmp_path / "project"
    (project / "runtime/configs").mkdir(parents=True)
    packs = project / "frozen/B1"
    tasks = []
    for source in SOURCES:
        interfaces = ["stdio", "function"] if source == "deepcoder_taco" else [None, None]
        rows = [_row(source, i, interface) for i, interface in enumerate(interfaces)]
        entries, pack_hash = _pack(packs / f"{source}.jsonl", rows)
        for (offset, length, row), interface in zip(entries, interfaces):
            tasks.append(_entry(source, row, packs / f"{source}.jsonl", offset, length, pack_hash, interface))
    parent = make_manifest("train_evolution", 1, tasks, split_seed=42)
    parent_path = project / "frozen/B1/manifest.json"
    freeze(parent_path, parent)
    checkpoint = tmp_path / "Qwen3-4B"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"fixture-base-weights")
    harness = project / "seed_harness/rsiflow_base_manifest.json"
    harness.parent.mkdir(parents=True, exist_ok=True)
    freeze(harness, {"harness": "fixture-seed", "version": 1})
    retrieval = tmp_path / "retrieval/search.sqlite"
    retrieval.parent.mkdir(parents=True)
    retrieval.write_bytes(b"fixture-retrieval")
    freeze(retrieval.with_suffix(".sqlite.manifest.json"), {"sha256": file_hash(retrieval), "sources": {}})
    base = json.loads((ROOT / "runtime/configs/base_4b.json").read_text(encoding="utf-8"))
    base["task_checkpoint"] = str(checkpoint)
    (project / "runtime/configs/base.json").write_text(json.dumps(base), encoding="utf-8")
    freeze(project / "runtime/configs/validation.json", {"benchmark_ids": [], "validation_limits": {}})
    launcher = {"rounds": 5, "split_seed": 42, "rollout_seed": 42, "training_seed": 42, "seed": 42,
                "runtime_source": "runtime", "base_config": "configs/base.json",
                "allocated_tasks": 3000, "training_tasks_per_pass": 600,
                "train_quotas_per_round": {"envscaler": 200, "deepcoder_taco": 200, "hotpotqa": 100, "2wiki": 100},
                "validation_limits": {"bfcl_v3": 152}, "skip_acebench": True,
                "evaluation_schedule": "final_only", "data_release": "frozen",
                "frozen_data_dir": str(project / "frozen"),
                "validation_manifest": str(project / "frozen/validation/manifest.json"),
                "validation_vault": str(project / "vault"), "validation_config": "configs/validation.json",
                "retrieval_index": str(retrieval), "output_root": str(tmp_path / "formal_out"),
                "run_name": "formal_run", "ports": [8271, 8272, 8273, 8274],
                "training_timeout_seconds": 3600, "max_length": 16384, "minimum_sft_samples": 1,
                "artifact_evaluation": "direct_submission"}
    launcher_path = project / "launcher.json"
    freeze(launcher_path, launcher)
    return {"project": project, "parent": parent, "parent_path": parent_path, "tasks": tasks,
            "launcher_path": launcher_path, "launcher": launcher, "checkpoint": checkpoint,
            "harness": harness, "retrieval": retrieval, "packs": packs, "tmp": tmp_path}


def _plan(world, tmp_path, name="smoke"):
    return smoke.plan(tmp_path / "logs" / name, world["launcher_path"], world["parent_path"],
                      project_root=world["project"])


def _build_parent_rows(out, *, task_ids=None, manifest_hash=None, success=False, infra=False,
                       missing_score=False, wrong_manifest=False):
    manifest_hash = ("n" * 64) if wrong_manifest else (manifest_hash or "m" * 64)
    kinds = [("envscaler", "tool_use"), ("deepcoder_taco", "code"), ("deepcoder_taco", "code"),
             ("hotpotqa", "searchqa"), ("2wiki", "searchqa")]
    rows = []
    for index, (source, domain) in enumerate(kinds):
        task_id = task_ids[index] if task_ids else f"{source}:task-{index}"
        if MOCK_CONTROL["tool_calls_only"]:
            message = {"role": "assistant", "content": "",
                       "tool_calls": [{"id": "call_x", "type": "function",
                                       "function": {"name": "execute_bash", "arguments": "{\"cmd\":\"ls\"}"}}]}
        else:
            message = {"role": "assistant", "content": "answer"}
        calls = [{"call_id": index, "status": MOCK_CONTROL["transport_status"],
                  "response": {"message": message,
                               "usage": {"prompt_tokens": 4, "completion_tokens": 4}}}]
        row = {"task_id": task_id, "source": source, "domain": domain,
               "source_role": "train_evolution", "purpose": "evolution_train", "round_id": 1,
               "branch_id": "parent", "split": "evolve_train", "manifest_hash": manifest_hash,
               "collection_stage": "parent_pre_update", "question_id": f"q{index}",
               "rollout_id": f"r{index}", "model_call_count": 1, "transport_calls": calls,
               "terminal_reward": 1.0 if success else 0.0,
               "messages": [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}],
               "verification": {"status": "completed", "success": success,
                                "verifier_id": "fixture-trusted-verifier", "full_verifier": True,
                                "task_success": success, "exact_match": 1 if success else 0}}
        if infra:
            row["execution_error_type"] = "service_unavailable"
            row["infrastructure_error"] = True
            row["verification"] = {"status": "infrastructure_error", "success": False}
        if missing_score:
            row.pop("verification")
        rows.append(row)
    return rows


_parent_rows = _build_parent_rows


class FakeAdapter:
    instances = []

    def __init__(self, project_root, receipt_root, model_path=None):
        self.project_root, self.receipt_root = Path(project_root), Path(receipt_root)
        self.calls = []
        FakeAdapter.instances.append(self)

    def _config(self, arguments):
        return None

    def bootstrap(self, arguments):
        self.calls.append("bootstrap")
        state = {"generation": 0, "model_ref": "Qwen3-4B", "harness_path": arguments["seed_harness"],
                 "checkpoint_path": arguments["checkpoint_path"],
                 "checkpoint_manifest": [{"path": "model.safetensors", "bytes": 20, "sha256": "a" * 64}],
                 "artifacts": {"directory": None, "manifest": {}}}
        Path(arguments["state_path"]).write_text(json.dumps(state), encoding="utf-8")
        return {"state_path": arguments["state_path"], "checkpoint_path": arguments["checkpoint_path"]}

    def rollout(self, arguments):
        self.calls.append("rollout")
        out = Path(arguments["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        run_dir = out.parent
        root = run_dir.parents[1]
        selection = json.loads((root / "plan/selection.json").read_text(encoding="utf-8"))
        mini = json.loads((root / "release/B1/manifest.json").read_text(encoding="utf-8"))
        rows = _parent_rows(out, task_ids=selection["task_ids"], manifest_hash=mini["manifest_hash"])
        (out / "train_trajectories.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        performance = {"round_id": 1, "training_manifest": mini["manifest_hash"],
                       "feedback_role": "train_evolution", "collection_stage": "parent_pre_update",
                       "total_rollouts": len(rows), "macro_success": 0.0,
                       "evaluation_protocol": "same_round_full_B_parent_child_v2"}
        (out / "performance.json").write_text(json.dumps(performance), encoding="utf-8")
        (out / "cost.json").write_text(json.dumps({"wall_time_seconds": 1.0}), encoding="utf-8")
        (out / "coverage.json").write_text(json.dumps({"full_coverage": True, "status": "complete_window"}),
                                           encoding="utf-8")
        if MOCK_CONTROL["write_receipts"]:
            cache = run_dir / "rollout_cache" / fingerprint([mini["manifest_hash"]]) / "train_rollouts"
            cache.mkdir(parents=True, exist_ok=True)
            for row in rows:
                identifier = hashlib.sha256(f"{row['task_id']}:0".encode()).hexdigest()
                binding = {"task": row["task_id"], "rollout": 0, "probe": False,
                           "state_hash": "s" * 64,
                           "round_scope": {"manifest_hash": mini["manifest_hash"], "round_id": 1,
                                           "collection_stage": "parent_pre_update"}}
                (cache / f"{identifier}.json").write_text(json.dumps({"binding": binding, "row": row}),
                                                          encoding="utf-8")
        return {"output_dir": str(out), "tasks_observed": len(rows),
                "performance_path": str(out / "performance.json"),
                "trajectories_path": str(out / "train_trajectories.jsonl")}

    def sft(self, arguments):
        self.calls.append("sft")
        out = Path(arguments["output_dir"])
        (out / "model_update").mkdir(parents=True, exist_ok=True)
        child = out / "child_checkpoint"
        child.mkdir(exist_ok=True)
        (child / "model.safetensors").write_bytes(b"fixture-child-weights")
        parent_state = json.loads(Path(arguments["state_path"]).read_text(encoding="utf-8"))
        parent_harness = Path(parent_state["harness_path"])
        if MOCK_CONTROL["harness_clone"]:
            child_harness = out / parent_harness.name
            child_harness.write_bytes(parent_harness.read_bytes())
        else:
            child_harness = out / "different_harness.json"
            child_harness.write_text(json.dumps({"harness": "changed"}), encoding="utf-8")
        state = {"generation": 1, "model_ref": "Qwen3-4B", "harness_path": str(child_harness),
                 "checkpoint_path": str(child),
                 "checkpoint_manifest": [{"path": "model.safetensors", "bytes": 21, "sha256": "b" * 64}],
                 "artifacts": {"directory": str(out / "artifacts"), "manifest": parent_state["artifacts"]["manifest"]}}
        (out / "candidate_state.json").write_text(json.dumps(state), encoding="utf-8")
        parent_rows = [json.loads(l) for l in
                       (Path(arguments["parent_rollout_dir"]) / "train_trajectories.jsonl").read_text().splitlines()
                       if l.strip()]
        n_positive = len(smoke.select_positive_rows(parent_rows, profile="multidomain"))
        n_input = n_positive
        n_final = max(1, n_positive - 1) if MOCK_CONTROL["sft_dedup"] else n_positive
        request_dir = out / "model_update"
        request_dir.mkdir(parents=True, exist_ok=True)
        (request_dir / "sft_positive.jsonl").write_text(
            "".join(json.dumps({"question_id": f"q{i}", "messages": []}) + "\n" for i in range(n_input)),
            encoding="utf-8")
        selection_report = {"input_rows": n_input, "unique_rows": n_final,
                            "duplicates": list(range(n_final, n_input)), "deduplication_applied": True}
        (request_dir / "sft_selection.json").write_text(json.dumps(selection_report), encoding="utf-8")
        total = ((n_final + 3) // 4) * 4
        padded = list(range(n_final)) + [i % n_final for i in range(n_final, total)]
        chunk = total // 4
        partitions = [padded[r * chunk:(r + 1) * chunk] for r in range(4)]
        metrics = {"world_size": 4,
                   "rank_records": [{"rank": r, "local_rank": r, "sampled_indices": partitions[r],
                                     "optimizer_steps": 1, "peak_memory_bytes": 1024} for r in range(4)],
                   "num_train_epochs_completed": 1.0, "training_loss": 0.5,
                   "changed_lora_tensors": 7, "lora_delta_l2": 0.1,
                   "merged_probe_name": "q_proj", "merged_probe_changed_elements": 100,
                   "resolved_training_config": dict(PRESET), "optimizer_steps": 1,
                   "selection": selection_report,
                   "positive_trajectories": n_final, "checkpoint_path": str(child)}
        gpu = {"status": "completed", "restored_checkpoint": str(child), "replica_gpus": [0, 1, 2, 3],
               "gpus": [0, 1, 2, 3]}
        if MOCK_CONTROL["sft_training_pid"] is not None:
            gpu["training_pid"] = MOCK_CONTROL["sft_training_pid"]
            gpu["command"] = [sys.executable, "-m", "torch.distributed.run", "--standalone",
                              "train_task_meta_sft.py", "--request-dir", str(out / "model_update")]
        if MOCK_CONTROL["sft_mutator"]:
            MOCK_CONTROL["sft_mutator"](metrics, gpu, state, out, child, arguments)
        (out / "model_update/training_metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
        (out / "model_update/gpu_phase_receipt.json").write_text(json.dumps(gpu), encoding="utf-8")
        (out / "candidate_state.json").write_text(json.dumps(state), encoding="utf-8")
        if MOCK_CONTROL["sft_signal"]:
            os.kill(os.getpid(), MOCK_CONTROL["sft_signal"])
        return {"candidate_state_path": str(out / "candidate_state.json"),
                "checkpoint_path": str(child), "positive_samples": 1,
                "training_metrics_path": str(out / "model_update/training_metrics.json")}


class _FakeClient:
    def __call__(self, messages, **kwargs):
        if MOCK_CONTROL["client_contract"] == "choices":
            return {"choices": [{"message": {"role": "assistant", "content": "RSIFLOW_SMOKE_OK"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 4}}
        return {"message": {"role": "assistant", "content": "RSIFLOW_SMOKE_OK"},
                "usage": {"prompt_tokens": 4, "completion_tokens": 4}, "binding": {}}


def _patch_services(monkeypatch, *, stop_error=None):
    calls = []

    def _ensure(config, checkpoint):
        calls.append("ensure")
        SERVICE_CHECKPOINTS.append(("ensure", str(checkpoint)))
        registry = Path(config.output_root) / "services/four_gpu_services.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        four_gpu = registry.parent / "four_gpu"
        four_gpu.mkdir(exist_ok=True)
        records = []
        for gpu in range(4):
            cfg = four_gpu / f"gpu_{gpu}.json"
            cfg.write_text(json.dumps({"output_root": str(config.output_root), "inference_gpu": gpu}),
                           encoding="utf-8")
            records.append({"gpu": gpu, "pid": None, "identity": None, "config": str(cfg),
                            "command": [sys.executable, "-B", "start_multidomain_service.py", "--config", str(cfg)],
                            "base_url": f"http://127.0.0.1:{8271 + gpu}/v1"})
        registry.write_text(json.dumps({"phase": "inference_ready", "checkpoint": str(checkpoint),
                                        "services": records}), encoding="utf-8")
        if MOCK_CONTROL["signal_on_ensure"]:
            os.kill(os.getpid(), MOCK_CONTROL["signal_on_ensure"])

    monkeypatch.setattr(smoke, "ensure_services", _ensure)

    def _verify(config, checkpoint):
        calls.append("verify")
        SERVICE_CHECKPOINTS.append(("verify", str(checkpoint)))
        return [{"gpu": g, "base_url": f"http://127.0.0.1:{8271 + g}/v1"} for g in range(4)]

    monkeypatch.setattr(smoke, "verify_services", _verify)
    monkeypatch.setattr(smoke, "start_services", lambda *a, **k: calls.append("start"))

    def _stop(*a, **k):
        calls.append("stop")
        if stop_error:
            raise stop_error

    monkeypatch.setattr(smoke, "stop_services", _stop)
    monkeypatch.setattr(smoke, "registry_path", lambda config=None: Path(config.output_root) / "services/four_gpu_services.json")
    monkeypatch.setattr(smoke, "LocalTaskClient", lambda *a, **k: calls.append("client") or _FakeClient())
    return calls


def _patch_adapter(monkeypatch, world, rows_factory):
    FakeAdapter.instances = []
    monkeypatch.setattr(smoke, "TaskAdapter", FakeAdapter)

    def patched(out, **kwargs):
        return rows_factory(out, **kwargs)

    monkeypatch.setattr(sys.modules[__name__], "_parent_rows", patched)


def _success_factory(out, **kw):
    return _build_parent_rows(out, success=True, **kw)


def _run_rollout(world, tmp_path, monkeypatch, factory=_success_factory):
    receipt = _plan(world, tmp_path)
    calls = _patch_services(monkeypatch)
    _patch_adapter(monkeypatch, world, factory)
    result = smoke.execute_rollout(Path(receipt["output_root"]))
    return receipt, calls, result


# --- 1. real PipelineConfig contract ------------------------------------------

def test_effective_config_is_real_pipeline_config_with_model_dump(world, tmp_path):
    receipt = _plan(world, tmp_path)
    config = smoke.load_effective_config(Path(receipt["output_root"]))
    assert isinstance(config, PipelineConfig)
    dumped = config.model_dump()
    assert dumped["output_root"] == receipt["output_root"]
    assert len(dumped["task_replicas"]) == 4 and dumped["trainer_python"]
    assert config.window_quotas == {"tool_use": 1, "code": 2, "searchqa": 2}


# --- selection / mini release / config (unchanged contracts) ------------------

def test_selection_is_deterministic_and_covers_five_distinct_kinds(world):
    first = smoke.select_training_subset(world["parent"])
    second = smoke.select_training_subset(json.loads(json.dumps(world["parent"])))
    assert first["task_ids"] == second["task_ids"]
    assert len(set(first["task_ids"])) == 5
    assert first["coverage"] == {k: 1 for k in smoke.COVERAGE_KEYS}
    interfaces = sorted(t["strata"]["interface"] for t in first["tasks"] if t["source"] == "deepcoder_taco")
    assert interfaces == ["function", "stdio"]


def test_selection_records_full_record_provenance(world):
    for record in smoke.select_training_subset(world["parent"])["records"]:
        with open(record["record_file"], "rb") as stream:
            stream.seek(record["record_offset"])
            payload = stream.read(record["record_length"])
        assert text_hash(public_text(json.loads(payload), record["source"])) == record["content_hash"]


@pytest.mark.parametrize("drop", ["envscaler", "hotpotqa", "2wiki"])
def test_missing_source_fails_closed(world, drop):
    tasks = [t for t in world["parent"]["tasks"] if t["source"] != drop]
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_training_subset(make_manifest("train_evolution", 1, tasks))


def test_missing_interface_fails_closed(world):
    for interface in ("stdio", "function"):
        tasks = [t for t in world["parent"]["tasks"]
                 if not (t["source"] == "deepcoder_taco" and t["strata"].get("interface") == interface)]
        with pytest.raises(smoke.SmokeBlocked):
            smoke.select_training_subset(make_manifest("train_evolution", 1, tasks))


def test_duplicate_or_wrong_role_fails_closed(world):
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_training_subset(make_manifest("train_evolution", 1,
                                                   world["parent"]["tasks"] + [world["parent"]["tasks"][0]]))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_training_subset(make_manifest("independent_validation", None, world["parent"]["tasks"]))


def test_empty_or_tampered_record_fails_closed(world):
    tasks = [dict(t) for t in world["parent"]["tasks"]]
    tasks[0]["record_length"] = 0
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_training_subset(make_manifest("train_evolution", 1, tasks))
    tasks = [dict(t) for t in world["parent"]["tasks"]]
    tasks[0]["record_sha256"] = "0" * 64
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_training_subset(make_manifest("train_evolution", 1, tasks))


def test_mini_release_preserves_bytes_and_only_five(world, tmp_path):
    receipt = _plan(world, tmp_path)
    mini = json.loads(Path(receipt["release"]["manifest_path"]).read_text())
    assert len(mini["tasks"]) == 5
    original = {t["task_id"]: t for t in world["parent"]["tasks"]}
    for task in mini["tasks"]:
        o = original[task["task_id"]]
        with open(o["record_file"], "rb") as s:
            s.seek(o["record_offset"]); a = s.read(o["record_length"])
        with open(task["record_file"], "rb") as s:
            s.seek(task["record_offset"]); b = s.read(task["record_length"])
        assert a == b
    assert receipt["release"]["source_assets_unchanged"] is True


def test_mini_release_refuses_existing_or_symlink(world, tmp_path):
    (tmp_path / "logs" / "smoke").mkdir(parents=True)
    with pytest.raises(smoke.SmokeBlocked):
        _plan(world, tmp_path)
    link = tmp_path / "logs" / "linked"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.plan(link, world["launcher_path"], world["parent_path"], project_root=world["project"])


def test_effective_config_smoke_sized_and_preset(world, tmp_path):
    receipt = _plan(world, tmp_path)
    eff = json.loads(Path(receipt["effective_config_path"]).read_text())
    assert eff["window_quotas"] == {"tool_use": 1, "code": 2, "searchqa": 2}
    assert eff["max_generations"] == 1 and eff["rollouts_per_task"] == 1
    training = eff["training"]
    assert training["num_train_epochs"] == 1.0 and training["max_steps"] == -1
    assert training["learning_rate"] == 2e-6 and training["lora_rank"] == 8
    assert training["lora_target_modules"] == PRESET["lora_target_modules"]
    launcher = json.loads(Path(receipt["smoke_config_path"]).read_text())
    assert launcher["train_quotas_per_round"] == smoke.SOURCE_QUOTAS
    assert launcher["allocated_tasks"] == 5 and launcher["rounds"] == 1


# --- 2. production rollout persisted receipts ---------------------------------

def test_rollout_requires_explicit_bwrap_before_bootstrap(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    monkeypatch.delenv("RSIFLOW_SANDBOX_BACKEND")

    class ForbiddenAdapter:
        def __init__(self, *args, **kwargs):
            pytest.fail("TaskAdapter must not be constructed without the explicit bwrap backend")

    monkeypatch.setattr(smoke, "TaskAdapter", ForbiddenAdapter)
    monkeypatch.setattr(smoke, "ensure_services", lambda *a, **k: pytest.fail("service side effect"))
    result = smoke.execute_rollout(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert "requires explicit RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1" in result["error"]


def test_rollout_accepts_production_rows_without_trajectory_path(world, tmp_path, monkeypatch):
    receipt, calls, result = _run_rollout(world, tmp_path, monkeypatch)
    assert result["status"] == "completed"
    rows = [json.loads(l) for l in Path(result["trajectories_path"]).read_text().splitlines()]
    assert all("trajectory_path" not in row for row in rows)
    assert all(row["transport_calls"] for row in rows)
    assert len(result["receipt_paths"]) == 5
    assert result["coverage"] == {k: 1 for k in smoke.COVERAGE_KEYS}
    assert calls[0] == "ensure" and calls[-1] == "stop"


def test_missing_persisted_receipt_blocks(world, tmp_path, monkeypatch):
    MOCK_CONTROL["write_receipts"] = False
    _, _, result = _run_rollout(world, tmp_path, monkeypatch)
    assert result["status"] == "incomplete"
    assert "persisted per-task rollout receipt" in result["error"]


def test_incomplete_transport_call_blocks(world, tmp_path, monkeypatch):
    MOCK_CONTROL["transport_status"] = "failed"
    _, _, result = _run_rollout(world, tmp_path, monkeypatch)
    assert result["status"] == "incomplete"


def test_wrong_answers_are_completed_with_model_failures(world, tmp_path, monkeypatch):
    _, _, result = _run_rollout(world, tmp_path, monkeypatch,
                                lambda out, **kw: _build_parent_rows(out, success=False, **kw))
    assert result["status"] == "completed_with_model_failures"
    assert result["model_failures"] == 5


def test_candidate_error_with_completed_calls_is_a_model_failure(world, tmp_path, monkeypatch):
    def candidate_failure(out, **kw):
        rows = _build_parent_rows(out, success=False, **kw)
        for row in rows:
            row["execution_error_type"] = "candidate_error"
            row["error"] = "FileNotFoundError: candidate artifact missing"
        return rows

    _, _, result = _run_rollout(world, tmp_path, monkeypatch, candidate_failure)
    assert result["status"] == "completed_with_model_failures"
    assert result["model_failures"] == 5


def test_candidate_error_without_model_calls_still_blocks(world, tmp_path, monkeypatch):
    def no_model_calls(out, **kw):
        rows = _build_parent_rows(out, success=False, **kw)
        for row in rows:
            row["execution_error_type"] = "candidate_error"
            row["error"] = "BlockingIOError: resource temporarily unavailable"
            row["model_call_count"] = 0
            row["transport_calls"] = []
        return rows

    _, _, result = _run_rollout(world, tmp_path, monkeypatch, no_model_calls)
    assert result["status"] == "incomplete"
    assert "No real local model call recorded" in result["error"]


def test_infrastructure_and_missing_score_block(world, tmp_path, monkeypatch):
    receipt, calls, infra = _run_rollout(world, tmp_path, monkeypatch,
                                         lambda out, **kw: _build_parent_rows(out, infra=True, **kw))
    assert infra["status"] == "incomplete"
    root = Path(receipt["output_root"])
    (root / "receipts/rollout.json").unlink()
    (root / "locks/rollout.started.json").unlink(missing_ok=True)
    _patch_adapter(monkeypatch, world, lambda out, **kw: _build_parent_rows(out, missing_score=True, **kw))
    missing = smoke.execute_rollout(root)
    assert missing["status"] == "incomplete"


# --- 3/4/5. SFT production contract, client response, pre-service blocking ----

def test_sft_accepts_production_metrics_and_cloned_harness(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    FakeAdapter.instances.clear()
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "completed"
    assert result["world_size"] == 4 and result["ranks"] == [0, 1, 2, 3]
    assert result["restored_checkpoint"] == "child"
    assert result["short_generation"]["answer"] == "RSIFLOW_SMOKE_OK"
    assert FakeAdapter.instances[-1].calls == ["sft"]


@pytest.mark.parametrize("field", ["world_size", "rank_records", "epochs", "training_loss",
                                   "changed_lora_tensors", "merged_probe", "replica_gpus",
                                   "restored_checkpoint", "preset"])
def test_sft_production_metric_tampering_fails(world, tmp_path, monkeypatch, field):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)

    def mutate(metrics, gpu, state, out, child, arguments):
        if field == "world_size":
            metrics["world_size"] = 1
        elif field == "rank_records":
            metrics["rank_records"] = metrics["rank_records"][:3]
        elif field == "epochs":
            metrics["num_train_epochs_completed"] = 2.0
        elif field == "training_loss":
            metrics["training_loss"] = float("nan")
        elif field == "changed_lora_tensors":
            metrics["changed_lora_tensors"] = 0
        elif field == "merged_probe":
            metrics["merged_probe_changed_elements"] = 0
        elif field == "replica_gpus":
            gpu["replica_gpus"] = [0, 1, 2]
        elif field == "restored_checkpoint":
            gpu["restored_checkpoint"] = "base"
        elif field == "preset":
            metrics["resolved_training_config"]["max_steps"] = 1

    MOCK_CONTROL["sft_mutator"] = mutate
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete", field


def test_harness_content_change_fails_but_clone_path_allowed(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    MOCK_CONTROL["harness_clone"] = False
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert "Harness" in result["error"]


def test_short_generation_requires_message_contract(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    MOCK_CONTROL["client_contract"] = "choices"
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert "message" in result["error"] or "KeyError" in result["error"]


def test_illegal_child_blocks_before_any_service_side_effect(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    calls.clear()

    def mutate(metrics, gpu, state, out, child, arguments):
        state["checkpoint_path"] = str(world["checkpoint"])

    MOCK_CONTROL["sft_mutator"] = mutate
    SERVICE_CHECKPOINTS.clear()
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    # Base restart is a required S2 precondition; the illegal CHILD must never be served.
    assert sum(1 for kind, _ in SERVICE_CHECKPOINTS if kind == "ensure") == 1
    assert "client" not in calls and "start" not in calls


def test_child_outside_root_blocks_before_services(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    calls.clear()

    def mutate(metrics, gpu, state, out, child, arguments):
        state["checkpoint_path"] = str(tmp_path / "elsewhere")

    MOCK_CONTROL["sft_mutator"] = mutate
    SERVICE_CHECKPOINTS.clear()
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert ("ensure", str(tmp_path / "elsewhere")) not in SERVICE_CHECKPOINTS
    assert sum(1 for kind, _ in SERVICE_CHECKPOINTS if kind == "ensure") == 1


# --- 6. evidence sealing -------------------------------------------------------

def test_tampered_parent_evidence_blocks_sft(world, tmp_path, monkeypatch):
    receipt, _, rollout = _run_rollout(world, tmp_path, monkeypatch)
    path = Path(rollout["trajectories_path"])
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    rows[0]["terminal_reward"] = 99.0
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert "evidence changed" in result["error"]


def test_rollout_receipt_seals_production_and_artifacts(world, tmp_path, monkeypatch):
    receipt, _, rollout = _run_rollout(world, tmp_path, monkeypatch)
    sealed = rollout["evidence_hashes"]
    assert set(sealed["production_implementation"]) == set(smoke.PRODUCTION_IMPLEMENTATION)
    assert len(sealed["rollout_receipts"]) == 5
    assert sealed["train_trajectories"] == file_hash(Path(rollout["trajectories_path"]))


# --- 7. cached failure semantics ----------------------------------------------

def test_cached_failure_is_not_already_completed(world, tmp_path, monkeypatch):
    receipt, _, rollout = _run_rollout(world, tmp_path, monkeypatch,
                                       lambda out, **kw: _build_parent_rows(out, infra=True, **kw))
    assert rollout["status"] == "incomplete"
    again = smoke.execute_rollout(Path(receipt["output_root"]))
    assert again["status"] == "incomplete" and again.get("cached_failure") is True
    assert again["status"] != "already_completed"
    assert smoke.main(["execute-rollout", "--output-root", receipt["output_root"]]) == 1


def test_zero_positive_cache_stays_blocked(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch,
                                 lambda out, **kw: _build_parent_rows(out, success=False, **kw))
    first = smoke.execute_sft(Path(receipt["output_root"]))
    assert first["status"] == "blocked_no_positive_parent_trajectory"
    again = smoke.execute_sft(Path(receipt["output_root"]))
    assert again["status"] == "blocked_no_positive_parent_trajectory"
    assert smoke.main(["execute-sft", "--output-root", receipt["output_root"]]) == 1


def test_successful_cache_reverifies_binding(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    assert smoke.execute_rollout(Path(receipt["output_root"]))["status"] == "already_completed"
    selection = json.loads(Path(receipt["selection_path"]).read_text())
    selection["task_ids"] = selection["task_ids"][::-1]
    Path(receipt["selection_path"]).write_text(json.dumps(selection), encoding="utf-8")
    again = smoke.execute_rollout(Path(receipt["output_root"]))
    assert again["status"] == "requires_audit"


# --- 8. common stage lock ------------------------------------------------------

def test_common_lock_blocks_concurrent_stages(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    (root / "locks").mkdir(exist_ok=True)
    (root / "locks/stage.lock").write_text("held", encoding="utf-8")
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute_sft(root)
    (root / "locks/stage.lock").unlink()


def test_state_rechecked_inside_lock(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    (root / "locks/sft.started.json").write_text("{}", encoding="utf-8")
    assert smoke.execute_sft(root)["status"] == "requires_audit"


# --- 9. signal cleanup ---------------------------------------------------------

@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_triggers_owned_cleanup(world, tmp_path, monkeypatch, signum):
    MOCK_CONTROL["signal_on_ensure"] = signum
    receipt, calls, result = _run_rollout(world, tmp_path, monkeypatch)
    assert result["status"] == "incomplete"
    assert "interrupted by signal" in result["error"]
    assert calls[-1] == "stop"
    assert result["cleanup"]["services_stopped"] is True
    assert signal.getsignal(signum) == signal.default_int_handler if signum == signal.SIGINT else True


# --- 10. registry process ownership -------------------------------------------

def test_foreign_registry_replaced_inside_root_is_refused(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    config = smoke.load_effective_config(root)
    registry = root / "services/four_gpu_services.json"
    foreign_cfg = root / "services/four_gpu/gpu_0.json"
    foreign_cfg.write_text(json.dumps({"output_root": str(tmp_path / "other_root")}), encoding="utf-8")
    registry.write_text(json.dumps({"phase": "inference_ready", "services": [
        {"gpu": 0, "pid": None, "identity": None, "config": str(foreign_cfg),
         "command": [sys.executable, "--config", str(foreign_cfg)]}]}), encoding="utf-8")
    stopped = []
    monkeypatch.setattr(smoke, "stop_services", lambda *a, **k: stopped.append("stop"))
    result = smoke.cleanup_owned_services(config, root)
    assert result["registry_owned"] is False and result["error"] and not stopped


def test_owned_registry_with_matching_processes_is_accepted(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    config = smoke.load_effective_config(root)
    result = smoke.cleanup_owned_services(config, root)
    assert result["registry_owned"] is True
    assert result["ownership_check"] == "registry processes verified as owned by this smoke root"


def test_no_registry_means_no_foreign_stop(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    calls = _patch_services(monkeypatch)

    class Boom(FakeAdapter):
        def bootstrap(self, arguments):
            raise RuntimeError("bootstrap failed before services")

    monkeypatch.setattr(smoke, "TaskAdapter", Boom)
    result = smoke.execute_rollout(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert result["cleanup"]["owned_registry_created"] is False
    assert "stop" not in calls


def test_cleanup_failure_fails_overall(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    _patch_services(monkeypatch, stop_error=RuntimeError("GPU memory not released"))
    _patch_adapter(monkeypatch, world, _success_factory)
    result = smoke.execute_rollout(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert "not released" in result["cleanup"]["error"]


# --- recovery / gates / plan purity -------------------------------------------

def test_recovery_never_retrains_and_keeps_hashes(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    sft = smoke.execute_sft(Path(receipt["output_root"]))
    assert sft["status"] == "completed"
    FakeAdapter.instances.clear()
    before = file_hash(Path(sft["training_metrics_path"]))
    result = smoke.execute_recovery(Path(receipt["output_root"]))
    assert result["status"] == "completed"
    assert not any("sft" in c for a in FakeAdapter.instances for c in a.calls)
    assert file_hash(Path(sft["training_metrics_path"])) == before
    assert result["training_receipts_unchanged"] is True
    assert calls[-1] == "stop"


def test_recovery_detects_tampered_training_receipt(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    sft = smoke.execute_sft(Path(receipt["output_root"]))
    Path(sft["training_metrics_path"]).write_text(json.dumps({"tampered": True}), encoding="utf-8")
    result = smoke.execute_recovery(Path(receipt["output_root"]))
    assert result["status"] == "incomplete"
    assert result["training_receipts_unchanged"] is False


def test_stage_gates(world, tmp_path):
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute_rollout(tmp_path / "logs" / "none")
    receipt = _plan(world, tmp_path)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute_sft(Path(receipt["output_root"]))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute_recovery(Path(receipt["output_root"]))


def test_plan_has_no_model_or_service_side_effects(world, tmp_path, monkeypatch):
    for name in ("ensure_services", "start_services", "stop_services", "verify_services"):
        monkeypatch.setattr(smoke, name, lambda *a, **k: pytest.fail(f"{name} during plan"))
    monkeypatch.setattr(smoke, "LocalTaskClient", lambda *a, **k: pytest.fail("client during plan"))
    receipt = _plan(world, tmp_path)
    assert receipt["status"] == "planned" and receipt["model_calls"] == 0
    assert receipt["formal_round_completed"] is False
    assert receipt["eligible_for_candidate_acceptance"] is False


def test_status_and_output_root_validation(world, tmp_path):
    receipt = _plan(world, tmp_path)
    assert smoke.status(Path(receipt["output_root"]))["stages"]["plan"] == "planned"
    assert receipt["output_root_validation"]["git_ignored"] is True
    with pytest.raises(smoke.SmokeBlocked):
        smoke.plan(tmp_path / "tracked" / "run", world["launcher_path"], world["parent_path"],
                   project_root=world["project"])


# --- mutation verification -----------------------------------------------------

MUT_ZERO_POSITIVE = ('                if not positives:\n', '                if False:\n')
MUT_MANIFEST_BINDING = ('        if row.get("manifest_hash") != mini_manifest_hash:\n', '        if False:\n')
MUT_CLEANUP_OWNERSHIP = ('    if not owned:\n        result["error"] = f"Refusing to stop services: {reason}"\n',
                         '    if False:\n        result["error"] = f"Refusing to stop services: {reason}"\n')
MUT_WORLD_SIZE = ('    if metrics.get("world_size") != 4:\n', '    if False:\n')
MUT_SFT_GATE = ('    if rollout_state != "final" or rollout["status"] not in SUCCESS_STATUSES:\n',
                '    if False:\n')
MUT_PRE_SERVICE = ('                    if problems:\n', '                    if False:\n')


def _mutant(tmp_path, name, mutation):
    old, new = mutation
    source = (PROJECT / "reproduction/scripts/training_side_smoke.py").read_text(encoding="utf-8")
    assert source.count(old) == 1, f"mutation anchor not unique for {name}"
    path = tmp_path / f"mutant_{name}.py"
    path.write_text(source.replace(old, new), encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"mutant_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROJECT = PROJECT
    module.ROOT = PROJECT
    module.__file__ = str(PROJECT / "reproduction/scripts/training_side_smoke.py")
    return module


def _wire(monkeypatch, module, factory):
    module.ensure_services = lambda c, k: None
    module.verify_services = lambda c, k: [{"gpu": g} for g in range(4)]
    module.stop_services = lambda *a, **k: None
    module.registry_path = lambda config=None: Path(config.output_root) / "services/four_gpu_services.json"
    module.LocalTaskClient = lambda *a, **k: _FakeClient()
    module.TaskAdapter = FakeAdapter
    monkeypatch.setattr(sys.modules[__name__], "_parent_rows", lambda out, **kw: factory(out, **kw))


def test_mutation_zero_positive_gate(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch,
                                 lambda out, **kw: _build_parent_rows(out, success=False, **kw))
    root = Path(receipt["output_root"])
    assert smoke.execute_sft(root)["status"] == "blocked_no_positive_parent_trajectory"
    mutant = _mutant(tmp_path, "zero_positive", MUT_ZERO_POSITIVE)
    _wire(monkeypatch, mutant, lambda out, **kw: _build_parent_rows(out, success=False, **kw))
    (root / "receipts/sft.json").unlink()
    FakeAdapter.instances.clear()
    result = mutant.execute_sft(root)
    assert result["status"] != "blocked_no_positive_parent_trajectory"
    assert any("sft" in c for a in FakeAdapter.instances for c in a.calls)


def test_mutation_manifest_binding(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    root = Path(receipt["output_root"])
    factory = lambda out, **kw: _build_parent_rows(out, success=True, wrong_manifest=True, **kw)
    _patch_services(monkeypatch)
    _patch_adapter(monkeypatch, world, factory)
    assert smoke.execute_rollout(root)["status"] == "incomplete"
    mutant = _mutant(tmp_path, "manifest", MUT_MANIFEST_BINDING)
    _wire(monkeypatch, mutant, factory)
    (root / "receipts/rollout.json").unlink()
    assert mutant.execute_rollout(root)["status"] == "completed"


def test_mutation_cleanup_ownership(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    foreign = tmp_path / "foreign/services/four_gpu_services.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text(json.dumps({"phase": "inference_ready", "services": []}), encoding="utf-8")
    stopped = []
    monkeypatch.setattr(smoke, "registry_path", lambda config=None: foreign)
    monkeypatch.setattr(smoke, "stop_services", lambda *a, **k: stopped.append("stop"))
    config = smoke.load_effective_config(root)
    assert smoke.cleanup_owned_services(config, root)["registry_owned"] is False
    mutant = _mutant(tmp_path, "ownership", MUT_CLEANUP_OWNERSHIP)
    mutant.registry_path = lambda config=None: foreign
    mutant.stop_services = lambda *a, **k: stopped.append("stop")
    mutant.cleanup_owned_services(config, root)
    assert stopped == ["stop"]


def test_mutation_world_size(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def mutate(metrics, gpu, state, out, child, arguments):
        metrics["world_size"] = 1

    MOCK_CONTROL["sft_mutator"] = mutate
    assert smoke.execute_sft(root)["status"] == "incomplete"
    (root / "receipts/sft.json").unlink()
    mutant = _mutant(tmp_path, "world_size", MUT_WORLD_SIZE)
    _wire(monkeypatch, mutant, _success_factory)
    MOCK_CONTROL["sft_mutator"] = mutate
    assert mutant.execute_sft(root)["status"] == "completed"


def test_mutation_pre_service_blocking(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def mutate(metrics, gpu, state, out, child, arguments):
        state["checkpoint_path"] = str(world["checkpoint"])

    MOCK_CONTROL["sft_mutator"] = mutate
    calls.clear()
    SERVICE_CHECKPOINTS.clear()
    assert smoke.execute_sft(root)["status"] == "incomplete"
    assert sum(1 for kind, _ in SERVICE_CHECKPOINTS if kind == "ensure") == 1
    (root / "receipts/sft.json").unlink()
    mutant = _mutant(tmp_path, "pre_service", MUT_PRE_SERVICE)
    _wire(monkeypatch, mutant, _success_factory)
    MOCK_CONTROL["sft_mutator"] = mutate
    mutant_calls = []
    mutant.ensure_services = lambda c, k: mutant_calls.append("ensure")
    mutant.execute_sft(root)
    assert mutant_calls.count("ensure") == 2


# --- legacy-contract RED: reverting each fix must break the new contract test --
# These prove the new tests reproduce the production contract rather than the old mocks.

LEGACY_CONFIG = ("    return PipelineConfig.model_validate(value)",
                 "    from types import SimpleNamespace\n    return SimpleNamespace(**value)")
LEGACY_TRAJECTORY = ("        _validate_transport_calls(row)",
                     "        if not row.get('trajectory_path'):\n"
                     "            raise SmokeBlocked(f\"Missing persistent trajectory: {row.get('task_id')}\")\n"
                     "        _validate_transport_calls(row)")
LEGACY_METRICS = ('    rank_records = metrics.get("rank_records") or []',
                  '    rank_records = [{"rank": r} for r in (metrics.get("ranks") or [])]')
LEGACY_CLIENT = ('    message = response["message"]',
                 '    message = response["choices"][0]["message"]')
LEGACY_CACHE = ('    if previous["status"] in SUCCESS_STATUSES:\n'
                '        return {"status": "already_completed", "stage": stage, "previous_status": previous["status"]}\n'
                '    return {"status": previous["status"], "stage": stage, "cached_failure": True,\n'
                '            "error": previous.get("error", "previous stage did not succeed")}',
                '    return {"status": "already_completed", "stage": stage, "previous_status": previous["status"]}')


def test_legacy_config_contract_is_rejected(world, tmp_path):
    receipt = _plan(world, tmp_path)
    mutant = _mutant(tmp_path, "legacy_config", LEGACY_CONFIG)
    config = mutant.load_effective_config(Path(receipt["output_root"]))
    assert not isinstance(config, PipelineConfig)
    assert not hasattr(config, "model_dump")


def test_legacy_trajectory_requirement_rejects_production_rows(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    root = Path(receipt["output_root"])
    _patch_services(monkeypatch)
    _patch_adapter(monkeypatch, world, _success_factory)
    assert smoke.execute_rollout(root)["status"] == "completed"
    (root / "receipts/rollout.json").unlink()
    mutant = _mutant(tmp_path, "legacy_trajectory", LEGACY_TRAJECTORY)
    _wire(monkeypatch, mutant, _success_factory)
    result = mutant.execute_rollout(root)
    assert result["status"] == "incomplete"
    assert "trajectory" in result["error"].lower()


def test_legacy_metric_fields_reject_production_sft(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    assert smoke.execute_sft(root)["status"] == "completed"
    (root / "receipts/sft.json").unlink()
    mutant = _mutant(tmp_path, "legacy_metrics", LEGACY_METRICS)
    _wire(monkeypatch, mutant, _success_factory)
    assert mutant.execute_sft(root)["status"] == "incomplete"


def test_legacy_client_contract_rejects_production_response(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    assert smoke.execute_sft(root)["status"] == "completed"
    (root / "receipts/sft.json").unlink()
    mutant = _mutant(tmp_path, "legacy_client", LEGACY_CLIENT)
    _wire(monkeypatch, mutant, _success_factory)
    result = mutant.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "choices" in result["error"] or "KeyError" in result["error"]


def test_legacy_cache_reports_failure_as_success(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch,
                                 lambda out, **kw: _build_parent_rows(out, infra=True, **kw))
    root = Path(receipt["output_root"])
    assert smoke.execute_rollout(root)["status"] == "incomplete"
    mutant = _mutant(tmp_path, "legacy_cache", LEGACY_CACHE)
    _wire(monkeypatch, mutant, _success_factory)
    cached = mutant.execute_rollout(root)
    assert cached["status"] == "already_completed"
    assert cached["previous_status"] == "incomplete"


# --- fix round 2: five Important items ----------------------------------------

def _spawn_trainer_like(request_dir):
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "train_task_meta_sft.py",
         "--request-dir", str(request_dir)],
        start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_sft_requires_resolved_config_and_full_preset(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def drop_config(metrics, gpu, state, out, child, arguments):
        metrics.pop("resolved_training_config", None)

    MOCK_CONTROL["sft_mutator"] = drop_config
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "resolved_training_config evidence is missing" in result["error"]
    (root / "receipts/sft.json").unlink()

    def drop_key(metrics, gpu, state, out, child, arguments):
        metrics["resolved_training_config"].pop("max_steps")

    MOCK_CONTROL["sft_mutator"] = drop_key
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "missing preset evidence: max_steps" in result["error"]


def test_sft_padding_duplicates_are_legal(world, tmp_path, monkeypatch):
    # 5 positives -> DistributedSampler pads to 8; repeats are legal, union must cover 0..4.
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "completed"
    merged = [i for r in result["ranks"] for i in []]  # ranks list only; check via metrics below
    metrics = json.loads(Path(result["training_metrics_path"]).read_text())
    sampled = [i for r in metrics["rank_records"] for i in r["sampled_indices"]]
    assert len(sampled) > len(set(sampled))
    assert set(sampled) == set(range(metrics["positive_trajectories"]))


def test_sft_dedup_reduces_final_dataset_legally(world, tmp_path, monkeypatch):
    MOCK_CONTROL["sft_dedup"] = True
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    result = smoke.execute_sft(Path(receipt["output_root"]))
    assert result["status"] == "completed"
    metrics = json.loads(Path(result["training_metrics_path"]).read_text())
    selection = json.loads((Path(result["training_metrics_path"]).parent / "sft_selection.json").read_text())
    assert selection["input_rows"] > selection["unique_rows"]
    assert metrics["positive_trajectories"] == selection["unique_rows"]


def test_sft_single_sample_with_padding_is_legal(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def one_sample(metrics, gpu, state, out, child, arguments):
        metrics["positive_trajectories"] = 1
        metrics["selection"] = {"input_rows": 1, "unique_rows": 1, "duplicates": [],
                                "deduplication_applied": True}
        for r in metrics["rank_records"]:
            r["sampled_indices"] = [0]
        (out / "model_update/sft_positive.jsonl").write_text(json.dumps({"q": 0}) + "\n", encoding="utf-8")
        (out / "model_update/sft_selection.json").write_text(json.dumps(metrics["selection"]), encoding="utf-8")

    MOCK_CONTROL["sft_mutator"] = one_sample
    result = smoke.execute_sft(root)
    assert result["status"] == "completed"


@pytest.mark.parametrize("mode", ["missing", "out_of_range", "empty"])
def test_sft_real_coverage_violations_fail(world, tmp_path, monkeypatch, mode):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def mutate(metrics, gpu, state, out, child, arguments):
        records = metrics["rank_records"]
        if mode == "missing":
            records[2]["sampled_indices"] = []
        elif mode == "out_of_range":
            records[0]["sampled_indices"] = [999]
        elif mode == "empty":
            for r in records:
                r["sampled_indices"] = []

    MOCK_CONTROL["sft_mutator"] = mutate
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete", mode
    assert "sample" in result["error"].lower()


def test_tampered_a0_state_blocks_sft(world, tmp_path, monkeypatch):
    receipt, _, rollout = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    state_path = Path(rollout["state_path"])
    state = json.loads(state_path.read_text())
    state["checkpoint_path"] = str(tmp_path / "tampered-checkpoint")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "evidence changed" in result["error"]


def test_tampered_base_config_blocks_sft(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    base_config = smoke._base_config_path(json.loads((root / "receipts/plan.json").read_text()))
    value = json.loads(base_config.read_text())
    value["tampered"] = True
    base_config.write_text(json.dumps(value), encoding="utf-8")
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "evidence changed" in result["error"]


def test_tampered_harness_blocks_sft(world, tmp_path, monkeypatch):
    receipt, _, rollout = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    harness = Path(json.loads(Path(rollout["state_path"]).read_text())["harness_path"])
    harness.write_text(json.dumps({"harness": "tampered"}), encoding="utf-8")
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "evidence changed" in result["error"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_sft_signal_terminates_owned_training_pgid(world, tmp_path, monkeypatch, signum):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    request_dir = root / "runs/training_side_smoke/sft/model_update"
    proc = _spawn_trainer_like(request_dir)
    try:
        MOCK_CONTROL["sft_training_pid"] = proc.pid
        MOCK_CONTROL["sft_signal"] = signum
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert "interrupted by signal" in result["error"]
        assert result["training_cleanup"]["training_terminated"] is True
        assert result["training_cleanup"]["training_owned"] is True
        proc.wait(timeout=10)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_sft_signal_refuses_foreign_training_pgid(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    foreign_dir = tmp_path / "foreign_root/runs/sft/model_update"
    proc = _spawn_trainer_like(foreign_dir)
    try:
        MOCK_CONTROL["sft_training_pid"] = proc.pid
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert result["training_cleanup"]["training_owned"] is False
        assert result["training_cleanup"]["training_terminated"] is False
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_foreign_registry_rejected_before_ensure(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    root = Path(receipt["output_root"])
    calls = _patch_services(monkeypatch)
    _patch_adapter(monkeypatch, world, _success_factory)
    four_gpu = root / "services/four_gpu"
    four_gpu.mkdir(parents=True)
    foreign_cfg = four_gpu / "gpu_0.json"
    foreign_cfg.write_text(json.dumps({"output_root": str(tmp_path / "other_root")}), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", str(foreign_cfg)],
                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        registry = root / "services/four_gpu_services.json"
        registry.write_text(json.dumps({"phase": "inference_ready", "services": [
            {"gpu": 0, "pid": proc.pid, "identity": smoke.process_identity(proc.pid),
             "config": str(foreign_cfg), "command": [sys.executable, "start_multidomain_service.py",
                                                     "--config", str(foreign_cfg)]}]}), encoding="utf-8")
        result = smoke.execute_rollout(root)
        assert result["status"] == "incomplete"
        assert "ensure" not in calls
        assert "ownership" in result["error"].lower() or "Registry" in result["error"]
    finally:
        proc.kill()
        proc.wait()


def test_live_cmdline_mismatch_rejected_before_ensure(world, tmp_path, monkeypatch):
    receipt = _plan(world, tmp_path)
    root = Path(receipt["output_root"])
    calls = _patch_services(monkeypatch)
    _patch_adapter(monkeypatch, world, _success_factory)
    four_gpu = root / "services/four_gpu"
    four_gpu.mkdir(parents=True)
    cfg = four_gpu / "gpu_0.json"
    cfg.write_text(json.dumps({"output_root": str(root)}), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                            start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        registry = root / "services/four_gpu_services.json"
        registry.write_text(json.dumps({"phase": "inference_ready", "services": [
            {"gpu": 0, "pid": proc.pid, "identity": smoke.process_identity(proc.pid),
             "config": str(cfg), "command": [sys.executable, "start_multidomain_service.py",
                                             "--config", str(cfg)]}]}), encoding="utf-8")
        result = smoke.execute_rollout(root)
        assert result["status"] == "incomplete"
        assert "ensure" not in calls
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait()


def test_pure_tool_calls_message_is_accepted(world, tmp_path, monkeypatch):
    from rsiflow.task_meta.task_client import qwen_message
    open_tag = chr(60) + "tool_call" + chr(62)
    close_tag = chr(60) + "/tool_call" + chr(62)
    payload = json.dumps({"name": "execute_bash", "arguments": {"cmd": "ls"}})
    decoded = qwen_message(open_tag + payload + close_tag)
    assert decoded.get("content", "") == ""
    assert decoded.get("tool_calls")
    assert smoke._message_has_evidence(decoded) is True
    MOCK_CONTROL["tool_calls_only"] = True
    _, _, result = _run_rollout(world, tmp_path, monkeypatch)
    assert result["status"] == "completed"


def test_both_empty_message_is_rejected(world, tmp_path, monkeypatch):
    assert smoke._message_has_evidence({"role": "assistant", "content": ""}) is False
    assert smoke._message_has_evidence({"role": "assistant", "content": "", "tool_calls": []}) is False
    assert smoke._message_has_evidence({"role": "assistant", "content": "  ",
                                        "tool_calls": [{"function": {"name": ""}}]}) is False

    def empty_message(out, **kw):
        rows = _build_parent_rows(out, **kw)
        for row in rows:
            for call in row["transport_calls"]:
                call["response"]["message"] = {"role": "assistant", "content": ""}
        return rows

    _, _, result = _run_rollout(world, tmp_path, monkeypatch, empty_message)
    assert result["status"] == "incomplete"
    assert "real model message" in result["error"]


# --- fix round 3: whole-PGID cleanup, receipt gap, S2 base restart ------------

def _spawn_leader_with_rank(request_dir):
    code = ("import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)',"
            "'train_task_meta_sft.py','--request-dir'," + repr(str(request_dir)) + "]); "
            "time.sleep(0.3)")
    return subprocess.Popen([sys.executable, "-c", code], start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_sft_signal_kills_whole_group_after_leader_exits(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    request_dir = root / "runs/training_side_smoke/sft/model_update"
    leader = _spawn_leader_with_rank(request_dir)
    leader.wait(timeout=10)
    assert leader.poll() is not None
    members = smoke._group_members(leader.pid)
    assert members, "rank child must remain in the group after the leader exits"
    try:
        MOCK_CONTROL["sft_training_pid"] = leader.pid
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert result["training_cleanup"]["training_terminated"] is True
        assert result["training_cleanup"]["training_owned"] is True
        assert not smoke._group_alive(leader.pid)
        assert all(smoke.process_identity(m) is None for m in members)
    finally:
        if smoke._group_alive(leader.pid):
            os.killpg(leader.pid, signal.SIGKILL)


def test_sft_signal_discovers_group_without_training_receipt(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    request_dir = root / "runs/training_side_smoke/sft/model_update"
    proc = _spawn_trainer_like(request_dir)
    try:
        MOCK_CONTROL["sft_training_pid"] = None
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert proc.pid in result["training_cleanup"]["discovered_groups"]
        assert result["training_cleanup"]["training_terminated"] is True
        proc.wait(timeout=10)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_sft_checks_registry_before_adapter_sft(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    four_gpu = root / "services/four_gpu"
    four_gpu.mkdir(parents=True, exist_ok=True)
    foreign_cfg = four_gpu / "gpu_0.json"
    foreign_cfg.write_text(json.dumps({"output_root": str(tmp_path / "other_root")}), encoding="utf-8")
    registry = root / "services/four_gpu_services.json"
    registry.write_text(json.dumps({"phase": "inference_ready", "services": [
        {"gpu": 0, "pid": None, "identity": None, "config": str(foreign_cfg),
         "command": [sys.executable, "start_multidomain_service.py", "--config", str(foreign_cfg)]}]}),
        encoding="utf-8")
    calls.clear()
    FakeAdapter.instances.clear()
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert not any("sft" in c for a in FakeAdapter.instances for c in a.calls)
    assert "ensure" not in calls


def test_s2_ensures_and_verifies_base_before_training(world, tmp_path, monkeypatch):
    receipt, calls, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    SERVICE_CHECKPOINTS.clear()
    FakeAdapter.instances.clear()
    result = smoke.execute_sft(root)
    assert result["status"] == "completed"
    base = str(smoke.load_effective_config(root).task_checkpoint)
    kinds = [kind for kind, _ in SERVICE_CHECKPOINTS]
    assert kinds[:2] == ["ensure", "verify"]
    assert SERVICE_CHECKPOINTS[0][1] == base and SERVICE_CHECKPOINTS[1][1] == base
    assert FakeAdapter.instances[-1].calls == ["sft"]
    assert SERVICE_CHECKPOINTS[-2][0] == "ensure" and SERVICE_CHECKPOINTS[-1][0] == "verify"
    assert SERVICE_CHECKPOINTS[-1][1] != base


# --- fix round 4: mandatory sampling evidence + leader-exited discovery -------

def test_sft_missing_selection_file_fails(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def drop_selection(metrics, gpu, state, out, child, arguments):
        (out / "model_update/sft_selection.json").unlink()

    MOCK_CONTROL["sft_mutator"] = drop_selection
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "sft_selection.json evidence is missing" in result["error"]


def test_sft_missing_positive_file_fails(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def drop_positive(metrics, gpu, state, out, child, arguments):
        (out / "model_update/sft_positive.jsonl").unlink()

    MOCK_CONTROL["sft_mutator"] = drop_positive
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "sft_positive.jsonl evidence is missing" in result["error"]


def test_sft_empty_selection_fails(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def empty_selection(metrics, gpu, state, out, child, arguments):
        (out / "model_update/sft_selection.json").write_text("{}", encoding="utf-8")

    MOCK_CONTROL["sft_mutator"] = empty_selection
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "sft_selection.json evidence is missing or empty" in result["error"]


def test_sft_shrunk_self_report_fails(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def shrink(metrics, gpu, state, out, child, arguments):
        metrics["positive_trajectories"] = 1
        for r in metrics["rank_records"]:
            r["sampled_indices"] = [0] if r["rank"] == 0 else []

    MOCK_CONTROL["sft_mutator"] = shrink
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "unique_rows disagrees with positive_trajectories" in result["error"]


def test_sft_inconsistent_input_rows_fails(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])

    def bad_input(metrics, gpu, state, out, child, arguments):
        path = out / "model_update/sft_selection.json"
        selection = json.loads(path.read_text())
        selection["input_rows"] = selection["input_rows"] + 3
        path.write_text(json.dumps(selection), encoding="utf-8")
        metrics["selection"] = selection

    MOCK_CONTROL["sft_mutator"] = bad_input
    result = smoke.execute_sft(root)
    assert result["status"] == "incomplete"
    assert "input_rows disagrees with sft_positive.jsonl" in result["error"]


def test_discovery_finds_group_when_leader_exited_no_receipt(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    request_dir = root / "runs/training_side_smoke/sft/model_update"
    leader = _spawn_leader_with_rank(request_dir)
    leader.wait(timeout=10)
    assert leader.poll() is not None
    members = smoke._group_members(leader.pid)
    assert members
    try:
        MOCK_CONTROL["sft_training_pid"] = None
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert leader.pid in result["training_cleanup"]["discovered_groups"]
        assert result["training_cleanup"]["training_terminated"] is True
        assert not smoke._group_alive(leader.pid)
        assert all(smoke.process_identity(m) is None for m in members)
    finally:
        if smoke._group_alive(leader.pid):
            os.killpg(leader.pid, signal.SIGKILL)


def test_discovery_refuses_foreign_rank_group(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    foreign_dir = tmp_path / "foreign_root/runs/sft/model_update"
    leader = _spawn_leader_with_rank(foreign_dir)
    leader.wait(timeout=10)
    members = smoke._group_members(leader.pid)
    try:
        MOCK_CONTROL["sft_training_pid"] = None
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        assert leader.pid not in result["training_cleanup"]["discovered_groups"]
        assert smoke._group_alive(leader.pid)
        assert any(smoke.process_identity(m) is not None for m in members)
    finally:
        if smoke._group_alive(leader.pid):
            os.killpg(leader.pid, signal.SIGKILL)
            for m in members:
                try:
                    os.kill(m, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def test_discovery_combined_receipt_and_leader_exited(world, tmp_path, monkeypatch):
    receipt, _, _ = _run_rollout(world, tmp_path, monkeypatch)
    root = Path(receipt["output_root"])
    request_dir = root / "runs/training_side_smoke/sft/model_update"
    leader = _spawn_leader_with_rank(request_dir)
    leader.wait(timeout=10)
    members = smoke._group_members(leader.pid)
    try:
        MOCK_CONTROL["sft_training_pid"] = leader.pid
        MOCK_CONTROL["sft_signal"] = signal.SIGTERM
        result = smoke.execute_sft(root)
        assert result["status"] == "incomplete"
        cleanup = result["training_cleanup"]
        assert cleanup["training_terminated"] is True
        assert any(g["pgid"] == leader.pid for g in cleanup["groups"])
        assert not smoke._group_alive(leader.pid)
    finally:
        if smoke._group_alive(leader.pid):
            os.killpg(leader.pid, signal.SIGKILL)


# --- Task 4: read-only natural-positive MODEL import smoke --------------------

@pytest.fixture
def model_smoke():
    return importlib.import_module("deepcoder_model_smoke")


def _source_tree_hashes(root):
    return {str(path): file_hash(path) for path in sorted(Path(root).rglob("*"))
            if path.is_file()}


@pytest.fixture
def model_source(world, tmp_path):
    from rsiflow.task_meta.durable import load_task, task_hash

    release = tmp_path / "source-release"
    rows = [_row("deepcoder_taco", index, "function") for index in range(16)]
    entries, pack_hash = _pack(release / "B1/deepcoder_taco.jsonl", rows)
    tasks = [_entry("deepcoder_taco", row, release / "B1/deepcoder_taco.jsonl",
                    offset, length, pack_hash, "function")
             for (offset, length, row) in entries]
    parent_manifest = make_manifest("train_evolution", 1, tasks, split_seed=42)
    manifest_path = release / "B1/manifest.json"
    freeze(manifest_path, parent_manifest)
    freeze(release / "protocol.json", {"rounds": 5, "schedule": "round_repeated"})

    source_run = tmp_path / "logs/source-run"
    a0_dir = source_run / "snapshots/A0"
    task_dir = a0_dir / "task"
    task_dir.mkdir(parents=True)
    harness = tmp_path / "external-harness/harness.json"
    harness.parent.mkdir()
    harness.write_bytes(world["harness"].read_bytes())
    artifacts = tmp_path / "external-artifacts"
    artifacts.mkdir()
    (artifacts / "workflow.json").write_text('{"step":"external-fixture"}')
    from rsiflow.task_meta.storage import artifact_manifest
    artifacts_manifest = artifact_manifest(artifacts)
    (world["checkpoint"] / "config.json").write_text('{"model_type":"fixture"}')
    (world["checkpoint"] / "tokenizer_config.json").write_text('{"tokenizer":"fixture"}')
    checkpoint_manifest = smoke.checkpoint_manifest(world["checkpoint"])
    state = {"generation": 0, "model_ref": "Qwen3-4B", "harness_path": str(harness),
             "checkpoint_path": str(world["checkpoint"]),
             "checkpoint_manifest": checkpoint_manifest,
             "artifacts": {"directory": str(artifacts), "manifest": artifacts_manifest}}
    state_path = task_dir / "task_state_snapshot.json"
    freeze(state_path, state)
    a0_hash = task_hash(load_task(state))
    snapshot_receipt = {"status": "snapshotted", "destination": str(a0_dir),
                        "restorable_task_state_path": str(state_path),
                        "files_sha256": {"task/task_state_snapshot.json": file_hash(state_path)}}
    freeze(a0_dir / "snapshot_receipt.json", snapshot_receipt)
    freeze(source_run / "meta_session/state.json", {
        "transport": "app_server", "status": "complete", "thread_id": "source-thread",
    })
    freeze(source_run / "round_1/attempts/attempt_1/candidate/candidate_state.json", {
        "checkpoint_path": str(tmp_path / "accepted-candidate-must-not-be-used"),
    })
    (source_run / "meta").mkdir(parents=True)
    (source_run / "meta/skills.jsonl").write_text('{"skill":"must-not-be-used"}\n')

    parent = source_run / "round_1/parent"
    parent.mkdir(parents=True)
    harness_hash = file_hash(harness)
    policy_hash = fingerprint(checkpoint_manifest)
    trajectory_rows = []
    for index, task in enumerate(tasks):
        task_id = task["task_id"]
        scope = {"role": "train_evolution", "purpose": "evolution_train", "round_id": 1,
                 "manifest_hash": parent_manifest["manifest_hash"],
                 "collection_stage": "parent_pre_update", "task_hash": a0_hash,
                 "harness_sha256": harness_hash, "checkpoint_manifest": checkpoint_manifest,
                 "artifacts": artifacts_manifest, "protocol_hash": "official-fixture-protocol"}
        message = {"role": "assistant", "content": "def solution():\n    return 1"}
        binding = {"checkpoint_path": str(world["checkpoint"].resolve()),
                   "weights": checkpoint_manifest}
        calls = [{"call_id": 0, "status": "completed",
                  "response": {"message": message,
                               "usage": {"prompt_tokens": 4, "completion_tokens": 4},
                               "binding": binding, "model_ref_requested": "Qwen3-4B",
                               "model_ref_response": "Qwen3-4B"}}]
        row = {"task_id": task_id, "source": "deepcoder_taco", "domain": "code",
               "source_role": "train_evolution", "purpose": "evolution_train", "round_id": 1,
               "branch_id": "parent", "split": "evolve_train",
               "manifest_hash": parent_manifest["manifest_hash"],
               "collection_stage": "parent_pre_update", "question_id": task_id,
               "rollout_id": 0, "model_call_count": 1, "transport_calls": calls,
               "state_hash": a0_hash, "model_ref": "Qwen3-4B",
               "harness_sha256": harness_hash, "harness_hash": harness_hash,
               "artifact_input_manifest": artifacts_manifest, "parent_policy_hash": policy_hash,
               "task_source_hash": task["content_hash"], "terminal_reward": 1.0,
               "trajectory_id": fingerprint(["source-trajectory", task_id]),
               "success_verifier_version": "official-deepcoder-fixture",
               "messages": [{"role": "user", "content": "question"}, message],
               "verification": {"status": "completed", "success": True,
                                "verifier_id": "fixture-trusted-verifier", "full_verifier": True,
                                "task_success": True, "exact_match": 1}}
        trajectory_rows.append(row)
        receipt = {"binding": {"task": task_id, "task_source_hash": task["content_hash"],
                               "task_record": task["content_hash"], "rollout": 0, "probe": False,
                               "state_hash": fingerprint(scope), "round_scope": scope},
                   "row": row}
        receipt_path = source_run / "rollout_cache" / fingerprint(scope) / "train_rollouts" / (
            hashlib.sha256(f"{task_id}:0".encode()).hexdigest() + ".json")
        freeze(receipt_path, receipt)
    (parent / "train_trajectories.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in trajectory_rows), encoding="utf-8")
    performance = {"round_id": 1, "training_manifest": parent_manifest["manifest_hash"],
                   "feedback_role": "train_evolution", "collection_stage": "parent_pre_update",
                   "total_rollouts": 16, "macro_success": 1.0,
                   "evaluation_protocol": "same_round_full_B_parent_child_v2",
                   "benchmarks": {"deepcoder_taco": {"count": 16, "successes": 16, "errors": {}}}}
    freeze(parent / "performance.json", performance)

    launcher = dict(world["launcher"])
    launcher.update(experiment_scope="deepcoder_five_round_canary",
                    training_schedule="round_repeated", rounds=5,
                    allocated_tasks=80, unique_tasks=16, training_tasks_per_pass=16,
                    train_quotas_per_round={"envscaler": 0, "deepcoder_taco": 16,
                                            "hotpotqa": 0, "2wiki": 0},
                    retry_rejected_from_round=6, evaluation_schedule="none",
                    frozen_data_dir=str(release), output_root=str(source_run.parent),
                    run_name=source_run.name)
    machine_config = world["project"] / "model-source-machine.json"
    freeze(machine_config, launcher)
    return {"source_run": source_run, "release": release, "manifest": parent_manifest,
            "manifest_path": manifest_path, "machine_config": machine_config,
            "state_path": state_path, "snapshot_receipt": a0_dir / "snapshot_receipt.json",
            "parent": parent, "rows": trajectory_rows, "checkpoint": world["checkpoint"],
            "harness": harness, "artifacts": artifacts}


def _model_plan(model_smoke, model_source, tmp_path, name="model-smoke"):
    return model_smoke.plan_model_smoke(
        model_source["source_run"], tmp_path / "logs" / name,
        model_source["machine_config"])


def _patch_model_runtime(model_smoke, monkeypatch):
    calls = _patch_services(monkeypatch)
    FakeAdapter.instances = []
    monkeypatch.setattr(model_smoke, "TaskAdapter", FakeAdapter)
    monkeypatch.setattr(model_smoke, "ensure_services", smoke.ensure_services)
    monkeypatch.setattr(model_smoke, "verify_services", smoke.verify_services)
    monkeypatch.setattr(model_smoke, "stop_services", smoke.stop_services)
    monkeypatch.setattr(model_smoke, "registry_path", smoke.registry_path)
    monkeypatch.setattr(model_smoke, "LocalTaskClient", smoke.LocalTaskClient)
    return calls


def test_model_smoke_plan_imports_only_a0_parent_natural_positives(
        model_smoke, model_source, tmp_path):
    before = _source_tree_hashes(model_source["source_run"])

    receipt = _model_plan(model_smoke, model_source, tmp_path)

    assert receipt["source"]["state_path"].endswith("snapshots/A0/task/task_state_snapshot.json")
    assert receipt["source"]["parent_dir"].endswith("round_1/parent")
    assert receipt["source"]["accepted_candidate_used"] is False
    assert receipt["source"]["skills_used"] is False
    assert receipt["positive_parent_count"] > 0
    assert receipt["status"] == "planned"
    assert receipt["source"]["task_count"] == 16
    assert receipt["source"]["transport_calls_verified"] == 16
    assert receipt["source"]["source_hashes_after"] == before
    root = Path(receipt["output_root"])
    config = model_smoke.load_effective_config(root)
    assert config.output_root == str(root)
    assert config.window_quotas == {"tool_use": 0, "code": 16, "searchqa": 0}
    registry = Path(model_smoke.registry_path(config)).resolve()
    assert registry.is_relative_to(root.resolve())
    assert not registry.is_relative_to(model_source["source_run"].resolve())
    assert _source_tree_hashes(model_source["source_run"]) == before


@pytest.mark.parametrize("mode", [
    "trajectory", "a0_snapshot", "performance", "source_state_hash",
    "parent_candidate", "transport", "manifest",
])
def test_model_smoke_plan_rejects_tampered_source_evidence(
        model_smoke, model_source, tmp_path, mode):
    if mode == "trajectory":
        rows = list(model_source["rows"])
        rows[0]["verification"]["full_verifier"] = False
        (model_source["parent"] / "train_trajectories.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
    elif mode == "a0_snapshot":
        state = json.loads(model_source["state_path"].read_text())
        state["model_ref"] = "tampered"
        model_source["state_path"].write_text(json.dumps(state))
    elif mode == "performance":
        path = model_source["parent"] / "performance.json"
        value = json.loads(path.read_text())
        value["total_rollouts"] = 15
        path.write_text(json.dumps(value))
    elif mode == "source_state_hash":
        path = model_source["snapshot_receipt"]
        value = json.loads(path.read_text())
        value["files_sha256"]["task/task_state_snapshot.json"] = "0" * 64
        path.write_text(json.dumps(value))
    elif mode == "parent_candidate":
        rows = list(model_source["rows"])
        for row in rows:
            row["branch_id"] = "child"
        (model_source["parent"] / "train_trajectories.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
    elif mode == "transport":
        rows = list(model_source["rows"])
        rows[0]["transport_calls"] = []
        rows[0]["model_call_count"] = 0
        (model_source["parent"] / "train_trajectories.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))
    elif mode == "manifest":
        rows = list(model_source["rows"])
        rows[0]["manifest_hash"] = "0" * 64
        (model_source["parent"] / "train_trajectories.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows))

    with pytest.raises((ValueError, RuntimeError)):
        _model_plan(model_smoke, model_source, tmp_path, mode)


def test_model_smoke_execute_uses_production_sft_and_recovers_without_retraining(
        model_smoke, model_source, tmp_path, monkeypatch):
    plan = _model_plan(model_smoke, model_source, tmp_path)
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    before = _source_tree_hashes(model_source["source_run"])

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "completed", result
    assert FakeAdapter.instances[-1].calls == ["sft"]
    assert result["recovery"]["retrained"] is False
    assert result["recovery"]["training_receipts_unchanged"] is True
    assert len(result["short_generations"]) == 2
    assert calls.count("stop") == 2
    assert _source_tree_hashes(model_source["source_run"]) == before
    assert result["source_hashes_unchanged"] is True
    assert not list((root / "locks").glob("*.started.json"))
    assert (root / "receipts/sft.json").is_file()
    assert (root / "receipts/recovery.json").is_file()
    assert model_smoke.main(["execute", "--output-root", str(root)]) == 0

    (root / "receipts/recovery.json").unlink()
    FakeAdapter.instances.clear()
    recovered = model_smoke.execute_model_smoke(root)
    assert recovered["status"] == "completed"
    assert FakeAdapter.instances == []
    assert _source_tree_hashes(model_source["source_run"]) == before


def test_model_smoke_execute_missing_plan_requires_audit(model_smoke, tmp_path):
    result = model_smoke.execute_model_smoke(tmp_path / "logs/missing-plan")
    assert result["status"] == "requires_audit"
    assert "plan" in result["error"].lower()


@pytest.mark.parametrize("stage", ["sft", "recovery"])
def test_model_smoke_execute_started_stage_requires_audit_without_training(
        model_smoke, model_source, tmp_path, monkeypatch, stage):
    plan = _model_plan(model_smoke, model_source, tmp_path, f"started-{stage}")
    root = Path(plan["output_root"])
    _patch_model_runtime(model_smoke, monkeypatch)
    (root / "locks").mkdir(exist_ok=True)
    (root / "locks" / f"{stage}.started.json").write_text("{}")

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert FakeAdapter.instances == []


def test_model_smoke_execute_binding_or_source_change_requires_audit_before_training(
        model_smoke, model_source, tmp_path, monkeypatch):
    plan = _model_plan(model_smoke, model_source, tmp_path)
    other = _model_plan(model_smoke, model_source, tmp_path, "source-changed")
    root = Path(plan["output_root"])
    _patch_model_runtime(model_smoke, monkeypatch)
    effective = root / "plan/effective_config.json"
    value = json.loads(effective.read_text())
    value["max_generations"] = 99
    effective.write_text(json.dumps(value))

    changed_binding = model_smoke.execute_model_smoke(root)
    assert changed_binding["status"] == "requires_audit"
    assert FakeAdapter.instances == []

    with (model_source["parent"] / "train_trajectories.jsonl").open("a") as stream:
        stream.write("\n")
    changed_source = model_smoke.execute_model_smoke(Path(other["output_root"]))
    assert changed_source["status"] == "requires_audit"
    assert FakeAdapter.instances == []


def test_model_smoke_exited_training_pgid_is_cleaned_for_sft_and_recovery(
        model_smoke, model_source, tmp_path, monkeypatch):
    plan = _model_plan(model_smoke, model_source, tmp_path, "exited-training")
    root = Path(plan["output_root"])
    _patch_model_runtime(model_smoke, monkeypatch)
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    process.wait(timeout=10)
    assert smoke.process_identity(process.pid) is None
    assert not smoke._group_alive(process.pid)
    MOCK_CONTROL["sft_training_pid"] = process.pid

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "completed", result
    assert result["sft"]["training_cleanup"]["error"] is None
    assert result["recovery"]["training_cleanup"]["error"] is None
    assert result["sft"]["status"] == result["recovery"]["status"] == "completed"
    gpu = json.loads(Path(result["sft"]["gpu_phase_receipt_path"]).read_text())
    assert gpu["training_pid"] == process.pid
    assert "train_task_meta_sft.py" in gpu["command"]
    assert "--request-dir" in gpu["command"]


def test_model_smoke_source_and_seal_tampering_requires_audit_before_side_effects(
        model_smoke, model_source, tmp_path, monkeypatch):
    plan = _model_plan(model_smoke, model_source, tmp_path, "source-seal-attack")
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    trajectory_path = model_source["parent"] / "train_trajectories.jsonl"
    rows = [json.loads(line) for line in trajectory_path.read_text().splitlines() if line]
    rows[0]["messages"][1]["content"] = "unreviewed supervision"
    trajectory_path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    plan_path = root / "receipts/plan.json"
    sealed_plan = json.loads(plan_path.read_text())
    identity = sealed_plan["plan_identity"]
    changed_hash = file_hash(trajectory_path)
    for key in ("source_hashes_before", "source_hashes_after"):
        sealed_plan["source"][key][str(trajectory_path)] = changed_hash
    plan_path.write_text(json.dumps(sealed_plan))

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert sealed_plan["plan_identity"] == identity
    assert FakeAdapter.instances == []
    assert calls == []


@pytest.mark.parametrize("mode", ["base_config", "helper"])
def test_model_smoke_execute_rejects_base_config_or_helper_change_before_side_effects(
        model_smoke, model_source, tmp_path, monkeypatch, mode):
    if mode == "helper":
        changed_path = tmp_path / "helper/training_side_smoke.py"
        changed_path.parent.mkdir()
        changed_path.write_bytes(Path(smoke.__file__).read_bytes())
        monkeypatch.setattr(model_smoke.side, "__file__", str(changed_path))
    else:
        changed_path = model_source["machine_config"].parent / "runtime/configs/base.json"
    plan = _model_plan(model_smoke, model_source, tmp_path, f"binding-{mode}")
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    if mode == "helper":
        changed_path.write_text("# changed helper implementation\n")
    else:
        value = json.loads(changed_path.read_text())
        value["unreviewed_base_change"] = True
        changed_path.write_text(json.dumps(value))

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert FakeAdapter.instances == []
    assert calls == []


@pytest.mark.parametrize("mode", ["harness", "artifact", "weights", "non_weights"])
def test_model_smoke_external_a0_inputs_are_sealed_and_rechecked_before_services(
        model_smoke, model_source, tmp_path, monkeypatch, mode):
    plan = _model_plan(model_smoke, model_source, tmp_path, f"external-{mode}")
    root = Path(plan["output_root"])
    seals = plan["source"]["input_seals"]
    paths = {
        "harness": model_source["harness"],
        "artifact": model_source["artifacts"] / "workflow.json",
        "weights": model_source["checkpoint"] / "model.safetensors",
        "non_weights": model_source["checkpoint"] / "config.json",
    }
    changed_path = paths[mode]
    group = "harness" if mode == "harness" else "artifacts" if mode == "artifact" else "checkpoint"
    assert seals[group][str(changed_path.resolve())] == file_hash(changed_path)
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    changed_path.write_bytes(changed_path.read_bytes() + b"unreviewed-change")

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert FakeAdapter.instances == []
    assert calls == []


def test_model_smoke_external_input_change_during_sft_requires_final_audit(
        model_smoke, model_source, tmp_path, monkeypatch):
    plan = _model_plan(model_smoke, model_source, tmp_path, "external-during-sft")
    root = Path(plan["output_root"])
    _patch_model_runtime(model_smoke, monkeypatch)

    def change_non_weights(metrics, gpu, state, out, child, arguments):
        config = model_source["checkpoint"] / "config.json"
        config.write_bytes(config.read_bytes() + b"changed-during-sft")

    MOCK_CONTROL["sft_mutator"] = change_non_weights
    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    final = json.loads((root / "receipts/sft.json").read_text())
    assert final["status"] == "requires_audit"
    assert final["input_seals_unchanged"] is False


@pytest.mark.parametrize("backend", [None, "legacy_host"])
def test_model_smoke_recovery_only_requires_explicit_bwrap_before_side_effects(
        model_smoke, model_source, tmp_path, monkeypatch, backend):
    plan = _model_plan(model_smoke, model_source, tmp_path, f"recovery-backend-{backend}")
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    completed = model_smoke.execute_model_smoke(root)
    assert completed["status"] == "completed"
    (root / "receipts/recovery.json").unlink()
    calls.clear()
    SERVICE_CHECKPOINTS.clear()
    FakeAdapter.instances.clear()
    if backend is None:
        monkeypatch.delenv("RSIFLOW_SANDBOX_BACKEND")
    else:
        monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", backend)

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert calls == []
    assert SERVICE_CHECKPOINTS == []
    assert FakeAdapter.instances == []
    assert not (root / "receipts/recovery.json").exists()
    assert not (root / "locks/recovery.started.json").exists()


@pytest.mark.parametrize("asset", ["release", "checkpoint", "artifacts"])
def test_model_smoke_plan_rejects_output_root_inside_read_only_assets(
        model_smoke, model_source, tmp_path, asset):
    output_root = model_source[asset] / "logs/model-smoke-output"
    source_before = _source_tree_hashes(model_source["source_run"])

    with pytest.raises((ValueError, RuntimeError), match="read-only|release|output|overlap"):
        model_smoke.plan_model_smoke(
            model_source["source_run"], output_root, model_source["machine_config"])
    assert not output_root.exists()
    assert _source_tree_hashes(model_source["source_run"]) == source_before


def _link_source_meta_to_central(model_source, tmp_path):
    source_run = model_source["source_run"]
    central = tmp_path / "Meta_logs" / source_run.name
    central.mkdir(parents=True)
    central_meta = central / "meta"
    (source_run / "meta").rename(central_meta)
    freeze(central / ".run-owner.json", {"schema_version": 1, "run_dir": str(source_run.resolve())})
    (source_run / "meta").symlink_to(central_meta, target_is_directory=True)
    return central, central_meta, source_run / "meta"


def test_model_smoke_plan_accepts_production_central_meta_link(
        model_smoke, model_source, tmp_path):
    central, central_meta, link = _link_source_meta_to_central(model_source, tmp_path)
    target_text = os.readlink(link)
    target_identity = central_meta.stat()
    skills_hash = file_hash(central_meta / "skills.jsonl")

    receipt = _model_plan(model_smoke, model_source, tmp_path, "central-meta-link")

    assert receipt["status"] == "planned"
    assert not (model_source["source_run"] / "meta_session").is_symlink()
    assert link.is_symlink() and os.readlink(link) == target_text
    assert central_meta.stat().st_dev == target_identity.st_dev
    assert central_meta.stat().st_ino == target_identity.st_ino
    assert file_hash(central_meta / "skills.jsonl") == skills_hash
    assert json.loads((central / ".run-owner.json").read_text())["run_dir"] == str(
        model_source["source_run"].resolve())


def test_model_smoke_plan_seals_central_meta_link_without_target_content_scan(
        model_smoke, model_source, tmp_path):
    central, central_meta, link = _link_source_meta_to_central(model_source, tmp_path)

    receipt = _model_plan(model_smoke, model_source, tmp_path, "central-meta-seal")

    seal = receipt["source"]["meta_link_seal"]
    identity = central_meta.stat()
    assert seal["target_text"] == os.readlink(link)
    assert seal["resolved_path"] == str(central_meta.resolve())
    assert seal["st_dev"] == identity.st_dev and seal["st_ino"] == identity.st_ino
    assert seal["owner_path"] == str(central / ".run-owner.json")
    assert seal["owner_sha256"] == file_hash(central / ".run-owner.json")
    assert str(central_meta / "skills.jsonl") not in receipt["source"]["source_hashes_after"]


@pytest.mark.parametrize("mode", ["unknown_top", "nested", "root"])
def test_model_smoke_plan_only_allows_exact_top_level_central_meta_link(
        model_smoke, model_source, tmp_path, mode):
    _link_source_meta_to_central(model_source, tmp_path)
    source_run = model_source["source_run"]
    source_argument = source_run
    if mode == "unknown_top":
        (source_run / "unknown").symlink_to(tmp_path, target_is_directory=True)
    elif mode == "nested":
        (model_source["parent"] / "nested-link").symlink_to(model_source["harness"])
    else:
        source_argument = tmp_path / "logs/source-run-alias"
        source_argument.symlink_to(source_run, target_is_directory=True)

    with pytest.raises((ValueError, RuntimeError), match="symlink|link"):
        model_smoke.plan_model_smoke(
            source_argument, tmp_path / "logs" / f"reject-{mode}", model_source["machine_config"])


def _alternate_central_meta(model_source, tmp_path):
    central = tmp_path / "AlternateMeta_logs" / model_source["source_run"].name
    target = central / "meta"
    target.mkdir(parents=True)
    freeze(central / ".run-owner.json", {
        "schema_version": 1, "run_dir": str(model_source["source_run"].resolve()),
    })
    return target


def test_model_smoke_execute_rejects_central_meta_link_retarget_before_services(
        model_smoke, model_source, tmp_path, monkeypatch):
    _link_source_meta_to_central(model_source, tmp_path)
    plan = _model_plan(model_smoke, model_source, tmp_path, "central-meta-retarget")
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    alternate = _alternate_central_meta(model_source, tmp_path)
    link = model_source["source_run"] / "meta"
    link.unlink()
    link.symlink_to(alternate, target_is_directory=True)

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert calls == [] and FakeAdapter.instances == []


def test_model_smoke_execute_rejects_central_meta_target_identity_change(
        model_smoke, model_source, tmp_path, monkeypatch):
    central, central_meta, link = _link_source_meta_to_central(model_source, tmp_path)
    plan = _model_plan(model_smoke, model_source, tmp_path, "central-meta-inode")
    root = Path(plan["output_root"])
    calls = _patch_model_runtime(model_smoke, monkeypatch)
    original_identity = central_meta.stat()
    central_meta.rename(central / "meta-old")
    central_meta.mkdir()
    assert central_meta.stat().st_ino != original_identity.st_ino
    assert os.readlink(link) == str(central_meta)

    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    assert calls == [] and FakeAdapter.instances == []


def test_model_smoke_central_meta_retarget_during_sft_requires_final_audit(
        model_smoke, model_source, tmp_path, monkeypatch):
    _link_source_meta_to_central(model_source, tmp_path)
    plan = _model_plan(model_smoke, model_source, tmp_path, "central-meta-during-sft")
    root = Path(plan["output_root"])
    _patch_model_runtime(model_smoke, monkeypatch)
    alternate = _alternate_central_meta(model_source, tmp_path)

    def retarget(metrics, gpu, state, out, child, arguments):
        link = model_source["source_run"] / "meta"
        link.unlink()
        link.symlink_to(alternate, target_is_directory=True)

    MOCK_CONTROL["sft_mutator"] = retarget
    result = model_smoke.execute_model_smoke(root)

    assert result["status"] == "requires_audit"
    final = json.loads((root / "receipts/sft.json").read_text())
    assert final["status"] == "requires_audit"
    assert final["source_hashes_unchanged"] is False
