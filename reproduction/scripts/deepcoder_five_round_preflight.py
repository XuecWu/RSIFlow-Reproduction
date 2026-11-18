"""Offline, repeatable preflight for the repeated DeepCoder five-round canary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = ROOT / "RSIFlow"
for entry in (PROJECT, PROJECT / "runtime", ROOT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from component_versions import write_json
from evolution_protocol import file_hash, read
from experiment_progress import ExperimentProgress
from launch_meta import experiment_prompt
from persistent_meta import mainline_reminder, native_thread_parameters, persistent_instructions
from rsiflow.task_meta.evolution_protocol import stage_manifest
from task_adapter import TaskAdapter
from training_side_smoke import SmokeBlocked

POLICY = {
    "rounds": 5,
    "experiment_scope": "deepcoder_five_round_canary",
    "evaluation_schedule": "none",
    "retry_rejected_from_round": 6,
}
REQUIRED_INSTRUCTIONS = (
    "one attempt per round",
    "retain completes the current round and advances",
    "b1-b5",
    "no independent validation",
)
FORBIDDEN_INSTRUCTIONS = (
    "sole attempt",
    "no next round",
    "fresh round-disjoint",
    "fresh, round-disjoint",
    "fresh next-round",
    "next round's fresh",
    "no fixed retry",
    "rejection returns to component selection",
    "rejection retains the parent and returns",
    "return to step 3 on this same round/batch",
    "meta chooses again after rejection",
    "48 representative",
    "round-disjoint",
)


def _release_hashes(release):
    release = Path(release)
    hashes = {}
    for path in sorted(release.rglob("*")):
        if path.is_symlink():
            if path.name != "search.sqlite" or not path.is_file():
                raise SmokeBlocked(f"Frozen release contains an unexpected symlink: {path}")
        if path.is_file():
            hashes[path.relative_to(release).as_posix()] = file_hash(path)
    if not hashes:
        raise SmokeBlocked("Frozen release is empty")
    return hashes


def _validate_release(config, release):
    launcher = read(config)
    configured = Path(launcher["frozen_data_dir"]).resolve()
    if configured != Path(release).resolve():
        raise SmokeBlocked("Config frozen_data_dir does not identify the reviewed release")
    protocol = read(Path(release) / "protocol.json")
    ready = read(Path(release).parent / "READY.json")
    if (protocol.get("schedule") != "round_repeated" or protocol.get("rounds") != 5
            or protocol.get("tasks_per_round") != 16 or protocol.get("unique_tasks") != 16
            or protocol.get("allocated_task_slots") != 80):
        raise SmokeBlocked("Release protocol is not the repeated 16-task five-round scope")
    if ready.get("status") != "ready" or ready.get("real_evolution_started") is True:
        raise SmokeBlocked("Release READY receipt is not an offline ready receipt")

    manifests = [stage_manifest(release, number) for number in range(1, 6)]
    ids = [[task["task_id"] for task in manifest["tasks"]] for manifest in manifests]
    if any(len(manifest["tasks"]) != 16 for manifest in manifests) or ids != [ids[0]] * 5:
        raise SmokeBlocked("Each B1-B5 manifest must repeat the same ordered 16 task IDs")
    hashes = {str(number): manifest["manifest_hash"]
              for number, manifest in enumerate(manifests, 1)}
    if len(set(hashes.values())) != 5:
        raise SmokeBlocked("Round identities must have five distinct manifest hashes")
    if protocol.get("round_manifest_hashes") != hashes or ready.get("round_manifest_hashes") != hashes:
        raise SmokeBlocked("Release manifest hashes disagree with protocol.json or READY.json")
    if any(manifest.get("role") != "train_evolution" or manifest.get("round_id") != number
           for number, manifest in enumerate(manifests, 1)):
        raise SmokeBlocked("Release role or round identity changed")
    if (Path(release) / "validation").exists():
        raise SmokeBlocked("Five-round no-validation release must not contain a validation cohort")
    return launcher, manifests, hashes


def _check_instructions(text, layer):
    lowered = text.lower()
    missing = [item for item in REQUIRED_INSTRUCTIONS if item not in lowered]
    forbidden = [item for item in FORBIDDEN_INSTRUCTIONS if item in lowered]
    if missing or forbidden:
        raise SmokeBlocked(
            f"{layer} disagrees with five-round instructions: missing={missing}, forbidden={forbidden}")


def _record_turn(progress, run, number, request, receipt):
    turn = run / "meta_session/turns" / f"{number:04d}"
    write_json(turn / "command.json", {"action": "tool", "request": request})
    write_json(turn / "receipt.json", receipt)
    progress.record_turn(turn)


def _progress_preflight(run, manifests):
    session = run / "meta_session"
    write_json(session / "workflow_policy.json", POLICY)
    progress = ExperimentProgress(session, rounds=5)
    turn = 0

    def record(request, receipt=None):
        nonlocal turn
        operation = request["operation"]
        value = {"operation": operation, "status": {
            "compare_scores": "compared", "snapshot_task_meta": "snapshotted",
            "write_text": "written",
        }.get(operation, "completed")}
        if operation == "sft":
            value["candidate_state_path"] = request["output_dir"] + "/candidate_state.json"
        if operation == "append_skills":
            value["appended_count"] = 1
        if receipt:
            value.update(receipt)
        _record_turn(progress, run, turn, request, value)
        turn += 1

    record({"operation": "bootstrap", "round_number": 0,
            "state_path": str(run / "active_task_state.json")})
    record({"operation": "snapshot_task_meta", "round_number": 0,
            "destination": str(run / "snapshots/A0"),
            "active_task_state": str(run / "active_task_state.json")})

    checks = []
    chain = []
    active_id = "A0"
    for number, manifest in enumerate(manifests, 1):
        parent_id = active_id
        candidate_id = f"B{number}_candidate"
        attempt = run / f"round_{number}/attempts/attempt_1"
        parent = run / f"round_{number}/parent"
        record({"operation": "run_parent", "round_number": number,
                "state_path": str(run / f"states/{parent_id}.json"),
                "output_dir": str(parent), "manifest_hash": manifest["manifest_hash"],
                "task_ids": [task["task_id"] for task in manifest["tasks"]]})
        record({"operation": "prepare_meta_evidence", "round_number": number,
                "parent_rollout_dir": str(parent)})
        record({"operation": "write_text", "round_number": number,
                "path": str(attempt / "decision.json")})
        record({"operation": "sft", "round_number": number,
                "state_path": str(run / f"states/{parent_id}.json"),
                "parent_rollout_dir": str(parent), "output_dir": str(attempt / "sft")})
        record({"operation": "run_candidate", "round_number": number,
                "state_path": str(attempt / "sft/candidate_state.json"),
                "output_dir": str(attempt / "candidate"),
                "manifest_hash": manifest["manifest_hash"]})
        record({"operation": "compare_scores", "round_number": number,
                "before": str(parent / "performance.json"),
                "after": str(attempt / "candidate/performance.json")})
        selection = "retain_parent" if number == 2 else "accept_candidate"
        selection_request = {"operation": "write_text", "round_number": number,
                             "path": str(attempt / "selection.json"),
                             "content": json.dumps({"accept_or_retain": selection})}
        record(selection_request)
        if number == 2:
            reminder = progress.compact_status_block().lower()
            if "do not reselect or create attempt_2" not in reminder:
                raise SmokeBlocked("B2 retain progress does not forbid reselection and attempt_2")
        record({"operation": "append_skills", "round_number": number,
                "path": str(run / "meta/skills.jsonl")})
        record({"operation": "write_text", "round_number": number,
                "path": str(run / "meta/context.json")})
        active_id = candidate_id if selection == "accept_candidate" else parent_id
        record({"operation": "snapshot_task_meta", "round_number": number,
                "destination": str(run / f"snapshots/B{number}"),
                "active_task_state": str(run / f"states/{active_id}.json"),
                "parent_task_state": str(run / f"states/{parent_id}.json"),
                "candidate_task_state": str(attempt / "sft/candidate_state.json"),
                "context_path": str(run / "meta/context.json")})
        state = progress.rebuild()
        stage = next(item for item in state["stages"] if item["round"] == number)
        if stage["status"] != "complete" or stage.get("attempt") != 1 or stage.get("retry_pending"):
            raise SmokeBlocked(f"B{number} did not complete exactly one terminal attempt")
        expected_stage = f"B{number + 1}" if number < 5 else "COMPLETE"
        if state["current_stage"] != expected_stage or state["finish_allowed"] != (number == 5):
            raise SmokeBlocked(f"B{number} advanced to an unexpected stage or finish gate")
        checks.append({"round": number, "selection": selection,
                       "current_stage_after": state["current_stage"],
                       "finish_allowed": state["finish_allowed"],
                       "attempt": stage["attempt"], "retain_advanced": selection == "retain_parent",
                       "manifest_hash": manifest["manifest_hash"]})
        chain.append({"round": number, "parent_task_id": parent_id,
                      "candidate_task_id": candidate_id, "selected_task_id": active_id,
                      "manifest_hash": manifest["manifest_hash"]})

    final = progress.rebuild()
    if not progress.finish_allowed() or final["current_stage"] != "COMPLETE":
        raise SmokeBlocked("B5 completion did not open the production finish gate")
    validation = sorted({milestone for stage in final["stages"]
                         for milestone in stage["required_milestones"]
                         if milestone in {"validation_snapshot", "independent_validation"}})
    if validation:
        raise SmokeBlocked("No-validation policy retained validation milestones")
    return checks, chain, final


def run_preflight(config, release, output):
    config = Path(config).resolve()
    release = Path(release).resolve()
    output = Path(output).resolve()
    hashes_before = _release_hashes(release)
    launcher, manifests, manifest_hashes = _validate_release(config, release)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="deepcoder-five-round-preflight-") as temporary:
        run = Path(temporary) / "run"
        session = run / "meta_session"
        session.mkdir(parents=True)
        effective = TaskAdapter(PROJECT, Path(temporary) / "receipts")._config(
            {"config_path": str(config)})
        if (effective.experiment_scope != POLICY["experiment_scope"]
                or effective.max_generations != 5 or effective.window_quotas != {
                    "tool_use": 0, "code": 16, "searchqa": 0}
                or launcher.get("evaluation_schedule") != "none"
                or launcher.get("retry_rejected_from_round") != 6):
            raise SmokeBlocked("Effective config is not the no-validation five-round scope")
        write_json(session / "workflow_policy.json", POLICY)
        prompt = experiment_prompt(config, launcher, run)
        layers = {
            "initial_prompt": prompt,
            "persistent_instructions": persistent_instructions(POLICY),
            "mainline_reminder": mainline_reminder(POLICY),
        }
        for layer, text in layers.items():
            _check_instructions(text, layer)
        if ("same codex app-server\nprocess and thread" not in layers["persistent_instructions"].lower()
                or "on migration or resume" not in prompt.lower()
                or "never bootstrap over an existing evolved task" not in prompt.lower()):
            raise SmokeBlocked("Same-thread resume instructions are incomplete")
        native = native_thread_parameters(ROOT, effective.meta.model_dump(), POLICY)
        _check_instructions(native["developerInstructions"], "native thread parameters")
        write_json(session / "state.json", {
            "transport": "app_server", "status": "disconnected",
            "thread_id": "offline-five-round-thread", "sandbox_backend": "offline-only",
        })
        round_checks, chain, final = _progress_preflight(run, manifests)

    if _release_hashes(release) != hashes_before:
        raise SmokeBlocked("Frozen release changed during offline preflight")
    reviewed = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=30, check=True).stdout.strip()
    receipt = {
        "schema_version": 1,
        "status": "passed",
        "reviewed_commit": reviewed,
        "config_path": str(config),
        "config_sha256": file_hash(config),
        "release_dir": str(release),
        "release_files_sha256": hashes_before,
        "round_manifest_hashes": manifest_hashes,
        "rounds": 5,
        "unique_tasks": 16,
        "round_checks": round_checks,
        "active_state_chain": chain,
        "instruction_layers": list(layers),
        "same_thread_resume": {
            "instruction_verified": True,
            "expected_transport_method": "thread/resume",
            "simulated_thread_id": "offline-five-round-thread",
            "performed": False,
            "provider_calls": 0,
        },
        "independent_validation": "none",
        "validation_milestones": [],
        "observed_offline_turns": final["observed_turns"],
        "real_evolution_started": False,
        "formal_round_completed": False,
        "model_calls": 0,
        "api_calls": 0,
        "gpu_calls": 0,
        "output_path": str(output),
    }
    if output.exists() and read(output) != receipt:
        raise SmokeBlocked(f"Existing preflight output differs: {output}")
    write_json(output, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = run_preflight(args.config, args.release, args.output)
    except (OSError, ValueError, SmokeBlocked, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "status": receipt["status"], "rounds": 5,
                      "real_evolution_started": False, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
