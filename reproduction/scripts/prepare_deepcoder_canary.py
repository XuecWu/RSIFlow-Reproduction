"""Build the fixed sixteen-task DeepCoder/TACO function canary release offline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "RSIFlow", ROOT / "RSIFlow/runtime", ROOT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import training_side_smoke as smoke
from training_side_smoke import SmokeBlocked
from evolution_protocol import file_hash, freeze, read

SELECTION_RULE = ("first sixteen deepcoder_taco function records in frozen parent B1 manifest "
                  "order; fixed before any model output; no random scoring or result-based reselection")


def select_deepcoder_function(parent_manifest, count=16):
    matches = [task for task in parent_manifest["tasks"]
               if task.get("source") == "deepcoder_taco"
               and (task.get("strata") or {}).get("interface") == "function"]
    if len(matches) < count:
        raise ValueError("DeepCoder function frozen B1 has fewer than sixteen eligible tasks")
    chosen = matches[:count]
    return {"tasks": chosen, "records": chosen,
            "task_ids": [task["task_id"] for task in chosen],
            "rule": SELECTION_RULE, "parent_manifest_hash": parent_manifest["manifest_hash"],
            "parent_round_id": 1, "model_results_used": False}


def _source_assets(parent, launcher):
    paths = {Path(task["record_file"]).resolve() for task in parent["tasks"]}
    retrieval = Path(launcher["retrieval_index"]).resolve()
    paths.update({retrieval, retrieval.with_suffix(".sqlite.manifest.json")})
    return sorted(paths)


def _hashes(paths):
    return {str(path): file_hash(path) for path in paths}


def _materialize_release(output_root, machine_config_path, parent_manifest_path):
    validation = smoke._validate_output_root(output_root)
    root = Path(validation["path"])
    launcher = read(machine_config_path)
    parent_path = Path(parent_manifest_path).resolve()
    parent = read(parent_path)
    selection = select_deepcoder_function(parent)
    assets = _source_assets(parent, launcher)
    hashes_before = _hashes(assets)
    selection.update(parent_manifest_path=str(parent_path),
                     parent_manifest_sha256=file_hash(parent_path),
                     source_asset_hashes=hashes_before)

    root.mkdir(parents=True)
    release = smoke.materialize_mini_release(selection, root, launcher)
    hashes_after = _hashes(assets)
    if hashes_after != hashes_before:
        raise smoke.SmokeBlocked("Source v5 assets changed while materializing the DeepCoder canary")
    freeze(root / "selection.json", selection)
    return {"validation": validation, "root": root, "launcher": launcher,
            "parent_path": parent_path, "parent": parent, "selection": selection,
            "assets": assets, "hashes_before": hashes_before,
            "hashes_after": hashes_after, "release": release}


def build_release(output_root, machine_config_path, parent_manifest_path):
    built = _materialize_release(output_root, machine_config_path, parent_manifest_path)
    root = built["root"]
    parent_path = built["parent_path"]
    parent = built["parent"]
    release = built["release"]
    receipt = {"schema_version": 1, "status": "ready", "output_root": str(root),
               "output_root_validation": built["validation"],
               "selection_path": str(root / "selection.json"),
               "selection_rule": SELECTION_RULE, "task_count": release["task_count"],
               "parent_manifest_path": str(parent_path),
               "parent_manifest_hash": parent["manifest_hash"],
               "parent_manifest_sha256": file_hash(parent_path),
               "source_asset_hashes_before": built["hashes_before"],
               "source_asset_hashes_after": built["hashes_after"],
               "source_assets_unchanged": True, "release": release,
               "smoke_only": True, "feedback_role": "train_evolution",
               "explicit_full_benchmark": False, "formal_round_completed": False,
               "model_calls": 0, "api_calls": 0, "gpu_calls": 0}
    freeze(root / "READY.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--machine-config", required=True, type=Path)
    parser.add_argument("--parent-manifest", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build_release(args.output_root, args.machine_config, args.parent_manifest)
    except (OSError, ValueError, smoke.SmokeBlocked) as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "status": receipt["status"], "task_count": receipt["task_count"],
                      "manifest_hash": receipt["release"]["manifest_hash"],
                      "source_assets_unchanged": receipt["source_assets_unchanged"],
                      "output_root": receipt["output_root"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
