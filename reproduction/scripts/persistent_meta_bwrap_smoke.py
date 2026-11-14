import argparse
import hashlib
import json
import os
import shutil
import sys
import tomllib
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "RSIFlow"))
sys.path.insert(0, str(PROJECT / "RSIFlow/runtime"))
sys.path.insert(0, str(PROJECT / "reproduction/scripts"))

from auditlib import sanitize
from experiment_progress import ExperimentProgress
from launch_meta import SubprocessToolExecutor, prepare_codex_home, prepare_meta_storage
from persistent_meta import (
    CodexAppServer,
    NativeExperimentTools,
    dynamic_tools,
    native_thread_parameters,
    process_identity,
)
from persistent_meta_sandbox import (
    BACKEND,
    BWRAP_LAUNCHER_SHA256,
    BWRAP_MANIFEST_SHA256,
    UPSTREAM,
    PersistentMetaSandbox,
)
from rsiflow.task_meta.meta_backends import MetaBackendConfig

EXPECTED_MODEL = "gpt-6-astra"
EXPECTED_PROVIDER = "OpenAI"
FIXTURE = {"public_fixture": "task6-public-fixture", "number": 6}
JOB_ARGV = ["/bin/sh", "-c", "sleep 20"]
SMOKE_INSTRUCTIONS = """This is a bounded infrastructure smoke, not an evolution experiment.
Use only the specific native or dynamic tool requested in each user message. Never call bootstrap,
finish_experiment, wait_job, or run_command unless that exact operation is explicitly requested.
Do not create additional jobs, edit files, load models, train, evaluate, or access private data.
Each requested dynamic operation must be called exactly once before ending that turn.
"""


def reconcile_resume(before, after):
    before_jobs = set(before.get("jobs", {}))
    after_jobs = set(after.get("jobs", {}))
    return {
        "same_thread": bool(before.get("thread_id")) and before.get("thread_id") == after.get("thread_id"),
        "duplicate_jobs": sorted(after_jobs - before_jobs),
        "bootstrap_repeated": after.get("bootstrap_receipts", 0) > before.get("bootstrap_receipts", 0),
    }


def validate_smoke_report(report):
    reconciliation = reconcile_resume(report["before"], report["after"])
    failures = [name for name, failed in (
        ("same_thread", not reconciliation["same_thread"]),
        ("duplicate_jobs", bool(reconciliation["duplicate_jobs"])),
        ("bootstrap_repeated", reconciliation["bootstrap_repeated"]),
        ("dynamic_tool_receipt", not report.get("dynamic_tool_receipt")),
        ("returned_model", report.get("returned_model") != EXPECTED_MODEL),
    ) if failed]
    if failures:
        raise ValueError("smoke reconciliation failed: " + ", ".join(failures))
    return reconciliation


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def value_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def sanitized_text(value, secret="", marker=""):
    text = sanitize(value)
    if secret:
        text = text.replace(secret, "[REDACTED]")
    if marker:
        text = text.replace(marker, "[REDACTED_PARENT_MARKER]")
    return text


def write_sanitized_json(path, value, secret="", marker=""):
    text = sanitized_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), secret, marker)
    Path(path).write_text(text + "\n", encoding="utf-8")


def load_meta(config_path):
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    meta = config["meta"]
    parsed = MetaBackendConfig(**meta)
    expected = {
        "provider": EXPECTED_PROVIDER,
        "model": EXPECTED_MODEL,
        "base_url": UPSTREAM,
        "api_key_env": "OPENAI_API_KEY",
        "execution_location": "local_chroot",
    }
    actual = {name: getattr(parsed, name) for name in expected}
    if actual != expected or parsed.allow_model_fallback or parsed.allow_provider_fallback:
        raise ValueError("smoke provider identity or fallback policy mismatch")
    return meta


def offline_check(config_path):
    meta = load_meta(config_path)
    host = tomllib.loads((PROJECT / "reproduction/configs/codex_host.toml").read_text(encoding="utf-8"))
    provider = host["model_providers"]["OpenAI"]
    checks = {
        "backend": BACKEND == "linux_bwrap_seccomp_v1",
        "codex_binary_hash": digest(meta["codex_executable"]) == meta["codex_binary_sha256"],
        "catalog_hash": digest(meta["model_catalog_json"]) == meta["model_catalog_sha256"],
        "manifest_hash": digest(PROJECT / "RSIFlow/configs/bwrap_isolation.json") == BWRAP_MANIFEST_SHA256,
        "launcher_hash": digest(PROJECT / "RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py") == BWRAP_LAUNCHER_SHA256,
        "dynamic_tools": {tool["name"] for tool in dynamic_tools()} == {
            "experiment", "job_status", "wait_job", "finish_experiment",
        },
        "host_model": host["model"] == EXPECTED_MODEL and host["model_provider"] == EXPECTED_PROVIDER,
        "host_relay": provider["base_url"] == "http://127.0.0.1:18443/api/v1"
        and provider["env_key"] == "RSIFLOW_RELAY_KEY" and provider["wire_api"] == "responses",
        "workspace_write": host["sandbox_mode"] == "workspace-write",
        "network_disabled": host["sandbox_workspace_write"]["network_access"] is False,
        "no_danger_full_access": "danger-full-access" not in json.dumps(host),
        "bounded": meta["budget"]["wall_time_seconds"] <= 480 and meta["budget"]["max_requests"] <= 16,
    }
    result = {"ok": all(checks.values()), "checks": checks, "backend": BACKEND,
              "provider": EXPECTED_PROVIDER, "model": EXPECTED_MODEL, "upstream": UPSTREAM}
    print(json.dumps(result, sort_keys=True))
    return result["ok"]


def prepare_codex_installation_id(codex_home):
    destination = Path(codex_home) / "installation_id"
    if not destination.exists():
        destination.write_text(str(uuid.uuid4()) + "\n", encoding="utf-8")
    destination.chmod(0o644)


def make_controller(run, meta):
    codex_home = run / "meta_session/codex_home"
    executor = SubprocessToolExecutor(
        project_root=PROJECT / "RSIFlow", run_dir=run, workspace=PROJECT,
    )
    progress = ExperimentProgress(run / "meta_session", rounds=1)
    tools = NativeExperimentTools(
        project=PROJECT / "RSIFlow", run=run, executor=executor, progress=progress,
    )
    sandbox = PersistentMetaSandbox(
        project=PROJECT / "RSIFlow", run=run, codex_home=codex_home,
        meta_config=meta, writable_outputs=(),
    )
    server = CodexAppServer(
        codex_home=codex_home, workspace=PROJECT, journal=run / "meta_session/app_server",
        executable="/codex", on_tool=tools, process_launcher=sandbox,
    )
    return server, sandbox, tools


def thread_parameters(meta):
    parameters = native_thread_parameters(PROJECT, meta)
    parameters["developerInstructions"] = SMOKE_INSTRUCTIONS
    # This kernel cannot run the vendor shell sandbox (no Landlock; nested
    # bwrap is blocked by the anti-escape seccomp), so the smoke disables the
    # native shell tool deterministically instead of relying on its failure.
    parameters["config"]["features.shell_tool"] = False
    return parameters


def environments():
    return [{"environmentId": "local", "cwd": str(PROJECT), "runtimeWorkspaceRoots": [str(PROJECT)]}]


def host_calls(run):
    calls = []
    root = run / "meta_session/turns"
    for command_path in sorted(root.glob("*/command.json")):
        command = json.loads(command_path.read_text(encoding="utf-8"))
        receipt_path = command_path.with_name("receipt.json")
        calls.append({
            "turn_dir": command_path.parent,
            "request": command["request"],
            "call_id": command.get("native_call_id"),
            "receipt": json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else None,
        })
    return calls


def operation_calls(run, operation):
    return [call for call in host_calls(run) if call["request"].get("operation") == operation]


def bootstrap_count(run):
    return len(operation_calls(run, "bootstrap"))


def jobs_snapshot(tools):
    records = tools.jobs.all()
    statuses = {job["job_id"]: tools.jobs.status(job["job_id"])["status"] for job in records}
    pids = {job["job_id"]: job["pid"] for job in records}
    return statuses, pids


def validate_job_status_receipt(receipt, job_id):
    if (not receipt or receipt.get("job_id") != job_id
            or receipt.get("status") not in {"running", "completed"}):
        raise ValueError("job_status did not observe only the original job")
    return receipt


def read_events(run):
    path = run / "meta_session/app_server/events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def dynamic_call_event(run, call_id):
    matches = []
    for event in read_events(run):
        if event.get("method") != "item/tool/call":
            continue
        params = event.get("params", {})
        if params.get("callId") == call_id:
            matches.append({"request_id": event.get("id"), "thread_id": params.get("threadId"),
                            "turn_id": params.get("turnId"), "call_id": call_id,
                            "tool": params.get("tool")})
    if len(matches) != 1:
        raise ValueError("dynamic tool event receipt missing or duplicated")
    return matches[0]


def require_completed(turn):
    if turn.get("status") != "completed":
        raise ValueError("Codex turn did not complete")


def copy_provider(run, name):
    source = run / "meta_session/provider"
    destination = run / "evidence" / name
    shutil.copytree(source, destination)
    shutil.copy2(source.parent / "transport_records.json", destination / "transport_records.json")
    return destination


def provider_summary(directory):
    records = json.loads((Path(directory) / "transport_records.json").read_text(encoding="utf-8"))
    if not records or any(not record.get("completed") or not record.get("accounting_completed")
                          or record.get("returned_model") != EXPECTED_MODEL for record in records):
        raise ValueError("provider receipts incomplete or returned model mismatch")
    files = {str(path.relative_to(directory)): digest(path)
             for path in sorted(Path(directory).rglob("*")) if path.is_file()}
    return {
        "completed_requests": len(records),
        "returned_models": sorted({record["returned_model"] for record in records}),
        "transport_records_sha256": digest(Path(directory) / "transport_records.json"),
        "files_sha256": files,
        "receipt_count": len(list((Path(directory) / "receipts").glob("*.json"))),
    }


def process_group_members(pgid):
    members = []
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
            if int(fields[2]) == pgid and fields[0] != "Z":
                members.append(int(path.parent.name))
        except (OSError, ValueError, IndexError):
            continue
    return members


def process_key_absent(pid, name, secret):
    try:
        fields = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False
    prefix = name.encode() + b"="
    return not any(field.startswith(prefix) for field in fields) and secret.encode() not in fields


def sanitized_event(event, raw_line):
    result = {"method": event.get("method"), "event_sha256": hashlib.sha256(raw_line.encode()).hexdigest()}
    if isinstance(event.get("id"), (str, int)):
        result["id"] = event["id"]
    params = event.get("params", {})
    safe_params = {key: params[key] for key in ("threadId", "turnId", "callId", "tool")
                   if isinstance(params.get(key), (str, int))}
    arguments = params.get("arguments")
    if isinstance(arguments, dict):
        safe_params["arguments_sha256"] = value_digest(arguments)
        request = arguments.get("request")
        if isinstance(request, dict) and isinstance(request.get("operation"), str):
            safe_params["operation"] = request["operation"]
    turn = params.get("turn")
    if isinstance(turn, dict):
        safe_params["turn"] = {key: turn[key] for key in ("id", "status") if isinstance(turn.get(key), str)}
    item = params.get("item")
    if isinstance(item, dict):
        safe_item = {key: item[key] for key in ("id", "type", "status", "tool", "success", "exitCode")
                     if isinstance(item.get(key), (str, bool, int))}
        item_arguments = item.get("arguments")
        if isinstance(item_arguments, dict):
            safe_item["arguments_sha256"] = value_digest(item_arguments)
        safe_params["item"] = safe_item
    if safe_params:
        result["params"] = safe_params
    response = event.get("result", {})
    if isinstance(response, dict):
        safe_result = {}
        thread = response.get("thread")
        if isinstance(thread, dict) and isinstance(thread.get("id"), str):
            safe_result["thread_id"] = thread["id"]
        for key in ("model", "modelProvider"):
            if isinstance(response.get(key), str):
                safe_result[key] = response[key]
        turn = response.get("turn")
        if isinstance(turn, dict):
            safe_result["turn"] = {key: turn[key] for key in ("id", "status") if isinstance(turn.get(key), str)}
        if safe_result:
            result["result"] = safe_result
    return result


def write_sanitized_events(run, secret, marker):
    source = run / "meta_session/app_server/events.jsonl"
    destination = run / "evidence/events.sanitized.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if source.exists():
        for raw_line in source.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            event = json.loads(raw_line)
            lines.append(sanitized_text(
                json.dumps(sanitized_event(event, raw_line), ensure_ascii=False, sort_keys=True), secret, marker,
            ))
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return destination


def write_failure_diagnostics(run, secret, marker):
    evidence = run / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    journal = run / "meta_session/app_server"
    diagnostics = {"journal_files": []}
    if (journal / "events.jsonl").is_file():
        events = write_sanitized_events(run, secret, marker)
        diagnostics["events_sanitized"] = {"path": str(events), "sha256": digest(events)}
    if (journal / "stderr.log").is_file():
        stderr = evidence / "stderr.sanitized.log"
        stderr.write_text(sanitized_text(
            (journal / "stderr.log").read_text(encoding="utf-8", errors="replace"), secret, marker,
        ), encoding="utf-8")
        diagnostics["stderr_sanitized"] = {"path": str(stderr), "sha256": digest(stderr)}
    if journal.is_dir():
        diagnostics["journal_files"] = [
            {"name": path.name, "sha256": digest(path), "size_bytes": path.stat().st_size}
            for path in sorted(journal.iterdir()) if path.is_file()
        ]
    destination = evidence / "app_server_diagnostics.json"
    write_sanitized_json(destination, diagnostics, secret, marker)
    return {"path": str(destination), "sha256": digest(destination)}


def close_controller(server, sandbox):
    try:
        if server is not None:
            server.close()
    finally:
        if sandbox is not None:
            sandbox.close()


def run_smoke(config_path, run):
    meta = load_meta(config_path)
    run = run.absolute()
    report = {
        "schema_version": 1,
        "ok": False,
        "backend": BACKEND,
        "provider": EXPECTED_PROVIDER,
        "upstream": UPSTREAM,
        "expected_model": EXPECTED_MODEL,
        "run_dir": str(run),
        "stage": "initialize",
    }
    server = sandbox = tools = None
    first_tools = None
    secret = os.environ.get("OPENAI_API_KEY", "")
    marker = "task6-parent-only-" + uuid.uuid4().hex
    os.environ["RSIFLOW_TASK6_PARENT_MARKER"] = marker
    first_relay = second_relay = None
    first_pgid = second_pgid = None
    before_provider = after_provider = None
    try:
        if not secret:
            raise ValueError("host provider key is missing")
        if run.exists():
            raise FileExistsError("smoke run directory must be fresh")
        run.mkdir(parents=True)
        prepare_meta_storage(run, meta_logs_root=run / "Meta_logs")
        codex_home = prepare_codex_home(
            run, host_path=PROJECT / "reproduction/configs/codex_host.toml",
            catalog_path=meta["model_catalog_json"],
        )
        prepare_codex_installation_id(codex_home)
        fixture_path = run / "public_fixture.json"
        fixture_path.write_text(json.dumps(FIXTURE, indent=2) + "\n", encoding="utf-8")
        report["fixture"] = {"sha256": digest(fixture_path), "value": FIXTURE}
        os.environ["RSIFLOW_SANDBOX_BACKEND"] = BACKEND

        report["stage"] = "first_app_server"
        server, sandbox, tools = make_controller(run, meta)
        first_tools = tools
        server.start()
        first_pgid = server.process.pid
        first_relay = sandbox.relay_directory
        identity = sandbox.identity()
        if identity["backend"] != BACKEND:
            raise ValueError("sandbox backend mismatch")
        report["runtime_identity"] = identity
        report["key_hygiene"] = {
            "host_transport_has_real_key": sandbox.transport._secret == secret,
            "sandbox_real_key_absent": "OPENAI_API_KEY" not in sandbox._environment()
            and secret not in sandbox._environment().values(),
            "sandbox_parent_marker_absent": marker not in sandbox._environment().values(),
            "sandbox_dummy_relay_key_present": sandbox._environment().get("RSIFLOW_RELAY_KEY") == "pipe-only",
            "codex_config_real_key_absent": secret not in (sandbox.codex_home / "config.toml").read_text(),
            "codex_config_marker_absent": marker not in (sandbox.codex_home / "config.toml").read_text(),
            "codex_config_dummy_relay_key_present": "RSIFLOW_RELAY_KEY" in (sandbox.codex_home / "config.toml").read_text(),
        }
        reply = server.request("thread/start", {**thread_parameters(meta), "dynamicTools": dynamic_tools()})
        first_thread = reply["thread"]["id"]
        report["first_thread_id"] = first_thread
        if reply.get("model", EXPECTED_MODEL) != EXPECTED_MODEL or reply.get("modelProvider", EXPECTED_PROVIDER) != EXPECTED_PROVIDER:
            raise ValueError("thread/start provider identity mismatch")

        report["stage"] = "read_json_turn"
        request_json = json.dumps({"request": {"operation": "read_json", "path": str(fixture_path)}})
        prompt = ("Call experiment exactly once with this exact arguments object, including every field:\n"
                  + request_json + "\nDo not call any other dynamic tool. After its result, end the turn.")
        require_completed(server.turn(first_thread, prompt, environments=environments()))
        reads = operation_calls(run, "read_json")
        if len(reads) != 1 or reads[0]["request"] != {"operation": "read_json", "path": str(fixture_path)}:
            raise ValueError("read_json must have exactly one exact host call")
        read_receipt = reads[0]["receipt"]
        if not read_receipt or read_receipt.get("status") != "read" or read_receipt.get("value") != FIXTURE:
            raise ValueError("read_json host receipt does not contain the public fixture")
        report["dynamic_tool_receipt"] = {
            **dynamic_call_event(run, reads[0]["call_id"]),
            "host_receipt_sha256": digest(reads[0]["turn_dir"] / "receipt.json"),
            "fixture_value": FIXTURE,
        }

        report["stage"] = "durable_job_turn"
        request_json = json.dumps({"request": {"operation": "run_command", "argv": JOB_ARGV}})
        prompt = ("Call experiment exactly once with this exact arguments object, including every field:\n"
                  + request_json + "\nDo not wait for the job and do not call job_status or any other dynamic tool. "
                  "Return the job receipt and end the turn.")
        require_completed(server.turn(first_thread, prompt, environments=environments()))
        starts = operation_calls(run, "run_command")
        if len(starts) != 1 or starts[0]["request"].get("argv") != JOB_ARGV:
            raise ValueError("run_command must have exactly one exact host call")
        start_receipt = starts[0]["receipt"]
        if not start_receipt or start_receipt.get("status") != "running":
            raise ValueError("durable job did not remain running")
        job_id = start_receipt["job_id"]
        job_pid = start_receipt["pid"]
        job_record = json.loads((run / "meta_session/jobs" / job_id / "job.json").read_text())
        if job_pid != job_record["pid"] or process_identity(job_pid) != job_record["process_start"]:
            raise ValueError("durable job PID identity mismatch")
        report["job_id"] = job_id
        report["job_pid"] = job_pid
        report["key_hygiene"]["host_job_provider_key_absent"] = process_key_absent(job_pid, "OPENAI_API_KEY", secret)
        before_jobs, before_pids = jobs_snapshot(tools)
        if len(before_jobs) != 1 or before_pids != {job_id: job_pid}:
            raise ValueError("exactly one original durable job/PID is required")
        report["before"] = {"thread_id": first_thread, "jobs": before_jobs,
                            "bootstrap_receipts": bootstrap_count(run)}
        before_provider = copy_provider(run, "provider_before_restart")
        report["provider_before_restart"] = provider_summary(before_provider)

        report["stage"] = "close_first_app_server"
        close_controller(server, sandbox)
        server = sandbox = tools = None
        report["host_job_alive_after_appserver_close"] = process_identity(job_pid) == job_record["process_start"]
        if not report["host_job_alive_after_appserver_close"]:
            raise ValueError("host durable job did not survive app-server close")
        report["first_appserver_group_clean"] = not process_group_members(first_pgid)
        report["first_relay_removed"] = first_relay is not None and not first_relay.exists()

        report["stage"] = "resume_before_new_turn"
        server, sandbox, tools = make_controller(run, meta)
        server.start()
        second_pgid = server.process.pid
        second_relay = sandbox.relay_directory
        if sandbox.identity()["backend"] != BACKEND:
            raise ValueError("resumed sandbox backend mismatch")
        report["key_hygiene"]["resumed_host_transport_has_real_key"] = sandbox.transport._secret == secret
        report["key_hygiene"]["resumed_sandbox_parent_marker_absent"] = (
            marker not in sandbox._environment().values()
        )
        reply = server.request("thread/resume", {
            **thread_parameters(meta), "threadId": first_thread, "excludeTurns": True,
        })
        second_thread = reply["thread"]["id"]
        report["second_thread_id"] = second_thread
        report["resume_exclude_turns"] = True
        after_jobs, after_pids = jobs_snapshot(tools)
        report["after"] = {"thread_id": second_thread, "jobs": after_jobs,
                           "bootstrap_receipts": bootstrap_count(run)}
        report["reconciliation"] = reconcile_resume(report["before"], report["after"])
        if (not report["reconciliation"]["same_thread"] or report["reconciliation"]["duplicate_jobs"]
                or report["reconciliation"]["bootstrap_repeated"] or after_pids != before_pids
                or len(after_jobs) != 1):
            raise ValueError("resume reconciliation failed before any new turn")
        report["resumed_job_pids"] = after_pids

        report["stage"] = "job_status_turn"
        request_json = json.dumps({"job_id": job_id})
        prompt = ("Call job_status exactly once with this exact arguments object:\n" + request_json
                  + "\nDo not start, wait for, or restart any job. Do not call experiment or any other dynamic tool. "
                  "Return the observed status and end the turn.")
        require_completed(server.turn(second_thread, prompt, environments=environments()))
        statuses = operation_calls(run, "job_status")
        if len(statuses) != 1 or statuses[0]["request"] != {"operation": "job_status", "job_id": job_id}:
            raise ValueError("job_status must have exactly one exact host call after resume")
        status_receipt = statuses[0]["receipt"]
        validate_job_status_receipt(status_receipt, job_id)
        report["job_status_receipt_sha256"] = digest(statuses[0]["turn_dir"] / "receipt.json")

        report["stage"] = "second_provider_evidence"
        after_provider = copy_provider(run, "provider_after_restart")
        report["provider_after_restart"] = provider_summary(after_provider)
        models = set(report["provider_before_restart"]["returned_models"]) | set(report["provider_after_restart"]["returned_models"])
        report["returned_model"] = models.pop() if len(models) == 1 else "mismatch"

        report["stage"] = "close_second_app_server"
        close_controller(server, sandbox)
        server = sandbox = tools = None
        terminal = first_tools.jobs.wait(job_id, seconds=40)
        if terminal.get("status") != "completed":
            raise ValueError("durable job did not reach a terminal result")
        result = terminal.get("result", {})
        if result.get("status") != "exited" or result.get("returncode") != 0 or result.get("argv") != JOB_ARGV:
            raise ValueError("durable job terminal result mismatch")
        report["terminal_job"] = {
            "job_id": job_id, "status": terminal["status"], "result_status": result["status"],
            "returncode": result["returncode"], "result_sha256": value_digest(result),
        }
        final_jobs, final_pids = jobs_snapshot(first_tools)
        if set(final_jobs) != {job_id} or final_pids != {job_id: job_pid} or bootstrap_count(run) != 0:
            raise ValueError("final durable job/bootstrap set changed")
        report["final_jobs"] = final_jobs
        report["final_job_pids"] = final_pids
        report["cleanup"] = {
            "first_appserver_group_clean": not process_group_members(first_pgid),
            "second_appserver_group_clean": not process_group_members(second_pgid),
            "first_relay_removed": first_relay is not None and not first_relay.exists(),
            "second_relay_removed": second_relay is not None and not second_relay.exists(),
            "durable_job_process_gone": process_identity(job_pid) is None,
            "codex_home_persists": (run / "meta_session/codex_home/config.toml").is_file(),
            "provider_receipts_persist": (run / "meta_session/transport_records.json").is_file()
            and before_provider.is_dir() and after_provider.is_dir(),
        }
        report["stage"] = "sanitize_evidence"
        events_path = write_sanitized_events(run, secret, marker)
        report["events"] = {
            "raw_sha256": digest(run / "meta_session/app_server/events.jsonl"),
            "sanitized_sha256": digest(events_path),
            "dynamic_tool_calls": sum(event.get("method") == "item/tool/call" for event in read_events(run)),
        }
        stderr_path = run / "meta_session/app_server/stderr.log"
        sanitized_stderr = run / "evidence/stderr.sanitized.log"
        sanitized_stderr.write_text(sanitized_text(stderr_path.read_text(encoding="utf-8"), secret, marker), encoding="utf-8")
        report["events"]["stderr_sha256"] = digest(stderr_path)
        report["events"]["sanitized_stderr_sha256"] = digest(sanitized_stderr)
        public_text = events_path.read_text(encoding="utf-8") + sanitized_stderr.read_text(encoding="utf-8")
        report["key_hygiene"]["sanitized_events_secret_absent"] = secret not in public_text and marker not in public_text
        if not all(report["key_hygiene"].values()) or not all(report["cleanup"].values()):
            raise ValueError("key hygiene or cleanup check failed")
        report["reconciliation"] = validate_smoke_report(report)
        report["ok"] = True
        report["stage"] = "completed"
    except Exception as exc:  # noqa: BLE001
        report["error_type"] = type(exc).__name__
        report["error_message"] = sanitized_text(str(exc), secret, marker)[:16384]
        report["failed_stage"] = report["stage"]
    finally:
        try:
            close_controller(server, sandbox)
        except Exception as exc:  # noqa: BLE001
            report["ok"] = False
            report["cleanup_error_type"] = type(exc).__name__
        if run.exists():
            try:
                if not report["ok"]:
                    report["diagnostics"] = write_failure_diagnostics(run, secret, marker)
                elif (run / "meta_session/app_server/events.jsonl").exists():
                    write_sanitized_events(run, secret, marker)
                write_sanitized_json(run / "report.json", report, secret, marker)
                public_report = (run / "report.json").read_text(encoding="utf-8")
                if (secret and secret in public_report) or marker in public_report:
                    raise ValueError("sanitized report contains a parent-only value")
            except Exception as exc:  # noqa: BLE001
                report["ok"] = False
                report["report_error_type"] = type(exc).__name__
                write_sanitized_json(run / "report.json", report, secret, marker)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=PROJECT / "reproduction/configs/meta_bwrap_smoke.json")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        return 0 if offline_check(args.config) else 1
    if args.run_dir is None:
        parser.error("--run-dir is required unless --check is used")
    report = run_smoke(args.config, args.run_dir)
    print(json.dumps({"report_path": str(args.run_dir.absolute() / "report.json"), "ok": report["ok"],
                      "stage": report["stage"], "error_type": report.get("error_type")}, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
