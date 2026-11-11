import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "RSIFlow"), str(ROOT / "RSIFlow/runtime"), str(ROOT)]

from data_protocol import allocate, balanced_sample, materialize, public_text, scan, text_hash
from evolution_protocol import file_hash, freeze, make_manifest
from prepare_600_744 import prepare
from rsiflow.task_meta.data import TaskRecord, sha256_file
from rsiflow.task_meta.retrieval import build_search_index


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_rows(path):
    path = Path(path)
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl", ".parquet"} and ".cache" not in p.parts)
        if "bfcl_v3" in path.parts:
            files = [file for file in files if file.name.startswith("BFCL_v3_") and "possible_answer" not in file.parts and "multi_turn_func_doc" not in file.parts]
    else:
        files = [path]
    for file in files:
        if file.suffix == ".parquet":
            import pyarrow.parquet as pq
            yield from pq.read_table(file).to_pylist()
        elif file.suffix == ".jsonl":
            with file.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        yield json.loads(line)
        else:
            text = file.read_text(encoding="utf-8")
            if text.lstrip().startswith("["):
                value = json.loads(text)
                if isinstance(value, list):
                    yield from value
                elif isinstance(value, dict):
                    yield value
                else:
                    raise ValueError(f"Unsupported JSON record in {file}")
            else:
                for line in text.splitlines():
                    if line.strip():
                        yield json.loads(line)


def normalize_deepcoder(row, index):
    value = dict(row)
    tests = value.get("tests", {})
    tests = json.loads(tests) if isinstance(tests, str) else dict(tests)
    metadata = value.get("metadata", {})
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    function_name = tests.get("fn_name") or (metadata or {}).get("func_name")
    if function_name:
        tests["fn_name"] = function_name
    if not isinstance(tests.get("inputs"), list) or not isinstance(tests.get("outputs"), list):
        raise ValueError("DeepCoder tests require inputs/outputs lists")
    if len(tests["inputs"]) != len(tests["outputs"]) or not tests["inputs"]:
        raise ValueError("DeepCoder test inputs/outputs are empty or misaligned")
    value["tests"] = tests
    value["interface"] = "function" if function_name else "stdio"
    value.setdefault("id", value.get("task_id", f"deepcoder-taco-{index}"))
    value.setdefault("public_tests", [])
    return value


def normalize_rows(source, rows):
    normalized = []
    for index, row in enumerate(rows):
        if source == "deepcoder_taco":
            row = normalize_deepcoder(row, index)
        if source == "envscaler" and not row.get("checklist_with_func"):
            raise ValueError("EnvScaler row lacks official checklist_with_func")
        if source in {"hotpotqa", "2wiki"} and "context" not in row:
            raise ValueError(f"{source} row lacks context")
        if source in {"hotpotqa", "2wiki"} and "_id" not in row and "id" in row:
            row = {**row, "_id": row["id"]}
        normalized.append(row)
    return normalized


def deduplicate_native_ids(source, rows):
    seen = set()
    unique = []
    dropped = []
    for row in rows:
        identity = row.get("task_id", row.get("id", row.get("_id")))
        if identity is not None and identity in seen:
            dropped.append(row)
            continue
        if identity is not None:
            seen.add(identity)
        unique.append(row)
    return unique, dropped


def source_spec(source, kind, path, original_split):
    domains = {"envscaler": "tool_use", "deepcoder_taco": "code", "bfcl_v3": "tool_use", "acebench": "tool_use",
               "humaneval_plus": "code", "mbpp_plus": "code", "hotpotqa": "searchqa", "2wiki": "searchqa",
               "hotpotqa_dev": "searchqa", "2wiki_dev": "searchqa"}
    return {
        "source": source,
        "domain": domains[source],
        "kind": kind,
        "path": str(path),
        "original_split": original_split,
        "version": "reconstructed-v1",
        "stratify": ["difficulty", "type", "level", "interface"],
    }


def record_from_item(item):
    path = Path(item["path"])
    if item["length"] is None:
        rows = json.loads(path.read_text())
        payload = rows[item["offset"]]
    else:
        with path.open("rb") as stream:
            stream.seek(item["offset"])
            payload = json.loads(stream.read(item["length"]))
    prompt = public_text(payload, item["source"])
    return TaskRecord(item["task_id"], item["domain"], item["source"], "evolve_train", prompt, payload,
                      item["source_sha256"], item["content_hash"])


def pack_allocated_manifests(training_manifests, validation_manifest, destination):
    destination = Path(destination)
    packed_training = [materialize(manifest, destination / f"B{index}") for index, manifest in enumerate(training_manifests, 1)]
    packed_validation = materialize(validation_manifest, destination / "validation")
    all_training = [dict(row, round_id=1) for manifest in packed_training for row in manifest["tasks"]]
    source_training = make_manifest("train_evolution", 1, all_training, split_seed=42, reconstruction="public_protocol_v1")
    return source_training, packed_validation


def inspect_source(path):
    path = Path(path)
    rows = list(read_rows(path))
    if path.is_dir():
        files = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix in {".json", ".jsonl", ".parquet"} and ".cache" not in p.parts)
    else:
        files = [path]
    result = {"path": str(path), "records": len(rows), "keys": sorted(rows[0]) if rows else [],
              "files": [{"path": str(file), "sha256": sha256_file(file)} for file in files]}
    if path.is_file():
        result["sha256"] = sha256_file(path)
    if rows and "checklist_with_func" in rows[0]:
        result["checklist_records"] = sum("checklist_with_func" in row for row in rows)
    if rows and "tests" in rows[0]:
        interfaces = {}
        for row in rows:
            tests = row.get("tests", {})
            tests = json.loads(tests) if isinstance(tests, str) else tests
            metadata = row.get("metadata", {})
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            fn = tests.get("fn_name") or (metadata or {}).get("func_name")
            key = "function" if fn else "stdio"
            interfaces[key] = interfaces.get(key, 0) + 1
        result["interfaces"] = interfaces
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--envscaler", required=True, type=Path)
    parser.add_argument("--env-metadata", required=True, type=Path)
    parser.add_argument("--deepcoder", required=True, type=Path)
    parser.add_argument("--hotpot-train", required=True, type=Path)
    parser.add_argument("--hotpot-dev", required=True, type=Path)
    parser.add_argument("--twowiki-train", required=True, type=Path)
    parser.add_argument("--twowiki-dev", required=True, type=Path)
    parser.add_argument("--bfcl", required=True, type=Path)
    parser.add_argument("--ace", required=True, type=Path)
    parser.add_argument("--humaneval", required=True, type=Path)
    parser.add_argument("--mbpp", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retrieval", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite cohort directory: {output}")
    output.mkdir(parents=True)
    normalized_root = output / "normalized"
    training_paths = {
        "envscaler": normalized_root / "train/envscaler.jsonl",
        "deepcoder_taco": normalized_root / "train/deepcoder_taco.jsonl",
        "hotpotqa": normalized_root / "train/hotpotqa.jsonl",
        "2wiki": normalized_root / "train/2wiki.jsonl",
    }
    validation_paths = {
        "bfcl_v3": normalized_root / "validation/bfcl_v3.jsonl",
        "acebench": normalized_root / "validation/acebench.jsonl",
        "humaneval_plus": normalized_root / "validation/humaneval_plus.jsonl",
        "mbpp_plus": normalized_root / "validation/mbpp_plus.jsonl",
        "hotpotqa_dev": normalized_root / "validation/hotpotqa_dev.jsonl",
        "2wiki_dev": normalized_root / "validation/2wiki_dev.jsonl",
    }
    source_rows = {
        "envscaler": args.envscaler,
        "deepcoder_taco": args.deepcoder,
        "hotpotqa": args.hotpot_train,
        "2wiki": args.twowiki_train,
        "bfcl_v3": args.bfcl,
        "acebench": args.ace,
        "humaneval_plus": args.humaneval,
        "mbpp_plus": args.mbpp,
        "hotpotqa_dev": args.hotpot_dev,
        "2wiki_dev": args.twowiki_dev,
    }
    duplicate_variants = {}
    for source, destination in {**training_paths, **validation_paths}.items():
        rows = normalize_rows(source, read_rows(source_rows[source]))
        rows, dropped = deduplicate_native_ids(source, rows)
        duplicate_variants[source] = dropped
        if not rows:
            raise ValueError(f"No records for {source}")
        write_jsonl(destination, rows)
    environments = list(read_rows(args.env_metadata))
    write_jsonl(output / "envscaler/train/environments.jsonl", environments)
    write_jsonl(output / "envscaler/heldout/environments.jsonl", environments)
    specs = []
    for source, path in training_paths.items():
        specs.append(source_spec(source, "train", path, "train"))
    for source, path in validation_paths.items():
        specs.append(source_spec(source, "evaluation", path, "dev" if source.endswith("_dev") else "test"))
    items, source_audit = scan(specs, config["grouping"])
    code = [row for row in items if row["source"] == "deepcoder_taco"]
    stdio = balanced_sample([row for row in code if row["strata"].get("interface") == "stdio"], 600, config["split_seed"])
    function = balanced_sample([row for row in code if row["strata"].get("interface") == "function"], 400, config["split_seed"])
    code_ids = {row["task_id"] for row in stdio + function}
    selected_training = [row for row in items if row["kind"] == "train" and (row["source"] != "deepcoder_taco" or row["task_id"] in code_ids)]
    selected_items = selected_training + [row for row in items if row["kind"] == "evaluation"]
    manifests, quarantine = allocate(selected_items, config, {"coverage": {}, "events": []})
    training_manifests = manifests[:5]
    if len(training_manifests) != 5:
        raise ValueError("Expected five training manifests")
    source_training, source_validation = pack_allocated_manifests(training_manifests, manifests[5], output / "allocated")
    all_training = source_training["tasks"]
    if len(all_training) != 3000:
        raise ValueError(f"Expected 3000 training tasks, got {len(all_training)}")
    freeze(output / "train_source_manifest.json", source_training)
    freeze(output / "validation_source_manifest.json", source_validation)
    search_tasks = [record_from_item(row) for manifest in training_manifests for row in manifest["tasks"] if row["domain"] == "searchqa"]
    build_search_index(search_tasks, args.retrieval, data_manifest_hash=source_training["manifest_hash"])
    config["retrieval_index"] = str(args.retrieval.resolve())
    config["source_training_manifest"] = str(output / "train_source_manifest.json")
    config["source_validation_manifest"] = str(output / "validation_source_manifest.json")
    write_json(args.config, config)
    ready = prepare(args.config)
    summary = {
        "status": "cohort_reconstructed",
        "source_audit": source_audit,
        "ready": ready,
        "quarantine_records": len(quarantine),
        "code_interface": {"stdio": len(stdio), "function": len(function)},
        "duplicate_variants_dropped": {source: len(rows) for source, rows in duplicate_variants.items()},
        "retrieval_index": str(args.retrieval.resolve()),
        "source_training_manifest": str(output / "train_source_manifest.json"),
        "source_validation_manifest": str(output / "validation_source_manifest.json"),
    }
    write_json(output / "build_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
