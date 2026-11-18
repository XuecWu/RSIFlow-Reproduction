"""Build the immutable five-round repeated DeepCoder function canary release offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "RSIFlow", ROOT / "RSIFlow/runtime", ROOT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import prepare_deepcoder_canary as canary
from training_side_smoke import SmokeBlocked
from evolution_protocol import file_hash, freeze, make_manifest, read
from prepare_600_744 import repack


def build_five_round_release(output_root, machine_config_path, parent_manifest_path):
    built = canary._materialize_release(output_root, machine_config_path, parent_manifest_path)
    root = built["root"]
    release_dir = Path(built["release"]["release_dir"])
    selection = built["selection"]
    manifests = {1: read(release_dir / "B1/manifest.json")}

    for number in range(2, 6):
        tasks = [{**task, "round_id": number} for task in selection["tasks"]]
        current = make_manifest("train_evolution", number, tasks,
                                split_seed=built["launcher"].get("split_seed", 42))
        packed = repack(current, release_dir / f"B{number}")
        freeze(release_dir / f"B{number}/manifest.json", packed)
        manifests[number] = packed

    manifest_hashes = {str(number): value["manifest_hash"]
                       for number, value in manifests.items()}
    protocol = {
        "schema_version": 1,
        "schedule": "round_repeated",
        "rounds": 5,
        "tasks_per_round": 16,
        "unique_tasks": 16,
        "allocated_task_slots": 80,
        "sqlite_index_round": 1,
        "round_manifest_hashes": manifest_hashes,
    }
    freeze(release_dir / "protocol.json", protocol)

    hashes_after = canary._hashes(built["assets"])
    parent_unchanged = (file_hash(built["parent_path"])
                        == selection["parent_manifest_sha256"])
    if hashes_after != built["hashes_before"] or not parent_unchanged:
        raise SmokeBlocked("Source v5 assets changed while materializing the DeepCoder five-round canary")

    receipt = {
        "schema_version": 1,
        "status": "ready",
        "schedule": "round_repeated",
        "rounds": 5,
        "task_count": 16,
        "tasks_per_round": 16,
        "unique_tasks": 16,
        "allocated_task_slots": 80,
        "output_root": str(root),
        "output_root_validation": built["validation"],
        "release_dir": str(release_dir),
        "protocol_path": str(release_dir / "protocol.json"),
        "tasks_sqlite": str(release_dir / "tasks.sqlite"),
        "selection_path": str(root / "selection.json"),
        "selection_rule": canary.SELECTION_RULE,
        "parent_manifest_path": str(built["parent_path"]),
        "parent_manifest_hash": built["parent"]["manifest_hash"],
        "parent_manifest_sha256": selection["parent_manifest_sha256"],
        "round_manifest_hashes": manifest_hashes,
        "source_asset_hashes_before": built["hashes_before"],
        "source_asset_hashes_after": hashes_after,
        "source_assets_unchanged": True,
        "smoke_only": True,
        "feedback_role": "train_evolution",
        "explicit_full_benchmark": False,
        "formal_round_completed": False,
        "model_calls": 0,
        "api_calls": 0,
        "gpu_calls": 0,
    }
    freeze(root / "READY.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--machine-config", required=True, type=Path)
    parser.add_argument("--parent-manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build_five_round_release(
            args.output_root, args.machine_config, args.parent_manifest)
    except (OSError, ValueError, SmokeBlocked) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({
        "ok": True,
        "status": receipt["status"],
        "schedule": receipt["schedule"],
        "rounds": receipt["rounds"],
        "task_count": receipt["task_count"],
        "unique_tasks": receipt["unique_tasks"],
        "allocated_task_slots": receipt["allocated_task_slots"],
        "round_manifest_hashes": receipt["round_manifest_hashes"],
        "source_assets_unchanged": receipt["source_assets_unchanged"],
        "output_root": receipt["output_root"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
