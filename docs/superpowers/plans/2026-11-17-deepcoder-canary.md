# DeepCoder Single-Domain End-to-End Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and execute a one-round, sixteen-task DeepCoder/TACO function-only canary through the production Qwen Task and Astra Meta loop, including paired candidate evaluation, conditional positive-only SFT, persistent skills, snapshots, cleanup, and audit.

**Architecture:** Add a narrowly scoped `deepcoder_canary` mode to the existing production domain and progress configuration rather than writing a second evolution loop. Reuse the existing mini-release byte-preserving packer, PersistentMeta, controller tools, TaskAdapter, GPU phases, and SFT. Keep all current multidomain defaults unchanged and omit independent 744/694 only under the new `evaluation_schedule=none` mode.

**Tech Stack:** Python 3.11, pytest, Pydantic `PipelineConfig`, existing RSIFlow PersistentMeta/Codex native tools, Qwen3-4B on 4×A800, SQLite frozen rounds, LoRA SFT, GitLab, SSH/cpolar.

**Spec:** `docs/superpowers/specs/2026-11-17-deepcoder-canary-design.md`

## Global Constraints

- Select the first sixteen v5 B1 DeepCoder/TACO records with `strata.interface=function` in parent-manifest order before any canary model output; never reselect by score.
- Use one round, seed 42, one rollout per task, and the exact parent batch for the candidate.
- Only `deepcoder_canary` may use zero tool_use/searchqa quotas; its code quota must be exactly sixteen.
- `evaluation_schedule=none` must not change `final_only` or `after_each_committed_round` behavior.
- One attempt maximum: rejection ends as `completed_retained` after skills/context/snapshot; no unbounded retry.
- MODEL uses only natural successful parent trajectories; zero positives blocks SFT without synthetic labels or held-out data.
- Do not modify v5 source bytes/manifests, official evaluators, or existing benchmark assertions.
- Execute new real work only on the approved 4×A800 machine; no new SCO jobs and no formal five-round/694 run.
- Never write credentials to tracked files, command lines, public pages, or audit reports.

## Review Focus

- Zero quotas leaking into normal multidomain configs: Task 1 tests that only the exact canary scope is accepted.
- A rejected candidate causing another attempt: Task 2 tests `retry_rejected_from_round=2` and one-round completion after retain.
- Source data mutation or accidental non-function tasks: Task 3 tests bytes, order, interface, and denominator.
- A HARNESS/ARTIFACTS result being mislabeled as SFT success: Task 5 requires component-specific evidence and explicit not-run markers.
- Partial parent/candidate coverage or cleanup failure being reported as completion: Task 5 requires exact sixteen-task pairing and owned-service GPU release before any PASS.

---

### Task 1: Add Code-Only Canary Scope to Production Configuration

**Files:**
- Modify: `RSIFlow/runtime/rsiflow/task_meta/pipeline.py:37-141,319-430`
- Modify: `RSIFlow/task_adapter.py:45-95,191-216`
- Test: `RSIFlow/tests/test_deepcoder_canary.py`

**Interfaces:**
- Consumes: existing `PipelineConfig`, `adapter_factory`, `MultiDomainExecutor`.
- Produces: `active_domains(config: PipelineConfig) -> tuple[str, ...]`; `experiment_scope="deepcoder_canary"`; code-only `expected_domains=("code",)` in both pipeline and TaskAdapter.

- [ ] **Step 1: Write failing scope and executor tests**

Create `test_deepcoder_canary.py` with a helper that loads `runtime/configs/base_4b.json`, sets `output_root` to a temporary external directory, uses `training_schedule="round_disjoint"`, `search_dev_fraction=0`, `mode="full"`, `meta.run_mode="full"`, and four distinct replicas. Add:

```python
def test_deepcoder_canary_is_the_only_zero_quota_scope(canary_config):
    canary_config.experiment_scope = "deepcoder_canary"
    canary_config.window_quotas = {"tool_use": 0, "code": 16, "searchqa": 0}
    canary_config.probe_per_domain = dict(canary_config.window_quotas)
    assert active_domains(canary_config.checked()) == ("code",)

    canary_config.experiment_scope = "multidomain"
    with pytest.raises(ValueError, match="All three domains require positive fixed quotas"):
        canary_config.checked()
```

Also test wrong canary counts (`code=7`, missing domain keys, or a nonzero inactive domain) raise `ValueError`, and monkeypatch `MultiDomainExecutor` in `TaskAdapter._executor` to capture that `expected_domains == ("code",)`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  -m pytest -q tests/test_deepcoder_canary.py
```

Expected: collection or assertions fail because the scope literal and `active_domains` do not exist and zero quotas are rejected.

- [ ] **Step 3: Implement the minimal scope extension**

Add the literal and helper:

```python
def active_domains(config):
    if config.experiment_scope == "deepcoder_canary":
        return ("code",)
    if config.experiment_scope == "envscaler_validation":
        return ("tool_use",)
    return DOMAINS
```

In `checked()`, require both quota dictionaries to equal `{"tool_use": 0, "code": 16, "searchqa": 0}` for the canary; retain the existing positive-three-domain check for every other scope. Pass `experiment_scope`, `scope_train_limit`, and `scope_probe_limit` through `TaskAdapter._config()`. Replace hardcoded active/expected domain selections in pipeline and `TaskAdapter._executor` with `active_domains(config)`.

- [ ] **Step 4: Run focused and existing domain tests**

Run `pytest -q tests/test_deepcoder_canary.py tests/test_runtime_adapter_regressions.py`; expected all selected tests PASS, with no changed existing assertions.

- [ ] **Step 5: Commit the independently reviewable scope change**

```bash
git add RSIFlow/runtime/rsiflow/task_meta/pipeline.py RSIFlow/task_adapter.py \
  RSIFlow/tests/test_deepcoder_canary.py
git commit -m "feat: 增加 DeepCoder 单域 canary 配置"
```

### Task 2: Add No-Validation, Single-Attempt Canary Progress and Prompts

**Files:**
- Modify: `RSIFlow/experiment_progress.py:191-213,247-400`
- Modify: `RSIFlow/launch_meta.py:330-484,528-545`
- Test: `RSIFlow/tests/test_deepcoder_canary.py`

**Interfaces:**
- Consumes: `rounds=1`, `evaluation_schedule="none"`, `retry_rejected_from_round=2` from the launcher config.
- Produces: progress without validation milestones; canary prompt that permits terminal retain and forbids 744/A0 validation; workflow policy preserving the configured retry boundary.

- [ ] **Step 1: Add failing progress and prompt tests**

Construct a one-round policy with `evaluation_schedule="none"` and assert every stage excludes both `validation_snapshot` and `independent_validation`. Assert existing `final_only` still retains those milestones only in B5. Add:

```python
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
```

For `experiment_prompt()`, assert it contains `single-domain engineering canary`, `one attempt`, and terminal retain language; assert it does not instruct A0 evaluation, post-acceptance 744 evaluation, or another attempt after retain. Also assert `workflow_policy.json` receives `retry_rejected_from_round=2` from config while an omitted key defaults to 1.

- [ ] **Step 2: Run the new tests and verify RED**

Run the same pytest command; expected failures because `none` still requires validation, rejection resets B1, and the launcher hardcodes retry boundary 1.

- [ ] **Step 3: Implement minimal schedule and retry handling**

In `ExperimentProgress.rebuild()`, handle `none` separately by filtering validation milestones from every stage and setting a canary-specific mainline goal. Do not alter the existing `final_only` branch. In `launch_meta.py`, use:

```python
"retry_rejected_from_round": config.get("retry_rejected_from_round", 1)
```

Add a `none` prompt branch that replaces legacy A0/post-round validation instructions with no-validation canary instructions. State that one rejected attempt is completed as `completed_retained` after comparison, skills, context, and snapshot, without activation or reselection.

- [ ] **Step 4: Run progress/launch regression tests**

Run the new file plus `test_600_744_protocol.py` and `test_launch_meta.py`; expected all PASS, including unchanged final-only behavior.

- [ ] **Step 5: Commit**

```bash
git add RSIFlow/experiment_progress.py RSIFlow/launch_meta.py \
  RSIFlow/tests/test_deepcoder_canary.py
git commit -m "feat: 增加单轮无独立评估 canary 进度"
```

### Task 3: Build the Frozen Eight-Task Function Release and Machine Config

**Files:**
- Create: `reproduction/scripts/prepare_deepcoder_canary.py`
- Create: `reproduction/configs/train_4b_deepcoder_canary.json`
- Test: `RSIFlow/tests/test_deepcoder_canary.py`

**Interfaces:**
- Consumes: v5 `rounds_3000_5x600_v5/B1/manifest.json`, `training_side_smoke.materialize_mini_release`, v5 launcher config.
- Produces: `select_deepcoder_function(parent_manifest, count=16) -> dict`; deterministic `release/B1/manifest.json`, `release/tasks.sqlite`, `selection.json`, `READY.json`; canary launcher accepted by `TaskAdapter._config()`.

- [ ] **Step 1: Add failing deterministic release tests**

Use a synthetic parent manifest containing interleaved STDIO/function and non-DeepCoder records. Assert selection returns exactly the first sixteen function records in original order and never includes another source/interface. For the real v5 fixture used offline, hash all source packs before/after materialization and assert:

```python
assert receipt["task_count"] == 16
assert {t["source"] for t in release_tasks} == {"deepcoder_taco"}
assert {t["strata"]["interface"] for t in release_tasks} == {"function"}
assert all(check["bytes_identical"] for check in receipt["record_checks"])
assert receipt["source_assets_unchanged"] is True
assert hashes_after == hashes_before
```

Run the builder twice into different roots and assert identical task IDs, source content hashes, and selection rule; assert an existing/symlink output root is refused.

- [ ] **Step 2: Run builder tests and verify RED**

Expected failure because the script/config do not exist.

- [ ] **Step 3: Implement the builder and config**

Implement `select_deepcoder_function()` with no random scoring or model input:

```python
matches = [task for task in parent_manifest["tasks"]
           if task.get("source") == "deepcoder_taco"
           and (task.get("strata") or {}).get("interface") == "function"]
if len(matches) < count:
    raise ValueError("DeepCoder function frozen B1 has fewer than sixteen eligible tasks")
chosen = matches[:count]
return {"tasks": chosen, "records": chosen,
        "task_ids": [task["task_id"] for task in chosen]}
```

Reuse `materialize_mini_release()` for byte copies, SQLite, retrieval-link and source-hash checks. Freeze `selection.json` and `READY.json` with parent/source hashes, `smoke_only=true`, `feedback_role=train_evolution`, and `explicit_full_benchmark=false`.

Derive the machine config from `train_4b_v5.json`, changing only the canary fields: unique output/run names, the new frozen release, rounds/allocated/per-pass 1/16/16, source quotas `0/16/0/0`, `experiment_scope=deepcoder_canary`, `evaluation_schedule=none`, `retry_rejected_from_round=2`, `scope_train_limit=16`, `scope_probe_limit=16`, and empty Meta skill seed. Preserve Qwen checkpoint, Astra provider/model, BF16 SFT preset, sockets, and seed.

- [ ] **Step 4: Verify real-assets plan without model calls**

Run the builder against v5 B1, then `RSIFlow_4B/launch.py --config <canary-config> --check`. Assert sixteen tasks, code-only effective domains, no validation schedule, no API/model/service invocation, and source hashes unchanged.

- [ ] **Step 5: Commit**

```bash
git add reproduction/scripts/prepare_deepcoder_canary.py \
  reproduction/configs/train_4b_deepcoder_canary.json RSIFlow/tests/test_deepcoder_canary.py
git commit -m "feat: 冻结 DeepCoder function 八任务 canary"
```

### Task 4: Whole-Branch Offline Review and Execution Runbook

**Files:**
- Modify: `AGENTS.md`
- Modify: `reproduction/LEARNING.md`
- Modify: `reproduction/REPRODUCTION.md`
- Create: `reproduction/audit/reviews/deepcoder-canary-runbook.md`

**Interfaces:**
- Consumes: Tasks 1–3 code/config and exact command-line arguments.
- Produces: an audited, copyable dev4 runbook and explicit evidence boundaries.

- [ ] **Step 1: Run the full offline suite and diff checks**

Run all `RSIFlow/tests` with the normal rsiflow interpreter, `git diff --check`, and a secret scan of changed files. Record exact pass/skip counts. Any red test blocks real execution.

- [ ] **Step 2: Request independent spec and quality review**

Have independent reviewers inspect Tasks 1–3 against the design, especially quota leakage, terminal retain, immutable source bytes, prompt contradictions, and whether any path silently invokes 694. Main agent must read reports and verify cited code; idle/done is not PASS.

- [ ] **Step 3: Write the runbook and update maintained docs**

Document the current dev4 SSH endpoint by reference to the access report, not by embedding credentials. Include foreground/detached launch, log/output paths, 4-hour hard timeout, explicit bwrap, safe pre-existing Meta credential injection, status parsing, cleanup, and resume rules. State that no SFT is claimed unless Meta actually selects MODEL and natural positives exist.

- [ ] **Step 4: Commit and push the reviewed implementation**

```bash
git add AGENTS.md reproduction/LEARNING.md reproduction/REPRODUCTION.md \
  reproduction/audit/reviews/deepcoder-canary-runbook.md
git commit -m "docs: 记录 DeepCoder canary 执行与证据边界"
git push gitlab reproduction/4b-preflight
```

### Task 5: Execute the Real Canary on 4×A800 and Verify Every Artifact

**Files:**
- Create at runtime, Git-ignored: `reproduction/logs/deepcoder-canary-<UTC timestamp>/`
- Create: `reproduction/audit/reviews/deepcoder-canary-real-results.md`
- Modify: `reproduction/PROGRESS.md`, `web/RESULTS.md`, `web/OPERATIONS.md`, `web/data/reproduction.json`

**Interfaces:**
- Consumes: reviewed config/release and dev4 runbook.
- Produces: real parent/candidate trajectories, Meta decision and skill receipts, accept/retain result, optional MODEL update evidence, cleanup, and a bounded final verdict.

- [ ] **Step 1: Preflight the actual machine and launch one detached canary**

Confirm four idle A800s, matching pushed HEAD, pinned bwrap, free ports, new output/run directories, and secure Meta credential availability without printing secrets. Start the production launcher under `timeout --signal=TERM --kill-after=120s 14400s`; use detached remote execution and a local monitor. Do not start if another canary registry owns the GPUs.

- [ ] **Step 2: Verify parent and Meta evidence before accepting completion**

Require A0 bootstrap/snapshot, one parent rollout with exactly sixteen unique function tasks, completed official tests and persisted model calls, representative Meta evidence, decision.json, skill_use.json, and one attempt directory. Infrastructure errors or missing calls keep the canary incomplete.

- [ ] **Step 3: Verify the selected component and candidate path**

For HARNESS require localization/direction reports, complete bundle, validation and candidate state. For MODEL require natural positives, four ranks, one epoch, changed merged wsixteens, child reload and short generation. For ARTIFACTS require direct submissions and exact re-scoring. Record not-run evidence for the other two components.

- [ ] **Step 4: Verify paired outcome, terminal decision, memory, and cleanup**

Require candidate coverage of the same sixteen IDs, `compare_scores` and `compare_task_differences`, explicit accept/retain, active-state identity, skills/context/snapshot after the attempt, and owned cleanup with four-GPU release. A retained parent can be a completed canary; a partial comparison cannot.

- [ ] **Step 5: Publish real results and obtain final independent review**

Update the public page and audit with actual component, counts, scores, commit, failures, and remaining limits. Distinguish rollout/scoring success, candidate execution, SFT execution, and accept/retain. Have independent reviewers inspect real receipts; then commit/push documentation and merge to the reproduction mainline without touching protected ledgers.

### Task 6: Final Verification and Delivery

**Files:**
- Review all branch diffs and runtime result reports; no new production behavior.

- [ ] **Step 1: Re-run standard checks on the exact deliverable**

Run the full offline tests, website tests, Node syntax check, JSON parsing, `git diff --check`, and public-resource HTTP checks. Confirm no generated logs, checkpoints, credentials, or temporary diagnostics were staged.

- [ ] **Step 2: Check every user requirement against evidence**

Confirm one DeepCoder function dataset, sixteen predeclared tasks, real Qwen, real Astra, one candidate, same-batch comparison, conditional positive-only SFT, skills/snapshot, cleanup/recovery, audit, and dev4 execution. If a component was not selected, report that exact limitation rather than claiming its path ran.

- [ ] **Step 3: Deliver the result**

Report the absolute result/report paths, Git commit, actual parent/candidate metrics, selected component, accept/retain, SFT status, resource cleanup, public URL, and the explicit statement that this is not five-round or author-result reproduction.
