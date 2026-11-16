"""Offline coverage for five frozen 600-task rounds and final complete-Task eval."""
import json
from collections import Counter
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'runtime')]
from evolution_protocol import read, training_quotas, assert_disjoint
from experiment_progress import ExperimentProgress
from launch_meta import DEFAULT_CONFIG, experiment_prompt
from prepare_600_744 import split_tasks
from task_adapter import TaskAdapter


def test_new_default_normalizes_600_and_keeps_old_configuration(tmp_path):
    config = read(DEFAULT_CONFIG)
    assert config['rounds'] == 5
    assert config['allocated_tasks'] == 3000
    assert sum(training_quotas(config).values()) == 600
    adapter = TaskAdapter(ROOT, tmp_path / 'receipts')
    runtime = adapter._config({'config_path': str(DEFAULT_CONFIG)})
    assert runtime.window_quotas == dict(tool_use=200, code=200, searchqa=200)
    assert runtime.max_generations == 5
    assert runtime.round_protocol['validation_manifest'] == config['validation_manifest']
    assert not runtime.round_protocol['skip_acebench']
    assert runtime.task_checkpoint.endswith('Qwen3-4B')
    assert len(runtime.task_replicas) == 4
    assert runtime.training['num_train_epochs'] == 1
    assert read(ROOT / 'configs/train_180_a0_v1.json')['training_tasks_per_pass'] == 180


def test_split_is_deterministic_and_retains_every_task():
    tasks = [dict(source='envscaler', task_id=f't{i}', content_hash=f'h{i}',
                  group_id='same_environment', round_id=1) for i in range(1000)]
    batches = split_tasks(tasks)
    assert [len(b['tasks']) for b in batches] == [200] * 5
    assert split_tasks(list(reversed(tasks))) == batches
    assert {t['task_id'] for b in batches for t in b['tasks']} == {t['task_id'] for t in tasks}


def test_final_only_ledger_requires_eval_only_at_b5(tmp_path):
    (tmp_path / 'workflow_policy.json').write_text(json.dumps({
        'rounds': 5, 'evaluation_schedule': 'final_only'}))
    progress = ExperimentProgress(tmp_path, rounds=5)
    result = progress.rebuild()
    assert all('independent_validation' not in s['required_milestones'] for s in result['stages'][:-1])
    assert 'independent_validation' in result['stages'][-1]['required_milestones']
    assert all('task_meta_snapshot' in s['required_milestones'] for s in result['stages'])
    # Complete all required milestones except final evaluation. Do not invoke any model.
    from test_experiment_progress import _turn
    mapping = {'meta_evidence': 'prepare_meta_evidence', 'decision_recorded': 'write_text',
               'candidate_built': 'rsiflow', 'candidate_rollout': 'run_candidate',
               'scores_compared': 'compare_scores', 'selection_recorded': 'write_text',
               'skills_appended': 'append_skills', 'context_updated': 'write_text',
               'task_meta_snapshot': 'snapshot_task_meta', 'parent_rollout': 'run_parent',
               'validation_snapshot': 'prepare_validation_snapshot'}
    index = 0
    for stage in result['stages']:
        number = stage['round']
        for milestone in stage['required_milestones']:
            if milestone == 'independent_validation': continue
            path = {'decision_recorded': f'/run/round_{number}/decision.json',
                    'selection_recorded': f'/run/round_{number}/selection.json',
                    'context_updated': '/run/meta/context.json'}.get(milestone)
            _turn(tmp_path, index, mapping.get(milestone, milestone), round_number=number, path=path)
            index += 1
    state = progress.rebuild()
    assert not state['finish_allowed']
    assert state['current_stage'] == 'B5'
    assert state['next_milestone'] == 'independent_validation'
    _turn(tmp_path, index, 'evaluate', round_number=5, status='exited', returncode=0)
    assert progress.rebuild()['finish_allowed']


def test_prompt_schedule_does_not_conflict(tmp_path):
    prompt = experiment_prompt(DEFAULT_CONFIG, read(DEFAULT_CONFIG), tmp_path)
    assert '"training_tasks_per_round": 600' in prompt
    assert 'Only after FINAL-round acceptance' in prompt
    assert 'saved model/checkpoint + Harness + Artifacts' in prompt
    assert 'Run 180 parent' not in prompt
    assert 'round=0, then evaluate' not in prompt
    assert 'then report-only independent validation' not in prompt
    assert 'activate, snapshot, independently validate, then advance' not in prompt


def test_prepared_real_cohorts_and_native_loader(tmp_path):
    config = read(DEFAULT_CONFIG)
    data = Path(config['frozen_data_dir'])
    if not (data / 'READY.json').is_file(): pytest.skip('prepare_600_744.py not run on this host')
    from rsiflow.task_meta.round_evolution import RoundStore
    store = RoundStore(data / 'tasks.sqlite')
    seen = set()
    content = set()
    batches = []
    for number in range(1, 6):
        store.select_round(number)
        rows = store.current['tasks']
        assert len(rows) == 600
        assert Counter(r['source'] for r in rows) == config['train_quotas_per_round']
        assert Counter(r['strata']['interface'] for r in rows if r['domain'] == 'code') == {'stdio': 120, 'function': 80}
        assert not seen.intersection(r['task_id'] for r in rows)
        assert not content.intersection(r['content_hash'] for r in rows)
        seen.update(r['task_id'] for r in rows)
        content.update(r['content_hash'] for r in rows)
        assert all(store._task(r).task_id == r['task_id'] for r in rows)
        batches.append(store.current)
    original = read(config['source_training_manifest'])
    assert seen == {t['task_id'] for t in original['tasks']}
    heldout = read(config['validation_manifest'])
    assert_disjoint([original, heldout])
    assert {t['task_id'] for t in heldout['tasks']} == {t['task_id'] for t in read(config['source_validation_manifest'])['tasks']}
    settings = read(ROOT / config['validation_config'])
    from evaluation_manifest import select_specs
    value, specs = select_specs(settings, config['validation_manifest'], 'independent_validation', tmp_path / 'inputs')
    assert len(value['tasks']) == 744
    assert set(specs) == {'humaneval_plus', 'mbpp_plus', 'hotpotqa_dev', '2wiki_dev'}
    assert 'livecodebench' not in settings['benchmark_ids']


def test_six_benchmark_completion_uses_full_snapshot_without_gpu(tmp_path, monkeypatch):
    """Exercise evaluator completion on synthetic cached scores, not model outputs."""
    from types import SimpleNamespace
    import rsiflow.task_meta.pipeline as pipeline
    import rsiflow.task_meta.durable as durable
    import rsiflow.task_meta.storage as storage
    import rsiflow.task_meta.rsiflow_manifest as harness
    import rsiflow.task_meta.gpu_phases as gpu
    import validation_sources
    from validate import validate
    config = read(DEFAULT_CONFIG)
    if not Path(config['validation_manifest']).exists(): pytest.skip('real cohort not prepared')
    cohort = read(config['validation_manifest'])
    settings = read(ROOT / config['validation_config'])
    settings_path = tmp_path / 'settings.json'
    settings_path.write_text(json.dumps(settings))
    out = tmp_path / 'report'
    out.mkdir()
    selection = tmp_path / 'selection.json'
    selection.write_text('{}')
    state = {'checkpoint_path': '/selected/model', 'harness_path': '/selected/harness',
             'artifacts': {'directory': '/selected/artifacts', 'manifest': []}}
    snapshot = out / 'round_snapshot.json'
    snapshot.write_text(json.dumps({'round': 5, 'task_state': state, 'meta_state': {'version': 5},
                                   'source_round': str(selection), 'chosen_component': 'HARNESS'}))
    loaded = []
    def load_task(value):
        loaded.append(value)
        return SimpleNamespace(**value)
    monkeypatch.setattr(pipeline, 'load_config', lambda _: SimpleNamespace(output_root=tmp_path, round_protocol={'enabled': True}))
    monkeypatch.setattr(durable, 'load_task', load_task)
    monkeypatch.setattr(durable, 'task_hash', lambda _: 'selected-task')
    monkeypatch.setattr(storage, 'checkpoint_manifest', lambda _: [])
    monkeypatch.setattr(harness, 'load_manifest', lambda _: SimpleNamespace(identity='selected-harness'))
    monkeypatch.setattr(validation_sources, 'verify', lambda *args: None)
    monkeypatch.setattr(gpu, 'ensure_services', lambda *args: None)
    monkeypatch.setattr(gpu, 'verify_services', lambda *args: {'mock': True})
    for source in settings['benchmark_ids']:
        rows = [t for t in cohort['tasks'] if t['source'] == source]
        scores = [{'native_id': native_id, 'execution_status': 'completed',
                   'scoring_status': 'completed', 'success': False, 'native_scores': {}}
                  for row in rows for native_id in row.get('native_ids', [row['native_id']])]
        path = out / 'scores' / source / 'result.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'status': 'completed', 'state_hash': 'selected-task',
                                    'expected': len(scores), 'completed': len(scores), 'task_results': scores}))
    pipeline_path = tmp_path / 'config' / 'pipeline.json'
    pipeline_path.parent.mkdir()
    pipeline_path.write_text('{}')
    result = validate(snapshot, pipeline_path, settings_path, role='independent_validation',
                      manifest_path=config['validation_manifest'])
    assert result['status'] == 'completed'
    assert result['benchmarks_completed'] == 6
    assert result['completion']['n_scored'] == 744
    assert loaded == [state]
    assert read(out / 'frozen_task.json')['task_state'] == state


def test_dynamic_spec_drops_duplicate_runtime_benchmark_key():
    from dynamic_predictions import runtime_spec
    source = {"benchmark": "HumanEval+", "data_path": "x", "entrypoint": "y"}
    assert runtime_spec(source) == {"data_path": "x", "entrypoint": "y"}


def test_tool_selection_keeps_daily_50_and_allows_formal_bound_152():
    from tool_validation import validate_tool_selection
    daily = {"source_role": "independent_validation", "purpose": "report_only",
             "manifest_hash": "m", "system_snapshot_sha256": "s", "evaluation_config_sha256": "c",
             "evaluation_scope": {"manifest_hash": "m", "source_role": "independent_validation",
                                  "system_snapshot_sha256": "s", "evaluation_config_sha256": "c"}}
    assert validate_tool_selection("bfcl_v3", daily, [{"task_id": str(i)} for i in range(50)]) == 50
    formal = {**daily, "formal_report_evaluation": True}
    assert validate_tool_selection("bfcl_v3", formal, [{"task_id": str(i)} for i in range(152)]) == 152
    with pytest.raises(ValueError):
        validate_tool_selection("bfcl_v3", {**formal, "evaluation_scope": {"manifest_hash": "other"}},
                                [{"task_id": str(i)} for i in range(152)])
    with pytest.raises(ValueError):
        validate_tool_selection("acebench", formal, [{"task_id": str(i)} for i in range(152)])
    with pytest.raises(ValueError):
        validate_tool_selection("bfcl_v3", {"source_role": "independent_validation"}, [{"task_id": str(i)} for i in range(152)])


def test_final_test_round_gate_uses_effective_pipeline_rounds():
    from validate import required_final_rounds
    class Config:
        max_generations = 5
    assert required_final_rounds(Config()) == 5
    Config.max_generations = 3
    assert required_final_rounds(Config()) == 3
    Config.max_generations = 0
    with pytest.raises(ValueError, match="positive integer"):
        required_final_rounds(Config())


def test_bfcl_attribution_requires_explicit_native_context():
    from tool_validation import attribution_from_receipts
    with pytest.raises(RuntimeError, match="attribution"):
        attribution_from_receipts({}, {"n1": "missing"}, "state", ["n1"], {"n1": "simple"})


def _patch_validate_scaffold(monkeypatch, tmp_path, settings, manifest, selected_specs):
    from types import SimpleNamespace
    import rsiflow.task_meta.pipeline as pipeline
    import rsiflow.task_meta.durable as durable
    import rsiflow.task_meta.storage as storage
    import rsiflow.task_meta.rsiflow_manifest as harness
    import rsiflow.task_meta.gpu_phases as gpu
    import validation_sources, evaluation_manifest, evaluation_results

    settings_path = tmp_path / "settings.json"
    official_specs = tmp_path / "official-specs.json"
    official_specs.write_text(json.dumps({"evaluators": {}}))
    settings["official_specs"] = str(official_specs)
    settings_path.write_text(json.dumps(settings))
    out = tmp_path / "report"
    out.mkdir()
    selection = tmp_path / "selection.json"
    selection.write_text("{}")
    state = {"checkpoint_path": "/selected/model", "harness_path": "/selected/harness",
             "artifacts": {"directory": "/selected/artifacts", "manifest": []}}
    snapshot = out / "round_snapshot.json"
    snapshot.write_text(json.dumps({"round": 5, "task_state": state, "meta_state": {"version": 5},
                                   "source_round": str(selection), "chosen_component": "HARNESS"}))
    pipeline_path = tmp_path / "config" / "pipeline.json"
    pipeline_path.parent.mkdir()
    pipeline_path.write_text("{}")
    monkeypatch.setattr(pipeline, "load_config", lambda _: SimpleNamespace(output_root=tmp_path, round_protocol={"enabled": True}))
    monkeypatch.setattr(durable, "load_task", lambda value: SimpleNamespace(**value))
    monkeypatch.setattr(durable, "task_hash", lambda _: "selected-task")
    monkeypatch.setattr(storage, "checkpoint_manifest", lambda _: [])
    monkeypatch.setattr(harness, "load_manifest", lambda _: SimpleNamespace(identity="selected-harness"))
    monkeypatch.setattr(validation_sources, "verify", lambda *args: None)
    monkeypatch.setattr(gpu, "ensure_services", lambda *args: None)
    monkeypatch.setattr(gpu, "verify_services", lambda *args: {"mock": True})
    monkeypatch.setattr(evaluation_manifest, "select_specs", lambda *args, **kwargs: (manifest, selected_specs))
    def publish(*args, **kwargs):
        out = Path(args[4])
        (out / "task_results.jsonl").write_text("")
        (out / "task_metrics.json").write_text("{}")
        return {"overall": {"complete": True}}
    monkeypatch.setattr(evaluation_results, "publish", publish)
    return snapshot, pipeline_path, settings_path, out


def test_validate_rechecks_completed_tool_cache_through_evaluate_tool(tmp_path, monkeypatch):
    import tool_validation
    from validate import validate
    settings = {"benchmark_ids": ["bfcl_v3"], "tool_benchmarks": {"bfcl_v3": {"repository_root": "/unused"}},
                "official_specs": "/unused"}
    manifest = {"manifest_hash": "manifest", "tasks": [{"source": "bfcl_v3", "task_id": "bfcl_v3:n1",
                                                            "native_id": "n1"}]}
    snapshot, pipeline_path, settings_path, out = _patch_validate_scaffold(
        monkeypatch, tmp_path, settings, manifest, {})
    score = out / "scores" / "bfcl_v3" / "result.json"
    score.parent.mkdir(parents=True)
    score.write_text(json.dumps({"status": "completed", "state_hash": "selected-task", "expected": 1,
                                 "completed": 1, "task_results": []}))
    from common import sha
    (out / "complete.json").write_text(json.dumps({"status": "completed", "state_hash": "selected-task",
                                                     "result_files": {"scores/bfcl_v3/result.json": sha(score)}}))

    def reject_cache(*args, **kwargs):
        raise RuntimeError("Completed BFCL cache lacks trusted task attribution")
    monkeypatch.setattr(tool_validation, "evaluate_tool", reject_cache)
    with pytest.raises(RuntimeError, match="lacks trusted task attribution"):
        validate(snapshot, pipeline_path, settings_path, role="independent_validation", manifest_path="manifest")


def test_selected_spec_is_preserved_while_generate_and_score_use_normalized_runtime_spec(tmp_path, monkeypatch):
    from validate import validate
    import dynamic_predictions, rsiflow.task_meta.reporting as reporting, evaluation_results
    from common import sha
    data = tmp_path / "tasks.json"
    data.write_text("[]")
    ids = tmp_path / "ids.json"
    ids.write_text("[]")
    entry = tmp_path / "entry.py"
    entry.write_text("pass\n")
    original = {"benchmark": "HumanEval+", "repository": "repo", "commit": "a" * 40,
                "entrypoint": str(entry), "entrypoint_sha256": sha(entry), "python_executable": "python",
                "data_path": str(data), "data_sha256": sha(data), "task_ids_path": str(ids),
                "task_ids_sha256": sha(ids), "protocol": "test"}
    manifest = {"manifest_hash": "manifest", "tasks": []}
    settings = {"benchmark_ids": ["humaneval_plus"], "tool_benchmarks": {}, "official_specs": "/unused",
                "dynamic_predictions": True}
    snapshot, pipeline_path, settings_path, out = _patch_validate_scaffold(
        monkeypatch, tmp_path, settings, manifest, {"humaneval_plus": original})
    seen = {}

    def generate(config, frozen, spec_path, output):
        raw = json.loads(Path(spec_path).read_text())
        seen["persisted_spec"] = raw["evaluators"]["humaneval_plus"]
        seen["persisted_spec_sha256"] = sha(spec_path)
        prediction = Path(output) / "humaneval_plus.jsonl"
        prediction.parent.mkdir(parents=True, exist_ok=True)
        prediction.write_text(json.dumps({"task_id": "humaneval_plus:n1"}) + "\n")
    monkeypatch.setattr(dynamic_predictions, "generate_dynamic", generate)

    def evaluate(spec, rows, binding, output):
        seen["score_benchmark"] = spec.benchmark
        return {"status": "completed", "state_hash": binding["state_hash"], "expected": 0,
                "completed": 0, "task_results": []}
    monkeypatch.setattr(reporting, "evaluate_official", evaluate)
    monkeypatch.setattr(evaluation_results, "official_task_scores", lambda *args: {})
    result = validate(snapshot, pipeline_path, settings_path, role="independent_validation", manifest_path="manifest")
    assert result["status"] == "completed"
    assert seen["persisted_spec"] == original
    assert seen["score_benchmark"] == "HumanEval+"
    assert seen["persisted_spec_sha256"] == sha(out / "humaneval_plus.spec.json")
