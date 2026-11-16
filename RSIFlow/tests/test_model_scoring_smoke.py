"""Offline tests for the independent minimal real-model scoring smoke runner.

No GPU, no model, no API and no official evaluator process is started here. Synthetic
receipts produced by these tests are test-only fixtures and are never real smoke evidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime"))
sys.path.insert(0, str(PROJECT / "reproduction" / "scripts"))

import model_scoring_smoke as smoke
from data_protocol import public_text, text_hash
from evolution_protocol import file_hash, fingerprint, freeze, make_manifest
from rsiflow.task_meta.durable import value_hash
from rsiflow.task_meta.reporting import OfficialEvaluatorSpec, ReportBlocked

CODE_QA = ("humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev")
SEED_HARNESS = ROOT / "seed_harness/rsiflow_base_manifest.json"


def _record(source, native_id, index):
    if source in ("hotpotqa_dev", "2wiki_dev"):
        return {"_id": native_id, "question": f"Question {index}?", "answer": f"answer-{index}",
                "context": [], "supporting_facts": [], "type": "comparison", "level": "medium"}
    if source in ("humaneval_plus", "mbpp_plus"):
        return {"task_id": native_id, "prompt": f"def f{index}():\n    ", "canonical_solution": "    return 1\n",
                "entry_point": f"f{index}", "test": "assert True", "base_input": [], "plus_input": [], "atol": 0}
    return {"id": native_id, "question": [{"role": "user", "content": f"call {index}"}]}


def _pack(directory, source, records):
    path = directory / f"{source}.jsonl"
    parts = []
    with path.open("wb") as stream:
        for record in records:
            line = (json.dumps(record, ensure_ascii=False) + "\n").encode()
            parts.append((stream.tell(), len(line), record))
            stream.write(line)
    digest = file_hash(path)
    return path, digest, parts


def build_parent(directory, *, bfcl_total=60, bfcl_gap_every=6, recognized=50):
    """Create a read-only parent manifest fixture with real pack hashes and offsets."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tasks = []
    official = set()
    for source in CODE_QA:
        ids = [f"{source}-native-{i}" for i in range(2)]
        records = [_record(source, nid, i) for i, nid in enumerate(ids)]
        path, digest, parts = _pack(directory, source, records)
        for (offset, length, record), nid in zip(parts, ids):
            tasks.append(dict(source=source, domain="searchqa" if source.endswith("_dev") else "code",
                              task_id=f"{source}:{nid}", native_id=nid, version="fixture-v1",
                              original_split="validation", group_id=nid, role="independent_validation",
                              round_id=None, purpose="report_only", exposure="exposure_unknown",
                              content_hash=text_hash(public_text(record, source=source)),
                              source_sha256=digest, record_file=str(path), record_sha256=digest,
                              record_offset=offset, record_length=length))
    bfcl_records, bfcl_ids = [], []
    for index in range(bfcl_total):
        gap = bfcl_gap_every and index % bfcl_gap_every == 0
        nid = f"bfcl_gap_{index}" if gap else f"bfcl_ok_{index}"
        if not gap:
            official.add(nid)
        bfcl_ids.append(nid)
        bfcl_records.append(_record("bfcl_v3", nid, index))
    path, digest, parts = _pack(directory, "bfcl_v3", bfcl_records)
    for (offset, length, record), nid in zip(parts, bfcl_ids):
        tasks.append(dict(source="bfcl_v3", domain="tool_use", task_id=f"bfcl_v3:{nid}", native_id=nid,
                          version="fixture-v1", original_split="validation", group_id=nid,
                          role="independent_validation", round_id=None, purpose="report_only",
                          exposure="exposure_unknown",
                          content_hash=text_hash(public_text(record, source="bfcl_v3")),
                          source_sha256=digest, record_file=str(path), record_sha256=digest,
                          record_offset=offset, record_length=length))
    value = make_manifest("independent_validation", None, tasks)
    manifest_path = directory / "manifest.json"
    freeze(manifest_path, value)
    evaluators = {}
    for source in CODE_QA:
        entry = directory / f"{source}_entry.py"
        entry.write_text("# fixture official entrypoint\n", encoding="utf-8")
        source_tasks = [t for t in tasks if t["source"] == source]
        ids_path = directory / f"{source}_ids.json"
        freeze(ids_path, [t["native_id"] for t in source_tasks])
        data_path = Path(source_tasks[0]["record_file"])
        evaluators[source] = dict(repository="https://example.invalid/official", commit="a" * 40,
                                  entrypoint=str(entry), entrypoint_sha256=file_hash(entry),
                                  python_executable=sys.executable, data_path=str(data_path),
                                  data_sha256=file_hash(data_path), task_ids_path=str(ids_path),
                                  task_ids_sha256=file_hash(ids_path),
                                  protocol="official_2wiki_original_v1" if source == "2wiki_dev" else "fixture_v1",
                                  release_version="v0.1.10" if source in ("humaneval_plus", "mbpp_plus") else None,
                                  timeout_seconds=60, metadata={})
    settings = {"benchmark_ids": [*CODE_QA, "bfcl_v3", "acebench"],
                "official_specs": str(directory / "evaluators.json"),
                "tool_benchmarks": {"bfcl_v3": {"repository_root": str(directory / "bfcl_repo")},
                                    "acebench": {"user_model": "fixture-user-model",
                                                 "user_api_key_env": "ACE_USER_API_KEY",
                                                 "user_base_url_env": "ACE_USER_BASE_URL"}},
                "validation_limits": {"bfcl_v3": 152, "acebench": 50, "humaneval_plus": 164,
                                      "mbpp_plus": 178, "hotpotqa_dev": 100, "2wiki_dev": 100}}
    freeze(directory / "evaluators.json", {"evaluators": evaluators})
    settings_path = directory / "validation.json"
    freeze(settings_path, settings)
    pipeline_config_path = directory / "launcher.json"
    freeze(pipeline_config_path, {"rounds": 5, "skip_acebench": True, "fixture": True})
    return SimpleNamespace(manifest_path=manifest_path, manifest=value, tasks=tasks, official=official,
                           evaluators=evaluators, settings_path=settings_path, directory=directory,
                           pipeline_config_path=pipeline_config_path)


@pytest.fixture(autouse=True)
def project_root(tmp_path, monkeypatch):
    """Output roots must live in a Git-ignored directory inside PROJECT."""
    monkeypatch.setattr(smoke, "PROJECT", tmp_path)
    (tmp_path / ".gitignore").write_text("**/logs/\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def parent(tmp_path):
    return build_parent(tmp_path / "parent")


def _config(tmp_path, checkpoint):
    return SimpleNamespace(task_checkpoint=str(checkpoint), output_root=str(tmp_path / "out"),
                           task_replicas=[{"gpu": i, "base_url": f"http://127.0.0.1:{8271 + i}/v1"} for i in range(4)],
                           data_dir=str(tmp_path / "data"), retrieval_index=str(tmp_path / "search.sqlite"),
                           trainer_python=sys.executable, model_dump=lambda: {"schema_version": 1})


def _checkpoint(tmp_path):
    checkpoint = tmp_path / "Qwen3-4B"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"fixture-weights")
    return checkpoint


def _rows(ids, state_hash, *, answer="def f():\n    return 1\n", calls=True):
    rows = []
    for task_id in ids:
        row = {"task_id": task_id, "split": "report_eval", "state_hash": state_hash,
               "final_submission_count": 1, "final_answer": answer, "infrastructure_error": False,
               "trajectory": {}, "evaluation_status": "pending_official",
               "transport_calls": [{"status": "completed", "model": "local"}] if calls else [],
               "wall_seconds": 0.1, "record_hash": "fixture-record-hash"}
        rows.append(row)
    return rows


# --- deterministic selection -------------------------------------------------

def test_selection_is_deterministic_and_takes_first_complete_task_per_code_qa(parent):
    first = smoke.select_subset(parent.tasks, parent.official)
    second = smoke.select_subset(list(parent.tasks), set(parent.official))
    assert [t["task_id"] for t in first["tasks"]] == [t["task_id"] for t in second["tasks"]]
    for source in CODE_QA:
        chosen = [t for t in first["tasks"] if t["source"] == source]
        assert len(chosen) == 1
        expected = next(t for t in parent.tasks if t["source"] == source)
        assert chosen[0]["task_id"] == expected["task_id"]
    assert len(first["tasks"]) == 54


def test_bfcl_selects_exactly_first_50_recognized_complete_tasks_in_parent_order(parent):
    selected = smoke.select_subset(parent.tasks, parent.official)
    bfcl = [t for t in selected["tasks"] if t["source"] == "bfcl_v3"]
    assert len(bfcl) == 50
    assert len({t["task_id"] for t in bfcl}) == 50
    expected = [t["task_id"] for t in parent.tasks
                if t["source"] == "bfcl_v3" and t["native_id"] in parent.official][:50]
    assert [t["task_id"] for t in bfcl] == expected
    assert all(t["native_id"] in parent.official for t in bfcl)


def test_bfcl_unrecognized_ids_are_reported_as_parent_protocol_gap(parent):
    selected = smoke.select_subset(parent.tasks, parent.official)
    gaps = selected["bfcl_unrecognized"]
    assert len(gaps) == 10
    assert all(entry["native_ids"] and not set(entry["native_ids"]) & parent.official for entry in gaps)
    assert selected["bfcl_recognized_total"] == 50
    assert "694" not in selected["rule"]


def test_bfcl_fewer_than_50_recognized_fails_closed(tmp_path):
    sparse = build_parent(tmp_path / "sparse", bfcl_total=45, bfcl_gap_every=6)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_subset(sparse.tasks, sparse.official)


def test_ambiguous_or_missing_native_identity_fails_closed(parent):
    broken = [dict(t) for t in parent.tasks]
    target = next(t for t in broken if t["source"] == "bfcl_v3" and t["native_id"] in parent.official)
    target["native_ids"] = []
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_subset(broken, parent.official)
    duplicate = [dict(t) for t in parent.tasks]
    duplicate.append(dict(duplicate[0]))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_subset(duplicate, parent.official)


def test_native_ids_family_must_be_complete_and_consistent(parent):
    tasks = [dict(t) for t in parent.tasks]
    target = next(t for t in tasks if t["source"] == "bfcl_v3" and t["native_id"] in parent.official)
    target["native_ids"] = [target["native_id"], "bfcl_not_in_official"]
    with pytest.raises(smoke.SmokeBlocked):
        smoke.select_subset(tasks, parent.official)


# --- record / spec / binding integrity ---------------------------------------

def test_record_read_verifies_pack_hash_offset_and_content_hash(parent):
    task = parent.tasks[0]
    record = smoke.read_task_record(task)
    assert record.get("_id") == task["native_id"] or record.get("task_id") == task["native_id"]
    tampered = dict(task, record_sha256="0" * 64)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.read_task_record(tampered)
    wrong_hash = dict(task, content_hash="0" * 64)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.read_task_record(wrong_hash)


def test_subset_specs_preserve_official_identity_and_bind_parent_manifest(tmp_path, parent):
    selected = smoke.select_subset(parent.tasks, parent.official)
    plan = smoke.build_plan_artifacts(tmp_path / "logs" / "plan", parent.manifest, parent.manifest["manifest_hash"],
                                      parent.official, parent.evaluators)
    for source in CODE_QA:
        spec = plan["specs"][source]
        original = parent.evaluators[source]
        for field in ("repository", "commit", "entrypoint", "entrypoint_sha256", "python_executable",
                      "protocol", "release_version", "timeout_seconds"):
            assert spec[field] == original[field]
        assert spec["data_path"] != original["data_path"]
        assert spec["metadata"]["parent_manifest_hash"] == parent.manifest["manifest_hash"]
        assert spec["metadata"]["smoke_only"] is True
        ids = OfficialEvaluatorSpec(**spec, benchmark=source).validate()
        assert len(ids) == 1
    assert plan["subset_hash"] == file_hash(plan["subset_path"])
    assert len(json.loads(Path(plan["subset_path"]).read_text())["task_ids"]) == 54


def test_subset_spec_rejects_tampered_data(tmp_path, parent):
    plan = smoke.build_plan_artifacts(tmp_path / "logs" / "plan", parent.manifest, parent.manifest["manifest_hash"],
                                      parent.official, parent.evaluators)
    spec = dict(plan["specs"]["hotpotqa_dev"], data_sha256="0" * 64)
    with pytest.raises(ReportBlocked):
        OfficialEvaluatorSpec(**{**spec, "benchmark": "hotpotqa_dev"}).validate()


def test_parent_spec_carrying_benchmark_key_is_normalized_for_dynamic_stage(tmp_path, parent):
    # The machine report-evaluators-v4.json specs carry a "benchmark" field; generate_dynamic
    # constructs OfficialEvaluatorSpec(**spec, benchmark=identifier), so the subset spec must not
    # duplicate that keyword.
    evaluators = {name: {**spec, "benchmark": "HumanEval+"} for name, spec in parent.evaluators.items()}
    plan = smoke.build_plan_artifacts(tmp_path / "logs" / "plan", parent.manifest, parent.manifest["manifest_hash"],
                                      parent.official, evaluators)
    for source in CODE_QA:
        assert "benchmark" not in plan["specs"][source]
        assert len(OfficialEvaluatorSpec(**{**plan["specs"][source], "benchmark": source}).validate()) == 1


def test_binding_and_snapshot_are_smoke_only_and_bind_parent_manifest(tmp_path, parent):
    checkpoint = _checkpoint(tmp_path)
    config = _config(tmp_path, checkpoint)
    plan = smoke.build_plan_artifacts(tmp_path / "logs" / "plan", parent.manifest, parent.manifest["manifest_hash"],
                                      parent.official, parent.evaluators)
    frozen_path, binding = smoke.freeze_smoke_task(
        tmp_path / "frozen", config, parent_manifest_hash=parent.manifest["manifest_hash"],
        subset_hash=plan["subset_hash"], settings_path=parent.settings_path,
        pipeline_config_path=parent.settings_path)
    assert binding["status"] == "frozen_for_report_eval"
    assert binding["source_role"] == "independent_validation"
    assert binding["purpose"] == "report_only"
    assert binding["explicit_full_benchmark"] is False
    assert binding["manifest_hash"] == parent.manifest["manifest_hash"]
    assert binding["smoke_subset_hash"] == plan["subset_hash"]
    assert binding["state_hash"]
    state = json.loads(Path(binding["task_state"]).read_text())
    assert state["generation"] == 0
    assert state["checkpoint_path"] == str(checkpoint)
    assert json.loads(frozen_path.read_text()) == binding
    source_round = json.loads((tmp_path / "frozen" / "source_round.json").read_text())
    assert source_round["smoke_only"] is True
    assert source_round["rounds_completed"] == 0


# --- prediction evidence classification --------------------------------------

def test_empty_prediction_is_blocked_not_model_zero():
    rows = _rows(["a"], "hash", answer="   ")
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(rows, ["a"], "hash")


def test_wrong_state_hash_duplicate_id_and_missing_id_are_blocked():
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(_rows(["a"], "other"), ["a"], "hash")
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(_rows(["a", "a"], "hash"), ["a"], "hash")
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(_rows(["a"], "hash"), ["a", "b"], "hash")


def test_missing_completed_model_call_is_blocked():
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(_rows(["a"], "hash", calls=False), ["a"], "hash")
    rows = _rows(["a"], "hash")
    rows[0]["transport_calls"] = [{"status": "requires_audit"}]
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(rows, ["a"], "hash")


def test_infrastructure_error_row_is_blocked():
    rows = _rows(["a"], "hash")
    rows[0]["infrastructure_error"] = True
    with pytest.raises(smoke.SmokeBlocked):
        smoke.validate_prediction_rows(rows, ["a"], "hash")


def test_wrong_model_answer_is_a_valid_scored_submission():
    rows = _rows(["a"], "hash", answer="def f():\n    return None\n")
    smoke.validate_prediction_rows(rows, ["a"], "hash")


# --- plan / execute boundaries -----------------------------------------------

def test_plan_never_starts_services_or_calls_the_model(tmp_path, parent, monkeypatch):
    for name in ("start_services", "verify_services", "generate_dynamic", "evaluate_official", "evaluate_tool"):
        monkeypatch.setattr(smoke, name, lambda *a, **k: pytest.fail(f"{name} must not run in plan"))
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: _config(tmp_path, _checkpoint(tmp_path)))
    monkeypatch.setattr(smoke, "official_native_ids", lambda settings: parent.official)
    receipt = smoke.plan(tmp_path / "logs" / "plan", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert receipt["mode"] == "plan"
    assert receipt["scope"] == smoke.SCOPE
    assert receipt["smoke_selected_tasks"] == 54
    assert receipt["bfcl_unrecognized_count"] == 10
    assert receipt["model_calls"] == 0
    assert not (tmp_path / "logs" / "plan" / "complete.json").exists()


def test_execute_without_explicit_bwrap_backend_fails_closed(tmp_path, parent, monkeypatch):
    monkeypatch.delenv("RSIFLOW_SANDBOX_BACKEND", raising=False)
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: pytest.fail("must not reach config"))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)


def test_execute_fails_closed_on_asset_blockers(tmp_path, parent, monkeypatch):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", smoke.BACKEND)
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: _config(tmp_path, _checkpoint(tmp_path)))
    monkeypatch.setattr(smoke, "start_services", lambda *a, **k: pytest.fail("must not start services"))
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["status"] == "incomplete"
    assert report["assets"]["blockers"]
    assert report["benchmarks"] == {}


def test_execute_requires_explicit_flag_and_fresh_output_root(tmp_path, parent, monkeypatch):
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: _config(tmp_path, _checkpoint(tmp_path)))
    existing = tmp_path / "logs" / "already"
    existing.mkdir(parents=True)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute(existing, str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    seen = {}

    def fake_plan(*a, **k):
        seen["mode"] = "plan"
        return {"mode": "plan", "status": "planned", "output_root": str(tmp_path / "logs" / "planned"),
                "smoke_selected_tasks": 54, "bfcl_unrecognized_count": 10,
                "assets": {"blockers": []}, "execute_ready": True}

    monkeypatch.setattr(smoke, "plan", fake_plan)
    monkeypatch.setattr(smoke, "execute", lambda *a, **k: pytest.fail("execute must be opt-in"))
    smoke.main(["--output-root", str(tmp_path / "logs" / "planned"), "--config", str(parent.pipeline_config_path),
                "--manifest", str(parent.manifest_path), "--settings", str(parent.settings_path)])
    assert seen["mode"] == "plan"


def _write_bfcl_evidence(out, binding, ids, pred_path, mode):
    """Test-only synthetic BFCL evidence with trusted-generator bindings; never real evidence."""
    pred_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"id": nid, "result": f"[native_call({nid})]"} for nid in ids]
    if mode == "empty_submission":
        rows[0]["result"] = "   "
    elif mode == "ambiguous_submission":
        rows[0]["response"] = "another non-empty submission"
    elif mode == "no_submission_field":
        rows[0].pop("result")
    pred_path.write_text(json.dumps(rows), encoding="utf-8")
    transport = out / "transport"
    transport.mkdir(parents=True, exist_ok=True)
    keys = {nid: f"receipt-{nid}" for nid in ids}
    if mode == "shared_receipt":
        keys[ids[1]] = keys[ids[0]]
    requests = {}
    for nid, key in keys.items():
        request = {"messages": [{"role": "user", "content": f"official dialogue for {nid}"}], "tools": []}
        requests[nid] = request
        if mode == "receipt_missing" and nid == ids[0]:
            continue
        receipt = {"status": "completed",
                   "identity": {"state_hash": binding["state_hash"], "request": request,
                                "protocol": "official_outer_saved_harness_inner_v1"},
                   "user_simulator": False, "calls": [{"status": "completed", "usage": {}}]}
        if mode == "receipt_not_completed" and nid == ids[0]:
            receipt["status"] = "requires_audit"
        elif mode == "call_not_completed" and nid == ids[0]:
            receipt["calls"] = [{"status": "dispatching"}]
        elif mode == "user_simulator_receipt" and nid == ids[0]:
            receipt["user_simulator"] = True
        elif mode == "wrong_state_hash" and nid == ids[0]:
            receipt["identity"]["state_hash"] = "other-state"
        elif mode == "receipt_request_missing" and nid == ids[0]:
            receipt["identity"].pop("request")
        (transport / f"{key}.json").write_text(json.dumps(receipt), encoding="utf-8")
    if mode == "missing_attribution":
        return None
    attribution = {
        "generator": smoke.TRUSTED_ATTRIBUTION_GENERATOR,
        "state_hash": binding["state_hash"],
        "selected_task_ids": list(ids),
        "selected_task_ids_sha256": value_hash(list(ids)),
        "tasks": {nid: [{"receipt_key": keys[nid], "request_sha256": value_hash(requests[nid])}] for nid in ids},
    }
    if mode == "attribution_wrong_state":
        attribution["state_hash"] = "other-state"
    elif mode == "attribution_missing_task":
        attribution["tasks"].pop(ids[0])
    elif mode == "untrusted_generator":
        attribution["generator"] = "hand_written_fixture_v0"
    elif mode == "attribution_missing_generator":
        attribution.pop("generator")
    elif mode == "selected_ids_hash_mismatch":
        attribution["selected_task_ids_sha256"] = "0" * 64
    elif mode == "request_hash_mismatch":
        attribution["tasks"][ids[0]][0]["request_sha256"] = "0" * 64
    elif mode == "request_hash_missing":
        attribution["tasks"][ids[0]] = [keys[ids[0]]]
    elif mode == "attribution_wrong_selected_ids":
        attribution["selected_task_ids"] = list(ids)[:-1]
    attribution_path = out / "task_call_attribution.json"
    attribution_path.write_text(json.dumps(attribution), encoding="utf-8")
    result_binding = {"generator": smoke.TRUSTED_ATTRIBUTION_GENERATOR, "path": str(attribution_path),
                      "sha256": file_hash(attribution_path)}
    if mode == "result_binding_missing":
        return None
    if mode == "result_binding_wrong_hash":
        result_binding["sha256"] = "0" * 64
    elif mode == "result_binding_wrong_generator":
        result_binding["generator"] = "hand_written_fixture_v0"
    elif mode == "result_binding_external_path":
        result_binding["path"] = str(out.parent / "elsewhere.json")
    elif mode == "forged_attribution_tampered":
        # File changed after the official result bound its hash: recomputation must catch it.
        attribution["tasks"][ids[0]][0]["receipt_key"] = keys[ids[1]]
        attribution_path.write_text(json.dumps(attribution), encoding="utf-8")
    return result_binding


def _patch_execute(tmp_path, parent, monkeypatch, *, official_status="completed", tool_result=None,
                   stop_error=None, start_error=None, bfcl_evidence="valid"):
    checkpoint = _checkpoint(tmp_path)
    config = _config(tmp_path, checkpoint)
    calls = []
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", smoke.BACKEND)

    def fake_config(config_path, output_root):
        config.output_root = str(output_root)
        return config

    monkeypatch.setattr(smoke, "effective_config", fake_config)
    monkeypatch.setattr(smoke, "official_native_ids", lambda settings: parent.official)
    monkeypatch.setattr(smoke, "asset_checks", lambda *a, **k: {"checks": {"fixture": True}, "blockers": []})

    def fake_start(*a, **k):
        calls.append("start")
        registry = Path(config.output_root) / "services" / "four_gpu_services.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({"phase": "starting", "services": []}), encoding="utf-8")
        if start_error:
            raise start_error

    monkeypatch.setattr(smoke, "start_services", fake_start)
    monkeypatch.setattr(smoke, "verify_services", lambda *a, **k: calls.append("verify") or [
        {"gpu": i, "base_url": f"http://127.0.0.1:{8271 + i}/v1"} for i in range(4)])

    def fake_stop(*a, **k):
        calls.append("stop")
        if stop_error:
            raise stop_error

    monkeypatch.setattr(smoke, "stop_services", fake_stop)

    def fake_dynamic(cfg, frozen_path, specs_path, output_dir):
        calls.append("generate")
        spec = json.loads(Path(specs_path).read_text())["evaluators"]
        identifier = next(iter(spec))
        ids = json.loads(Path(spec[identifier]["task_ids_path"]).read_text())
        state_hash = json.loads(Path(frozen_path).read_text())["state_hash"]
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{identifier}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _rows(ids, state_hash)), encoding="utf-8")
        return {"status": "submissions_completed_unscored", "count": len(ids)}

    def fake_official(spec, rows, binding, output_dir, **kwargs):
        calls.append("official")
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        result = {"status": official_status, "metrics": {"EM": 0.0, "F1": 0.0},
                  "expected": len(rows), "completed": len(rows), "state_hash": binding["state_hash"]}
        (out / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    def fake_tool(identifier, cfg, binding, spec, user_spec, output):
        calls.append("tool")
        assert identifier == "bfcl_v3"
        assert len(spec["selected_tasks"]) == 50
        assert len(set(spec["selected_task_ids"])) == len(spec["selected_task_ids"])
        assert user_spec["user_model"] == "fixture-user-model"
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        ids = list(spec["selected_task_ids"])
        pred_path = out / "native_work" / "BFCL_v3_irrelevance_result.json"
        attribution_binding = None if tool_result is not None else _write_bfcl_evidence(
            out, binding, ids, pred_path, bfcl_evidence)
        result = tool_result or {
            "status": "completed", "expected": 50, "completed": 50,
            "metrics": {"category_macro_accuracy": 0.5},
            "categories": [{"category": "irrelevance", "accuracy": 0.5, "denominator": 50}],
            "task_results": [{"native_id": nid, "success": True, "scoring_status": "completed",
                              "execution_status": "completed", "category": "irrelevance",
                              "trajectory_path": str(pred_path)} for nid in ids]}
        if attribution_binding is not None:
            result["task_call_attribution"] = attribution_binding
        (out / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setattr(smoke, "generate_dynamic", fake_dynamic)
    monkeypatch.setattr(smoke, "evaluate_official", fake_official)
    monkeypatch.setattr(smoke, "evaluate_tool", fake_tool)
    monkeypatch.setattr(smoke, "official_task_scores",
                        lambda identifier, spec, rows, directory, frozen: {
                            str(row["task_id"]): {"success": False, "native_scores": {}} for row in rows})
    return calls, config


def test_execute_stage_order_and_owned_service_cleanup(tmp_path, parent, monkeypatch):
    calls, config = _patch_execute(tmp_path, parent, monkeypatch)
    root = tmp_path / "logs" / "execute"
    report = smoke.execute(root, str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert calls[0] == "start" and calls[1] == "verify"
    assert calls[-1] == "stop"
    assert calls.count("generate") == 4
    assert calls.count("official") == 4
    assert calls.count("tool") == 1
    assert report["cleanup"]["services_stopped"] is True
    assert report["cleanup"]["registry"] == str(root / "services" / "four_gpu_services.json")
    assert report["ace"]["executed"] is False
    assert report["ace"]["pending_tasks"] == 50
    assert not (root / "complete.json").exists()


def test_execute_never_triggers_ace_user_client(tmp_path, parent, monkeypatch):
    calls, config = _patch_execute(tmp_path, parent, monkeypatch)
    root = tmp_path / "logs" / "execute"
    report = smoke.execute(root, str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert not list(root.rglob("user_*.receipt"))
    assert report["ace"]["user_simulator"] is False
    assert "acebench" not in report["benchmarks"]


def test_execute_reports_per_benchmark_denominators_without_aggregate_score(tmp_path, parent, monkeypatch):
    _patch_execute(tmp_path, parent, monkeypatch)
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    for source in CODE_QA:
        assert report["benchmarks"][source]["expected_tasks"] == 1
    assert report["benchmarks"]["bfcl_v3"]["expected_tasks"] == 50
    assert "overall_accuracy" not in report
    assert "694" not in json.dumps(report["scope"])
    assert report["parent_manifest"]["frozen_validation_tasks"] == 744
    assert report["parent_manifest"]["scored_validation_tasks"] == 694
    assert report["smoke_selected_tasks"] == 54


def test_official_wrong_answer_is_scored_not_blocked(tmp_path, parent, monkeypatch):
    _patch_execute(tmp_path, parent, monkeypatch, official_status="completed")
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["benchmarks"]["hotpotqa_dev"]["status"] == "scored"
    assert report["status"] in ("completed", "completed_with_model_failures")


def test_official_blocked_result_never_becomes_model_zero(tmp_path, parent, monkeypatch):
    _patch_execute(tmp_path, parent, monkeypatch, official_status="blocked")
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["benchmarks"]["hotpotqa_dev"]["status"] == "blocked"
    assert report["status"] == "incomplete"
    assert report["benchmarks"]["hotpotqa_dev"].get("model_zero_score") is not True


def test_bfcl_evidence_gap_is_blocked(tmp_path, parent, monkeypatch):
    partial = {"status": "completed", "expected": 50, "completed": 50, "metrics": {},
               "categories": [], "task_results": [{"native_id": "only-one", "success": True}]}
    _patch_execute(tmp_path, parent, monkeypatch, tool_result=partial)
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["benchmarks"]["bfcl_v3"]["status"] == "blocked"
    assert report["status"] == "incomplete"


def test_cleanup_failure_preserves_original_exception_and_is_reported(tmp_path, parent, monkeypatch):
    calls, _ = _patch_execute(tmp_path, parent, monkeypatch, official_status="blocked",
                              stop_error=RuntimeError("four GPUs did not release memory"))
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["status"] == "incomplete"
    assert report["cleanup"]["services_stopped"] is False
    assert "did not release" in report["cleanup"]["error"]
    assert calls[-1] == "stop"


def test_service_start_failure_still_attempts_owned_cleanup(tmp_path, parent, monkeypatch):
    calls, _ = _patch_execute(tmp_path, parent, monkeypatch, start_error=RuntimeError("GPU 2 busy"))
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["status"] == "incomplete"
    assert "start" in calls and "stop" in calls
    assert report["cleanup"]["error"] or report["stages"]["services"]["status"] == "blocked"


def test_empty_model_answer_blocks_benchmark_and_keeps_other_evidence(tmp_path, parent, monkeypatch):
    calls, config = _patch_execute(tmp_path, parent, monkeypatch)
    original = smoke.generate_dynamic

    def empty_dynamic(cfg, frozen_path, specs_path, output_dir):
        result = original(cfg, frozen_path, specs_path, output_dir)
        identifier = next(iter(json.loads(Path(specs_path).read_text())["evaluators"]))
        path = Path(output_dir) / f"{identifier}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if identifier == "mbpp_plus":
            rows[0]["final_answer"] = ""
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        return result

    monkeypatch.setattr(smoke, "generate_dynamic", empty_dynamic)
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
    assert report["benchmarks"]["mbpp_plus"]["status"] == "blocked"
    assert report["status"] == "incomplete"
    assert report["cleanup"]["services_stopped"] is True


# --- BFCL per-task submission and attributable local-call evidence ------------

def test_bfcl_valid_evidence_is_scored_and_completed_calls_are_counted(tmp_path, parent, monkeypatch):
    _patch_execute(tmp_path, parent, monkeypatch)
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path),
                           parent.manifest_path, parent.settings_path)
    bfcl = report["benchmarks"]["bfcl_v3"]
    assert bfcl["status"] == "scored"
    assert bfcl["completed_model_calls"] == 50
    assert bfcl["verified_native_submissions"] == 50
    assert bfcl["task_call_attribution_sha256"]
    # Code/QA contribute one completed call each; BFCL must not be omitted from the total.
    assert report["model_calls"] == 54


BFCL_BLOCK_REASONS = {
    "missing_attribution": "does not bind a trusted task-call attribution",
    "result_binding_missing": "does not bind a trusted task-call attribution",
    "result_binding_wrong_generator": "Untrusted attribution generator in official result binding",
    "result_binding_external_path": "escapes the benchmark output directory",
    "result_binding_wrong_hash": "hash does not match the official result binding",
    "forged_attribution_tampered": "hash does not match the official result binding",
    "untrusted_generator": "Untrusted attribution generator",
    "attribution_missing_generator": "Untrusted attribution generator",
    "attribution_wrong_state": "not bound to the frozen smoke state",
    "attribution_wrong_selected_ids": "selected native ids",
    "selected_ids_hash_mismatch": "selected-task-id hash",
    "attribution_missing_task": "does not cover exactly the selected native ids",
    "request_hash_missing": "request-content hash binding",
    "request_hash_mismatch": "request-content hash mismatch",
    "receipt_request_missing": "no request content",
    "shared_receipt": "claimed by more than one native id",
    "receipt_missing": "transport receipt missing",
    "receipt_not_completed": "transport receipt not completed",
    "call_not_completed": "no completed local model call",
    "user_simulator_receipt": "not a local Task call",
    "wrong_state_hash": "not bound to the frozen smoke state",
    "empty_submission": "Empty or ambiguous native submission",
    "ambiguous_submission": "Empty or ambiguous native submission",
    "no_submission_field": "No recognized native submission field",
}


@pytest.mark.parametrize("mode", sorted(BFCL_BLOCK_REASONS))
def test_bfcl_unverifiable_task_evidence_is_blocked_not_scored(tmp_path, parent, monkeypatch, mode):
    _patch_execute(tmp_path, parent, monkeypatch, bfcl_evidence=mode)
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path),
                           parent.manifest_path, parent.settings_path)
    bfcl = report["benchmarks"]["bfcl_v3"]
    assert bfcl["status"] == "blocked", mode
    assert BFCL_BLOCK_REASONS[mode] in bfcl["error"], (mode, bfcl["error"])
    assert bfcl.get("completed_model_calls", 0) == 0
    assert report["status"] == "incomplete"
    assert report["model_calls"] == 4


def test_bfcl_official_completed_without_evidence_is_not_scored(tmp_path, parent, monkeypatch):
    # The official runner reporting "completed" is not sufficient evidence on its own.
    _patch_execute(tmp_path, parent, monkeypatch, bfcl_evidence="missing_attribution")
    report = smoke.execute(tmp_path / "logs" / "execute", str(parent.pipeline_config_path),
                           parent.manifest_path, parent.settings_path)
    assert report["benchmarks"]["bfcl_v3"]["official_status"] == "completed"
    assert report["benchmarks"]["bfcl_v3"]["status"] == "blocked"


# --- output root must be a new Git-ignored directory inside PROJECT -----------

def test_output_root_outside_project_is_rejected(tmp_path, parent, monkeypatch):
    monkeypatch.setattr(smoke, "PROJECT", tmp_path / "project")
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / ".gitignore").write_text("**/logs/\n", encoding="utf-8")
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: pytest.fail("must not reach config"))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.plan(tmp_path / "outside" / "run", str(parent.pipeline_config_path),
                   parent.manifest_path, parent.settings_path)
    with pytest.raises(smoke.SmokeBlocked):
        smoke.execute(tmp_path / "outside" / "run", str(parent.pipeline_config_path),
                      parent.manifest_path, parent.settings_path)


def test_output_root_in_trackable_path_is_rejected(tmp_path, parent, monkeypatch):
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: pytest.fail("must not reach config"))
    for target in (tmp_path / "RSIFlow" / "tests" / "run", tmp_path / "reproduction" / "configs" / "run"):
        with pytest.raises(smoke.SmokeBlocked):
            smoke.plan(target, str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)
        with pytest.raises(smoke.SmokeBlocked):
            smoke.execute(target, str(parent.pipeline_config_path), parent.manifest_path, parent.settings_path)


def test_git_authoritative_not_ignored_is_rejected(tmp_path, parent, monkeypatch):
    monkeypatch.setattr(smoke, "_git_check_ignore", lambda path: False)
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: pytest.fail("must not reach config"))
    with pytest.raises(smoke.SmokeBlocked):
        smoke.plan(tmp_path / "logs" / "run", str(parent.pipeline_config_path),
                   parent.manifest_path, parent.settings_path)


def test_output_root_verification_method_is_recorded(tmp_path, parent, monkeypatch):
    monkeypatch.setattr(smoke, "effective_config", lambda *a, **k: _config(tmp_path, _checkpoint(tmp_path)))
    monkeypatch.setattr(smoke, "official_native_ids", lambda settings: parent.official)
    receipt = smoke.plan(tmp_path / "logs" / "plan", str(parent.pipeline_config_path),
                         parent.manifest_path, parent.settings_path)
    assert receipt["output_root_validation"]["git_ignored"] is True
    assert receipt["output_root_validation"]["verification"] in ("git_check_ignore", "structural_gitignore_ancestor")
