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


## Controller scope addendum (binding for Task 6 acceptance)

The original Step 4 native-shell turn is not executable in the approved sandbox architecture on this kernel. Codex legacy Landlock returns `Sandbox(LandlockRestrict)`; Codex vendor bwrap requires nested namespace creation that the reviewed outer anti-escape seccomp intentionally denies. Weakening that seccomp, using `danger-full-access`, or falling back to host execution violates the binding design constraints.

Accordingly, the controller's recorded scope ruling supersedes only the native-shell execution paragraph of Step 4: the accepted hygiene gate is the construction-level sandbox environment/config/resumed-sandbox assertions plus host dynamic-tool job key-absence checks and sanitized public logs. The smoke must set `shell_tool=false` deterministically. This is a disclosed validation limitation, not a claim that a real Codex native-shell descendant was tested. All other Step 4 requirements and all other Task 6 requirements remain binding.
