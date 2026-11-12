# RSI-Flow Bubblewrap Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed `linux_bwrap_seccomp_v1` backend for candidate code, native benchmarks, and persistent Codex Meta, then prove the real control flow on SCO without starting A0+B1–B5.

**Architecture:** A shared `BubblewrapBackend` validates pinned assets and constructs minimal mount/namespace commands. A trusted inner launcher applies credentials, capabilities, rlimits, and profile-specific seccomp after bubblewrap finishes setup; `LinuxSandbox`, `tool_sandbox.run_native`, and `CodexAppServer` adapt their existing scoring/RPC protocols to that backend. Persistent Meta keeps the provider credential in a host `ResponsesTransport`, while Codex and the credential-free bridge run inside bubblewrap and durable tool jobs remain host-side.

**Tech Stack:** Python 3.10/3.11, bubblewrap 0.6.1, Linux user/PID/network/IPC/UTS namespaces, libseccomp, pytest, pinned Codex `0.153.4`, official EvalPlus and BFCL runners, SCO ACP.

**Spec:** `docs/superpowers/specs/2026-11-12-bwrap-isolation-design.md`

## Global Constraints

- Backend ID is exactly `linux_bwrap_seccomp_v1`; production assets live under Git-ignored `assets/isolation/`, and `reproduction/preflight/` is diagnostic only.
- Pin bubblewrap `0.6.1`, SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`; pin Codex SHA256 `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da`.
- Explicitly select bwrap with `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`. Never fall back to host execution, Landlock after a bwrap failure, or `danger-full-access`.
- Use `--unshare-all --new-session --die-with-parent`; do not mount real procfs. Apply seccomp only in the inner launcher, never in host `preexec_fn` before bubblewrap setup.
- Deny broad writable `/`, `/data`, `/home`, `/root`, repository-root, credential, checkpoint, and raw-data mounts. Reject symlink escapes and paths outside dedicated writable roots.
- Keep the real `OPENAI_API_KEY` only in host `ResponsesTransport`; the sandbox receives only `RSIFLOW_RELAY_KEY=pipe-only`. Provider remains `OpenAI`, model `gpt-6-astra`, upstream `http://token.acerobotics.com/v1`, with no fallback.
- Preserve candidate JSON-lines RPC, native benchmark RPC file descriptors, official comparator behavior, durable `codex_home`, thread IDs, and host-side job adoption. Infrastructure errors must not become model zero scores.
- Do not change the 3000/744 release, five-round policy, Qwen3-4B checkpoint, SFT settings, or 694 scoring protocol. ACE 50 remains pending. Do not start A0+B1–B5, SFT, final evaluation, or an 8B run in this plan.
- Run commands through `python3 reproduction/scripts/audit_exec.py --audit-root reproduction/audit --phase <task-and-step> --cwd /data/wuxuecheng/Hierarchical_level/RSI-Flow -- <command>`. Record key results with `record_result.py`; never log secrets, request Authorization headers, or private file contents.
- Commit each completed task locally on `reproduction/4b-main8`; do not push. Record the commit with `record_operation.py --git-commit <full-hash>` and the relevant test evidence.
- SCO smoke jobs use workspace/AEC2 `sensebot-a100-a800-liujianbo`, image `registry.cn-sh-01.sensecore.cn/ccr-sensebot-ccr/starvla7:lihao-dev-20260426172526`, worker `N3lS.Ii.I60.8`, one node, and volume `019fdaf3-33db-7bbf-992b-bc2f64d0b2fd:/data`. Submit sequentially at least five seconds apart, use bounded commands rather than `sleep inf`, and verify terminal state/resource release. GPU smoke does not load Qwen in this plan.
- Before implementation, invoke `superpowers:test-driven-development`. Before claiming completion, invoke `superpowers:verification-before-completion`. A fresh reviewer must close all Important/Critical findings before Task 3 is marked done.

## Review Focus

- A trusted mount can be replaced by a symlink after validation; expect open-time identity checks or a fail-closed error. Task 1 pins parent-component rejection and source identity, and Task 2/4 SCO probes exercise the real launch.
- Inherited RPC descriptors can accidentally expose unrelated host descriptors; expect only the two declared native RPC descriptors plus stdio. Task 1 tests descriptor policy and Task 4 verifies two real RPC calls.
- Provider variables can leak through copied environments, config text, child commands, or logs; expect only a dummy relay value in the sandbox. Tasks 5 and 6 test process environments and real Codex descendants.
- A missing `/proc` can be mistaken for a benchmark/model failure; expect an infrastructure error with the official runner log preserved. Task 4 tests this classification and never mounts host procfs as a fix.
- Restart can duplicate a still-running rollout while appearing to resume correctly; expect the same `thread_id`, same durable job set, and no new bootstrap. Task 6 keeps a real short host job alive across restart and reconciles it before another turn.

---

### Task 1: Pinned Assets, Mount Policy, Inner Launcher, and Shared Backend

**Files:**
- Create: `RSIFlow/runtime/rsiflow/task_meta/bwrap_backend.py`
- Create: `RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py`
- Create: `RSIFlow/configs/bwrap_isolation.json`
- Create: `reproduction/scripts/install_bwrap.py`
- Modify: `.gitignore`
- Test: `RSIFlow/tests/test_bwrap_backend.py`

**Interfaces:**
- Consumes: existing `SandboxUnavailable` from `rsiflow.task_meta.sandbox`; diagnostic bwrap at `reproduction/preflight/isolation/bin/bwrap` only as installer input.
- Produces: `BwrapLimits`, `BwrapMount`, `BwrapRequest`, and `BubblewrapBackend.validate() -> dict`, `BubblewrapBackend.command(BwrapRequest) -> list[str]`; inner CLI `bwrap_launcher.py --profile NAME --limits-json JSON --env-json JSON --cwd ABSOLUTE --preserve-fds CSV -- COMMAND...`.

- [ ] **Step 1: Write failing asset and fail-closed tests**

```python
def test_bwrap_asset_is_pinned_and_diagnostic_path_is_not_production(tmp_path):
    manifest = json.loads((PROJECT / "RSIFlow/configs/bwrap_isolation.json").read_text())
    assert manifest["backend"] == "linux_bwrap_seccomp_v1"
    assert manifest["binary"] == {
        "path": "assets/isolation/bin/bwrap",
        "version": "0.6.1",
        "sha256": "d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca",
    }
    with pytest.raises(SandboxUnavailable, match="bwrap"):
        BubblewrapBackend(PROJECT, manifest_path=tmp_path / "missing.json",
                          allowed_write_roots=(tmp_path,)).validate()
```

Add cases for a wrong binary hash, missing namespace support, non-regular launcher, absolute/`..` targets, writable `/data`, repository-root writes, symlink parents, output outside `allowed_write_roots`, and unknown profile names. Assert every failure raises `SandboxUnavailable` or `ValueError`, never a reduced command.

- [ ] **Step 2: Run the tests and observe the missing backend failure**

Run:

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python3 -m pytest -q \
  RSIFlow/tests/test_bwrap_backend.py
```

Expected: FAIL because `bwrap_backend.py` and the tracked manifest do not exist.

- [ ] **Step 3: Implement the tracked profile manifest and installer**

`RSIFlow/configs/bwrap_isolation.json` must contain the exact binary identity above and these profile names/settings:

```json
{
  "schema_version": 1,
  "backend": "linux_bwrap_seccomp_v1",
  "binary": {
    "path": "assets/isolation/bin/bwrap",
    "version": "0.6.1",
    "sha256": "d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca"
  },
  "profiles": {
    "candidate": {"seccomp": "candidate", "allow_threads": false, "allow_processes": false},
    "candidate_session": {"seccomp": "candidate", "allow_threads": false, "allow_processes": false},
    "native_benchmark": {"seccomp": "native", "allow_threads": true, "allow_processes": false},
    "persistent_meta": {"seccomp": "meta", "allow_threads": true, "allow_processes": true}
  }
}
```

Add `assets/isolation/` to `.gitignore`. The installer accepts `--source` and `--root`, copies with `os.open(..., O_NOFOLLOW)` plus an atomic rename, checks the regular-file SHA256 and `bwrap --version`, and writes `install-receipt.json` containing the source/destination hashes and version. Install with:

```bash
python3 reproduction/scripts/install_bwrap.py \
  --source reproduction/preflight/isolation/bin/bwrap \
  --root assets/isolation
```

- [ ] **Step 4: Implement request validation and command construction**

Use these concrete value types:

```python
@dataclass(frozen=True)
class BwrapLimits:
    wall_seconds: float
    cpu_seconds: int
    memory_bytes: int
    output_bytes: int
    file_bytes: int
    processes: int
    file_descriptors: int = 32

@dataclass(frozen=True)
class BwrapMount:
    source: Path
    target: str
    mode: str = "ro"  # ro, rw, or dev

@dataclass(frozen=True)
class BwrapRequest:
    profile: str
    argv: tuple[str, ...]
    cwd: str
    env: dict[str, str]
    limits: BwrapLimits
    mounts: tuple[BwrapMount, ...]
    preserve_fds: tuple[int, ...] = ()
```

`BubblewrapBackend.command()` begins with the pinned binary and fixed flags, emits `--ro-bind`, `--bind`, or single-device `--dev-bind`, mounts the trusted launcher read-only at `/opt/rsiflow/bwrap_launcher.py`, and ends with `/usr/bin/python3 -I -B` plus the inner CLI. It must not emit `--proc`, `--dev /dev`, `--bind / /`, `--ro-bind-try`, or a network-sharing option. Include launcher/profile/limit source hashes in `validate()` evidence.

- [ ] **Step 5: Implement inner credentials, limits, descriptor policy, and seccomp**

The inner launcher parses only fixed typed arguments, rejects unknown profile names and non-absolute cwd, sets `umask(0o077)`, drops all capabilities and sets `PR_SET_NO_NEW_PRIVS`, then applies CPU/AS/FSIZE/NPROC/NOFILE/CORE limits. It closes every inherited descriptor above stderr except the two explicitly declared native RPC descriptors. It loads `libseccomp.so.2` immediately before `os.execve()`.

Reuse the denied syscall names from `sandbox._restrict_syscalls`; candidate denies network/process creation, native permits only `CLONE_THREAD` and atomic rename, and Meta permits local process/thread creation and loopback/Unix sockets while retaining mount/setns/ptrace/keyring/module/bpf denials. No profile may remove mount, setns, ptrace, or credential-change denials.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python3 -m pytest -q \
  RSIFlow/tests/test_bwrap_backend.py RSIFlow/tests/test_isolation_runtime.py
```

Expected: all selected tests PASS. Review `git diff --check`, then commit `feat: add pinned bubblewrap sandbox backend` with only Task 1 files. Record test logs and the commit in the audit ledger.

---

### Task 2: Candidate and Persistent Session Integration

**Files:**
- Modify: `RSIFlow/runtime/rsiflow/task_meta/sandbox.py:68`
- Create: `RSIFlow/runtime/rsiflow/task_meta/bwrap_candidate.py`
- Create: `reproduction/scripts/candidate_bwrap_smoke.py`
- Test: `RSIFlow/tests/test_bwrap_candidate.py`
- Test: `RSIFlow/tests/test_bwrap_backend.py`

**Interfaces:**
- Consumes: `BubblewrapBackend.command(BwrapRequest)` and `BwrapLimits` from Task 1.
- Produces: `LinuxSandbox(..., backend: str | None = None)`; `backend=None` reads `RSIFLOW_SANDBOX_BACKEND`, defaulting to legacy Landlock only when the variable is absent. `SandboxResult.backend` always reports the actual backend. Existing `run()` and `session()` signatures remain unchanged.

- [ ] **Step 1: Write failing selection and result-identity tests**

```python
def test_bwrap_is_explicit_and_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    monkeypatch.setattr("rsiflow.task_meta.bwrap_candidate.BubblewrapBackend.validate",
                        lambda self: (_ for _ in ()).throw(SandboxUnavailable("namespaces unavailable")))
    runner = LinuxSandbox(scratch_root=tmp_path)
    with pytest.raises(SandboxUnavailable, match="namespaces unavailable"):
        runner.run("print('must-not-run-on-host')")
    assert not (tmp_path / "candidate.py").exists()
```

Add tests that an unknown backend raises `SandboxUnavailable`, bwrap `run()` returns `backend="linux_bwrap_seccomp_v1"`, Landlock retains its legacy identity, extra-file basename checks remain unchanged, and candidate code receives no provider key even when the parent environment contains one.

- [ ] **Step 2: Verify red on the Landlock-only implementation**

Run:

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python3 -m pytest -q \
  RSIFlow/tests/test_bwrap_candidate.py
```

Expected: FAIL because `LinuxSandbox` has no backend selector and results retain the Landlock default.

- [ ] **Step 3: Implement candidate bwrap launch without changing scoring logic**

`bwrap_candidate.py` converts `SandboxLimits` to `BwrapLimits`, creates the same dedicated scratch and extra files as the legacy runner, constructs only trusted Python/stdlib/dynamic-library read mounts, and marks scratch as the sole candidate writable mount. It invokes the selected Python with `-s -B candidate.py` or `-s -B -u candidate.py`, using the same stdin/stdout/stderr protocol as the existing implementation.

In `sandbox.py`, branch at process construction rather than changing adapters. Keep timeout process-group kill, bounded output, `output_truncated`, session cleanup, and infrastructure exception classification. Do not call `_restrict_filesystem`, `_restrict_syscalls`, or a host `preexec_fn` in bwrap mode.

- [ ] **Step 4: Add timeout, output, session, and environment regression tests**

Use real short Python programs to assert:

```python
assert runner.run("print('ok')").stdout == "ok\n"
assert runner.run("import time; time.sleep(5)").timed_out is True
assert runner.run("print('x' * 10000)").output_truncated is True
with runner.session("import json\nfor line in __import__('sys').stdin:\n print(json.dumps({'echo': json.loads(line)}), flush=True)") as session:
    assert session.request({"value": 3}) == {"echo": {"value": 3}}
```

Run these through the audited SCO candidate smoke if the local kernel lacks namespaces; local unit tests must mock only command construction, never claim OS isolation success.

- [ ] **Step 5: Implement the SCO candidate negative matrix and run it**

`candidate_bwrap_smoke.py --output <path>` uses `LinuxSandbox` under the explicit bwrap environment and writes one JSON report containing separate `functional`, `negative`, `timeout`, and `session` sections. Negative checks assert `/data`, `/root`, checkpoint and a planted private marker are absent; runtime writes fail; scratch writes succeed; external connect, mount, setns, ptrace, fork/clone, and credential changes fail. It also plants a unique parent-only key value and checks both candidate environment and `os.environ` output for its absence.

Run the script in one bounded SCO job and save stdout/stderr under `reproduction/logs/`. Expected: every named check is true, normal output/session RPC works, timeout cleans the process group, and no infrastructure error is reported as a candidate wrong answer.

- [ ] **Step 6: Run candidate/full affected tests, record evidence, and commit**

Run `test_bwrap_backend.py`, `test_bwrap_candidate.py`, `test_task_adapter.py`, and `test_release_integration.py`. Commit `feat: integrate bubblewrap candidate sandbox`; record the SCO job ID, terminal state, report SHA256, and test evidence with `record_result.py` and `record_operation.py`.

---

### Task 3: Official EvalPlus Minimum Smoke

**Files:**
- Create: `reproduction/scripts/evalplus_bwrap_smoke.py`
- Modify: `RSIFlow/runtime/rsiflow/task_meta/evalplus_isolated.py:239`
- Test: `RSIFlow/tests/test_bwrap_candidate.py`
- Create: `RSIFlow/tests/test_evalplus_isolated.py`

**Interfaces:**
- Consumes: explicit `LinuxSandbox` selection from Task 2 and pinned EvalPlus `26d6d00bb1fd0fa37f39c99d5290da67891d1c5e` from `reproduction/configs/report-evaluators-v4.json`.
- Produces: `evaluate_evalplus_isolated()` returns the actual `worker_isolation` value; the smoke report contains official base/plus status for one passing canonical solution and one deliberately wrong solution, with infrastructure errors kept separate.

- [ ] **Step 1: Write a failing backend-identity test**

```python
def test_evalplus_receipt_reports_selected_bwrap_backend(monkeypatch):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    assert LinuxSandbox().backend == "linux_bwrap_seccomp_v1"
```

Also assert the smoke builder never edits official expected outputs, uses task rows and canonical solutions directly from the pinned data, and treats `EvalPlusInfrastructureError` as `status="infrastructure_error"` rather than `pass@1=0`.

- [ ] **Step 2: Run focused tests red**

Run the new tests with `PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow"`; expect the current hard-coded `worker_isolation="linux_landlock_seccomp"` assertion to fail.

- [ ] **Step 3: Implement the one-task official smoke**

The script reads the HumanEval+ evaluator spec, verifies `load_pinned_evalplus()` commit/cleanliness, selects the first task row deterministically, and creates two independent one-row temporary data/prediction pairs. The passing prediction is `task["prompt"] + task["canonical_solution"]`; the failing prediction replaces the canonical body with `return None` while preserving a valid function signature. Call `evaluate_evalplus_isolated()` for each pair and assert one official pass and one official fail. Do not reuse a mocked comparator.

Change only the final worker identity to `LinuxSandbox().backend`; leave official status mapping, native timers, expected values, and denominator checks intact.

- [ ] **Step 4: Run the real EvalPlus smoke on SCO**

Use the shared `rsiflow` Python, explicit bwrap environment, and a fresh output directory under `reproduction/logs/evalplus-bwrap-smoke/`. Expected: pinned source hash matches, canonical solution passes official base/plus checks, wrong solution fails normally, both receipts identify `linux_bwrap_seccomp_v1`, and no sandbox/RPC failure is categorized as a model failure.

- [ ] **Step 5: Run tests, record official result hashes, and commit**

Run all EvalPlus and candidate tests. Commit `test: verify EvalPlus through bubblewrap`; record the SCO job and official `official_results.json` hashes. Do not report this one-task smoke as a HumanEval+ benchmark score.

---

### Task 4: Native Benchmark Profile and BFCL `check_native`

**Files:**
- Modify: `RSIFlow/tool_sandbox.py:45`
- Modify: `RSIFlow/tool_validation.py:41`
- Modify: `reproduction/configs/validation_744.json`
- Create: `RSIFlow/tests/test_bwrap_native.py`
- Modify: `RSIFlow/tests/test_tool_validation_preflight.py`

**Interfaces:**
- Consumes: Task 1 `native_benchmark` profile and inner thread-only seccomp; existing `serve_rpc(incoming, outgoing, handler, errors, workers=4)`.
- Produces: `run_native(..., backend: str | None = None)` with the existing positional signature compatible; native profile preserves exactly `RSI_RPC_WRITE_FD` and `RSI_RPC_READ_FD`, permits threads and atomic rename, and raises infrastructure `RuntimeError` on launch/RPC failure.

- [ ] **Step 1: Write failing native command and RPC-descriptor tests**

Replace AST-source rewriting expectations with explicit backend assertions:

```python
def test_native_bwrap_preserves_only_declared_rpc_fds(monkeypatch):
    monkeypatch.setenv("RSIFLOW_SANDBOX_BACKEND", "linux_bwrap_seccomp_v1")
    request = make_native_request(work=WORK, output=OUTPUT, command=COMMAND)
    assert request.profile == "native_benchmark"
    assert set(request.preserve_fds) == {REQUEST_WRITE_FD, RESPONSE_READ_FD}
    assert request.limits.file_descriptors == 256
    assert request.limits.processes == 128
    assert all(mount.mode != "rw" or mount.source == OUTPUT for mount in request.mounts)
```

Add tests for atomic rename permission, thread-only clone rules, `/proc` absence, dummy API values rather than a real key, missing `/proc` official-runner errors remaining infrastructure errors, and timeout killing the complete process group.

- [ ] **Step 2: Verify red on the AST/Landlock implementation**

Run `RSIFlow/tests/test_bwrap_native.py`; expect failure because `run_native` probes Landlock and dynamically rewrites `_restrict_syscalls`/`_restrict_filesystem` source.

- [ ] **Step 3: Implement native bwrap launch**

Keep `serve_rpc` unchanged. In bwrap mode, build trusted read mounts from the selected official Python environment, `sys.prefix`, `sys.base_prefix`, copied `native_work`, `official_tool_entry.py`, and existing system library/device allowlist. Only the dedicated output tree is writable. Pass the two RPC descriptors through `Popen(pass_fds=...)` and `BwrapRequest.preserve_fds`; retain the existing pipe protocol, log, timeout, error collection, and cleanup. Remove AST rewriting from the bwrap path rather than weakening either seccomp profile.

- [ ] **Step 4: Prove the pinned BFCL Git source before runner smoke**

Run:

```bash
BFCL_ROOT=/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/evaluators/bfcl-ea13468/gorilla-ea13468e4423454d0c213704fb87cf7cb3990433/berkeley-function-call-leaderboard
actual=$(git -C "$BFCL_ROOT" rev-parse HEAD)
test "$actual" = ea13468e4423454d0c213704fb87cf7cb3990433
```

If the current archive has no `.git`, restore a separate official checkout at `assets/evaluators/bfcl-official/berkeley-function-call-leaderboard`, checkout exactly `ea13468e4423454d0c213704fb87cf7cb3990433`, verify `git status --porcelain --untracked-files=no` is empty, and update only `tool_benchmarks.bfcl_v3.repository_root` in the machine config. Never replace V3 data with V4 or infer provenance from a ZIP filename.

- [ ] **Step 5: Run official BFCL `check_native` on SCO**

Invoke:

```python
config = json.loads(Path("reproduction/configs/validation_744.json").read_text())
check_native("bfcl_v3", config["tool_benchmarks"]["bfcl_v3"],
             Path("reproduction/logs/bfcl-bwrap-check"))
```

Expected: official imports/registration complete, exactly two controlled CPU RPC calls are observed, `test_override_cpu_only` is returned through the existing bridge, no real provider key enters the child, and the SCO job exits successfully. A runner `/proc` import failure stops this task as infrastructure failure; do not mount host procfs or relabel it as a model failure.

- [ ] **Step 6: Run native/preflight tests, record, and commit**

Run `test_bwrap_native.py`, `test_tool_validation_preflight.py`, and tool validation tests. Commit `feat: run native benchmarks in bubblewrap`; record pinned Git HEAD, two-RPC evidence, logs, SCO terminal state, and output SHA256.

---

### Task 5: Persistent Meta Sandbox and Credential-Free Relay

**Files:**
- Create: `RSIFlow/persistent_meta_sandbox.py`
- Modify: `RSIFlow/persistent_meta.py:39`
- Modify: `RSIFlow/launch_meta.py:455`
- Modify: `reproduction/configs/codex_host.toml`
- Modify: `reproduction/configs/base_4b.json:55`
- Create: `RSIFlow/tests/test_persistent_meta_sandbox.py`
- Modify: `RSIFlow/tests/test_persistent_meta.py`
- Modify: `RSIFlow/tests/test_launch_meta.py`

**Interfaces:**
- Consumes: Task 1 `persistent_meta` profile, existing `IsolationRuntime` static tree validation, `MetaBackendConfig(**base["meta"])`, `ResponsesTransport`, and `bridge.py`.
- Produces: `PersistentMetaSandbox.start() -> None`, `.command(argv, cwd=None) -> list[str]`, `.spawn(argv, cwd, stdin, stdout, stderr) -> subprocess.Popen`, `.close() -> None`, `.identity() -> dict`; `CodexAppServer(..., process_launcher=None)` accepts the controlled spawner without changing stdio JSON-RPC or host `NativeExperimentTools` ownership.

- [ ] **Step 1: Write failing persistence, mount, and secret tests**

```python
def test_meta_sandbox_preserves_codex_home_and_hides_real_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "parent-only-secret")
    sandbox = make_meta_sandbox(tmp_path)
    (sandbox.codex_home / "session.jsonl").write_text("persistent-thread-state\n")
    first = sandbox.command(["/codex", "app-server", "--listen", "stdio://"])
    sandbox.close()
    second = make_meta_sandbox(tmp_path).command(["/codex", "app-server", "--listen", "stdio://"])
    assert (sandbox.codex_home / "session.jsonl").read_text() == "persistent-thread-state\n"
    assert "parent-only-secret" not in " ".join(first + second)
    assert "danger-full-access" not in " ".join(first + second)
```

Assert `.git`, `assets/private`, `assets/models`, raw data, and unrelated host directories are absent from mounts; project source, run directory, central Meta logs, `codex_home`, and declared experiment outputs are the only writable mounts. Static runtime hash changes must raise before spawn.

- [ ] **Step 2: Verify red on direct-host Codex startup**

Run the focused tests; expect failure because `CodexAppServer` directly inherits `os.environ` and starts the executable without a controlled launcher.

- [ ] **Step 3: Implement persistent static runtime and bwrap mounts**

Reuse `IsolationRuntime.prepare()/validate()` only for the read-only static tree, including its fixed `/proc/self/exe -> /codex` link. Do not call its Landlock `command()/probe()`, and never call `_clear_mutable()` on the run-persistent `codex_home`.

`PersistentMetaSandbox.start()` validates pinned Codex/catalog/runtime, creates a short Unix relay socket, and starts host `ResponsesTransport` with `MetaBackendConfig` and the real secret. `spawn()` wraps `["/usr/bin/python3", "/bridge.py", "/codex", "app-server", "--listen", "stdio://"]` in bwrap, uses minimal env with dummy relay key, and keeps stdio connected to the host controller. `close()` terminates the process group as needed, stops transport, and preserves provider receipts and `codex_home`.

- [ ] **Step 4: Route Codex provider through the private bridge**

In `reproduction/configs/codex_host.toml`, keep model/provider identity but set:

```toml
[model_providers.OpenAI]
name = "OpenAI"
base_url = "http://127.0.0.1:18443/api/v1"
wire_api = "responses"
env_key = "RSIFLOW_RELAY_KEY"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
stream_idle_timeout_ms = 600000
```

Keep `sandbox_mode="workspace-write"` and legacy Landlock compatibility inside the outer bwrap; do not use `danger-full-access`. Disable Codex workspace network access because the outer namespace permits only private loopback. Host `ResponsesTransport` remains configured for the real OpenAI upstream in `base_4b.json`; set `execution_location="local_chroot"` only as the existing config-family value while identity evidence reports the actual `linux_bwrap_seccomp_v1` backend.

- [ ] **Step 5: Inject the launcher without moving dynamic tools into the sandbox**

`CodexAppServer` uses `process_launcher.spawn()` when provided and otherwise retains its legacy path for unit compatibility. In bwrap mode its environment is constructed by the sandbox, not copied from the host. `PersistentMeta` creates the sandbox only when explicitly configured, passes it to `CodexAppServer`, and closes it in `finally`. `NativeExperimentTools`, `ExperimentJobs`, and `SubprocessToolExecutor` remain host-side and continue stripping provider credentials.

`native_thread_parameters()` must retain configured model/provider and `workspace-write`, set `sandbox_workspace_write.network_access=False`, and never enable multi-agent or arbitrary host sandbox access.

- [ ] **Step 6: Run offline RPC/config tests and commit**

Run `test_persistent_meta_sandbox.py`, `test_persistent_meta.py`, `test_launch_meta.py`, `test_custom_provider.py`, and `test_isolation_runtime.py`. Verify fake-Codex multiple turns and tool errors still use one PID, `prepare_codex_home()` preserves existing state, and no test requires a real key. Commit `feat: isolate persistent Codex with credential-free relay` and record evidence.

---

### Task 6: Real Codex Dynamic Tool and Restart/Resume Smoke on SCO

**Files:**
- Create: `reproduction/scripts/persistent_meta_bwrap_smoke.py`
- Create: `reproduction/configs/meta_bwrap_smoke.json`
- Test: `RSIFlow/tests/test_persistent_meta_sandbox.py`

**Interfaces:**
- Consumes: Task 5 `PersistentMetaSandbox`, `CodexAppServer`, `dynamic_tools()`, `NativeExperimentTools`, real `SubprocessToolExecutor`, and host `ResponsesTransport`.
- Produces: a sanitized report binding backend/runtime identity, real returned model, dynamic-tool receipt, first/second `thread_id`, durable job IDs before/after restart, provider evidence hashes, and process/resource cleanup.

- [ ] **Step 1: Add failing restart invariants to the smoke test harness**

Test report reconciliation with a fake event ledger only for report-parser unit logic:

```python
before = {"thread_id": "thread-A", "jobs": {"job-A": "running"}}
after = {"thread_id": "thread-A", "jobs": {"job-A": "running"}}
assert reconcile_resume(before, after) == {
    "same_thread": True, "duplicate_jobs": [], "bootstrap_repeated": False,
}
```

Also test that a changed thread, new job, second bootstrap receipt, missing dynamic-tool receipt, or provider model mismatch makes the report fail. This does not substitute for the real SCO run.

- [ ] **Step 2: Implement the bounded real smoke**

The script creates a fresh run directory and public fixture JSON, starts the real sandbox/app-server, and issues `thread/start` with the existing `dynamic_tools()`. Turn 1 instructs the model to call `experiment` once with `operation="read_json"` on that fixture and then end; assert a host turn receipt contains the fixture value and the provider returned exactly `gpt-6-astra`.

Turn 2 starts one harmless durable host job through `experiment(operation="run_command", argv=["/bin/sh", "-c", "sleep 20"])`, records its single job ID and PID, and closes only the app-server while the host job remains alive. Reconstruct all controller objects, call `thread/resume` with the saved ID and `excludeTurns=True`, reconcile `ExperimentJobs.all()` before any new turn, then ask Codex to call `job_status` and finish. Assert the same thread, one original job, no bootstrap receipt, and no repeated job PID. Wait for the short job and verify its result.

- [ ] **Step 3: Run the real SCO smoke with private env injection**

Source `assets/private/credentials.sh` only inside the SCO shell without echoing it; do not place its contents in `--env`, command logs, tracked config, or the report. Use a bounded timeout and fresh smoke output. Expected: native dynamic tool call and tool result appear in app-server events, provider receipts complete, resume returns the original thread, and the durable job set remains unchanged. A direct Responses success from an earlier probe is not accepted as a substitute.

- [ ] **Step 4: Verify sandbox and descendant key hygiene**

From a no-side-effect Codex native shell turn, print only boolean checks for whether `OPENAI_API_KEY` is present and whether the planted parent-only marker is present; do not print environment values. Assert the real provider key exists only in host transport, sandbox config contains `RSIFLOW_RELAY_KEY` but no real key, host dynamic-tool processes strip it, and public events/logs contain no secret after the existing sanitizer runs.

- [ ] **Step 5: Verify terminal state and cleanup, then commit**

Check the app-server, bridge, relay, and smoke child processes are gone; the short durable job has a terminal receipt; `codex_home` and provider receipts remain; the SCO job is terminal. Commit `test: verify persistent Codex tools and resume in bubblewrap`; record model identity, thread ID, job ID, evidence hashes, and cleanup in key results. Do not expose raw prompts/responses on the website.

---

### Task 7: Full Regression, Independent Security Review, and Audited Publication

**Files:**
- Modify: `AGENTS.md`
- Modify: `reproduction/PROGRESS.md`
- Modify: `reproduction/REPRODUCTION.md`
- Modify: `reproduction/audit/daily/2026-11-12.md`
- Modify: `web/RESULTS.md`
- Modify: `web/OPERATIONS.md`
- Modify: `web/REPRODUCTION.md`
- Modify: `web/data/reproduction.json`
- Regenerate: `web/data/process.json`

**Interfaces:**
- Consumes: all prior task commits, local pytest output, SCO terminal evidence, candidate/EvalPlus/BFCL/Codex reports, and reviewer findings.
- Produces: an evidence-backed Task 3 completion or a precisely named blocker; public pages contain only sanitized status and no claim that five rounds or benchmark scoring ran.

- [ ] **Step 1: Run the complete upstream test suite**

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
```

Expected: all tests pass, except that a pre-existing failure may be reported separately only if it is demonstrably unrelated and still fails on the parent commit. Do not delete or rename the old Landlock test to make it green; port it to explicit backend coverage or preserve the failure as a blocker.

- [ ] **Step 2: Run static and website verification**

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --check
python3 reproduction/scripts/build_status.py \
  --audit-root reproduction/audit \
  --output web/data/process.json
```

Run the existing secret scan before publication. Confirm `assets/isolation/`, credentials, raw provider requests, private trajectories, and large outputs remain ignored/unlinked.

- [ ] **Step 3: Obtain a fresh independent security review**

Dispatch a reviewer who did not implement the branch. Provide the spec, this plan, the complete diff, and real evidence. Require review of mount breadth/symlinks, namespace and seccomp staging, RPC descriptor inheritance, provider key hygiene, native `/proc` failure classification, timeout/process-group cleanup, persistent home/thread/job recovery, and any mocks or skipped tests presented as real evidence.

File every Important/Critical finding as a blocking change request. Return to the owning task for a failing regression test and minimal fix, then rerun its real SCO smoke and affected full tests. Do not mark Task 3 complete with open Important/Critical findings.

- [ ] **Step 4: Update project guidance and evidence-backed status**

Document explicit backend selection, asset installation, three profiles, keyless relay, failure classification, and smoke commands in `AGENTS.md` and `reproduction/REPRODUCTION.md`. Update current progress from “Landlock hard blocker” to either “bwrap validated by named evidence” or the exact remaining failure. Keep the engineering difference from upstream Landlock explicit.

Update public Markdown/JSON with test counts, SCO job IDs, and sanitized evidence. Candidate/EvalPlus/BFCL/Codex smoke are infrastructure validation, not model benchmark results; five rounds, SFT, 694 evaluation, ACE 50, and 8B remain untouched.

- [ ] **Step 5: Record final verification and commit**

Record full pytest, website tests, real smoke report hashes, reviewer closure, SCO terminal states, and resource cleanup. Commit `docs: publish validated bubblewrap isolation evidence`. Rebuild `web/data/process.json` after the audit entry if needed and include that generated update in the commit. Confirm `git status --short` contains only pre-existing audit churn explicitly explained in the handoff.

- [ ] **Step 6: Stop at the next experiment gate**

Report that isolation infrastructure is ready only if every previous gate passed. The separate five-round preflight still requires all five benchmark minimum smokes, healthy four-GPU Qwen services on logical GPU 0–3, current 694 `python RSIFlow_4B/launch.py --check`, and a final independent experiment preflight review. Do not launch the five-round job under this plan.

## Self-Review Record

- Spec coverage: Tasks 1–2 implement backend/candidate invariants; Task 3 covers EvalPlus; Task 4 covers native RPC/BFCL; Tasks 5–6 cover persistent Codex, keyless transport, thread resume, and durable jobs; Task 7 covers negative evidence review, full regression, audit, and public delivery.
- Placeholder scan: all tasks name exact files, interfaces, commands, expected red/green evidence, and commit boundaries. No model, endpoint, data-release, or scoring fallback is introduced.
- Type consistency: `BwrapRequest`, `BwrapMount`, `BwrapLimits`, `PersistentMetaSandbox.spawn()`, and existing `LinuxSandbox`, `run_native`, `CodexAppServer`, and `ResponsesTransport` names are used consistently across task boundaries.
- Review focus: mount identity, descriptor leakage, secret propagation, `/proc` failure classification, and restart job duplication each have both a focused test and a real-control-flow gate.
- Residual boundary: if Codex `workspace-write` still requires unavailable Landlock inside the outer bwrap, Task 6 fails closed and the design must be amended; this plan does not permit `danger-full-access` as an escape hatch.
