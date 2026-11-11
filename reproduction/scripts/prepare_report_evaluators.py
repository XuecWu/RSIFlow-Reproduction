import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "RSIFlow/runtime"))

from rsiflow.task_meta.reporting import OfficialEvaluatorSpec


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare(specs_path, manifest_path, output_dir, output_config):
    specs_path, manifest_path = Path(specs_path), Path(manifest_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    config = json.loads(specs_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    tasks = manifest["tasks"]
    counts = {}
    for identifier, value in config["evaluators"].items():
        value["benchmark"] = identifier
        rows = [row for row in tasks if row["source"] == identifier]
        if not rows:
            raise ValueError(f"Frozen manifest has no tasks for {identifier}")
        if identifier in {"hotpotqa_dev", "2wiki_dev"}:
            gold = []
            for row in rows:
                path = Path(row["record_file"])
                offset, length = row["record_offset"], row["record_length"]
                with path.open("rb") as stream:
                    stream.seek(offset)
                    native = json.loads(stream.read(length))
                native["_id"] = native.get("_id", native.get("id"))
                for key in ("context", "supporting_facts", "evidences"):
                    if isinstance(native.get(key), str):
                        native[key] = json.loads(native[key])
                if isinstance(native.get("supporting_facts"), dict):
                    facts = native["supporting_facts"]
                    native["supporting_facts"] = list(zip(facts["title"], facts["sent_id"], strict=True))
                gold.append(native)
            ids = [str(row["_id"]) for row in gold]
            data_path = output_dir / f"{identifier}.json"
            write_json(data_path, gold)
            value["data_path"] = str(data_path.resolve())
            value["data_sha256"] = digest(data_path)
        else:
            ids = [str(row["native_id"]) for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate official IDs for {identifier}")
        ids_path = output_dir / f"{identifier}_ids.json"
        write_json(ids_path, ids)
        value["task_ids_path"] = str(ids_path.resolve())
        value["task_ids_sha256"] = digest(ids_path)
        counts[identifier] = len(OfficialEvaluatorSpec(**value).validate())
    write_json(output_config, config)
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--specs", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--output-config", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.specs, args.manifest, args.output_dir, args.output_config), indent=2))


if __name__ == "__main__":
    main()
