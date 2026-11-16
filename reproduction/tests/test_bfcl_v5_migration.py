"""BFCL v4 -> v5 native-ID alignment migration tests.

All fixtures are synthetic and live in temporary directories. No GPU, network, model API,
real release write or pinned-evaluator mutation is involved. The real migration is exercised
separately against the read-only v4 assets under an explicit new versioned output root.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / "RSIFlow", ROOT / "RSIFlow/runtime", ROOT / "reproduction/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import migrate_bfcl_v5 as mig
from data_protocol import public_text, text_hash
from evolution_protocol import (assert_disjoint, file_hash, fingerprint, freeze,
                                make_manifest, read)
from prepare_600_744 import prepare
from tool_validation import native_layout

CATEGORIES = ("simple", "irrelevance", "multiple")
PINNED = "c" * 40
PER_CATEGORY = 80
BFCL_VALID = 133
BFCL_INVALID = 19
OTHER_VALIDATION = {"acebench": 2, "humaneval_plus": 2, "mbpp_plus": 2,
                    "hotpotqa_dev": 2, "2wiki_dev": 2}
REMOVED_LABELS = (["exec_simple"] * 6 + ["exec_parallel"] * 3 + ["exec_multiple"] * 1
                  + ["multi_turn_composite"] * 5 + ["rest"] * 1 + ["sql"] * 1)


def _bfcl_row(native_id, index):
    row = {"question": [[{"role": "user", "content": f"official task {native_id} prompt {index}"}]],
           "function": [{"name": f"fn_{index}", "description": "fixture function",
                         "parameters": {"type": "dict", "properties": {}}}]}
    if native_id is not None:
        row["id"] = native_id
    return row


def _write_pack(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    with path.open("wb") as stream:
        for row in rows:
            line = (json.dumps(row, ensure_ascii=False) + "\n").encode()
            entries.append((stream.tell(), len(line), row))
            stream.write(line)
    return entries, file_hash(path)


def _entry(source, domain, native_id, row, pack, offset, length, pack_hash, source_hash,
           original_split="test", version="reconstructed-v1"):
    content = text_hash(public_text(row, source))
    task_id = f"{source}:{native_id}"
    return {"source": source, "domain": domain, "task_id": task_id, "native_id": str(native_id),
            "content_hash": content, "group_id": fingerprint([task_id]), "strata": {},
            "version": version, "original_split": original_split, "role": "independent_validation",
            "round_id": None, "purpose": "report_only", "exposure": "exposure_unknown", "date": None,
            "record_file": str(pack), "record_offset": offset, "record_length": length,
            "record_sha256": pack_hash, "source_sha256": source_hash}


def build_world(tmp_path, *, eligible_shortfall=False):
    """A complete miniature v4 world: official repos, packs, source manifests and a real release."""
    world = tmp_path / "world"
    world.mkdir(parents=True)
    official_repo = world / "bfcl-official"
    (official_repo / "bfcl_eval/constants").mkdir(parents=True)
    (official_repo / "bfcl_eval/constants/model_config.py").write_text("# fixture\n", encoding="utf-8")
    (official_repo / ".git").mkdir()
    (official_repo / ".git/HEAD").write_text(PINNED + "\n", encoding="utf-8")
    data_dir = official_repo / "bfcl_eval/data"
    data_dir.mkdir(parents=True)
    official_rows = {}
    per_category = 50 if eligible_shortfall else PER_CATEGORY
    for category in CATEGORIES:
        rows = [_bfcl_row(f"{category}_{i}", i) for i in range(per_category)]
        (data_dir / f"BFCL_v3_{category}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        official_rows[category] = rows
    official_ids = {c: [r["id"] for r in rows] for c, rows in official_rows.items()}

    ace_repo = world / "acebench"
    ace_data = ace_repo / "data_all/data_en"
    ace_data.mkdir(parents=True)
    (ace_data / "data_agent.json").write_text(json.dumps(
        [{"id": "agent_0", "question": "fixture ace 0"}, {"id": "agent_1", "question": "fixture ace 1"}]),
        encoding="utf-8")

    allocated = world / "assets/cohort/allocated/validation"
    allocated.mkdir(parents=True)
    valid_ids = [official_ids[c][i] for i in range(per_category) for c in CATEGORIES][:BFCL_VALID]
    id_to_row = {r["id"]: r for rows in official_rows.values() for r in rows}
    bfcl_rows = [id_to_row[i] for i in valid_ids]
    invalid_rows = []
    for index, label in enumerate(REMOVED_LABELS):
        invalid_rows.append(_bfcl_row(f"{label}_{index}", 1000 + index))
    for index in range(2):
        row = _bfcl_row(None, 2000 + index)
        row["question"] = f"legacy row without native id {index}"
        row["function"] = ""
        invalid_rows.append(row)
    bfcl_rows.extend(invalid_rows)
    bfcl_entries, bfcl_pack_hash = _write_pack(allocated / "bfcl_v3.jsonl", bfcl_rows)

    tasks = []
    for (offset, length, row), native_id in zip(bfcl_entries[:BFCL_VALID], valid_ids):
        tasks.append(_entry("bfcl_v3", "tool_use", native_id, row, allocated / "bfcl_v3.jsonl",
                            offset, length, bfcl_pack_hash, "fixture-normalized-source-hash"))
    for offset, length, row in bfcl_entries[BFCL_VALID:]:
        native_id = row.get("id") or text_hash(public_text(row, "bfcl_v3"))
        tasks.append(_entry("bfcl_v3", "tool_use", native_id, row, allocated / "bfcl_v3.jsonl",
                            offset, length, bfcl_pack_hash, "fixture-normalized-source-hash"))

    other_packs = {}
    for source, count in OTHER_VALIDATION.items():
        domain = "tool_use" if source == "acebench" else "searchqa" if source.endswith("_dev") else "code"
        rows = []
        for index in range(count):
            if source == "acebench":
                rows.append({"id": f"agent_{index}", "question": f"fixture ace {index}"})
            elif source.endswith("_dev"):
                rows.append({"_id": f"{source}-id-{index}", "question": f"fixture {source} question {index}",
                             "answer": f"answer {index}"})
            else:
                rows.append({"task_id": f"{source}/fixture_{index}", "prompt": f"def fixture_{index}():\n    ",
                             "canonical_solution": "    return 1\n", "entry_point": f"fixture_{index}"})
        entries, pack_hash = _write_pack(allocated / f"{source}.jsonl", rows)
        other_packs[source] = (entries, pack_hash)
        for (offset, length, row), index in zip(entries, range(count)):
            native_id = row.get("id") or row.get("_id") or row.get("task_id")
            tasks.append(_entry(source, domain, native_id, row, allocated / f"{source}.jsonl",
                                offset, length, pack_hash, f"fixture-{source}-source-hash"))

    validation_source = make_manifest("independent_validation", None, tasks,
                                      clean_independence_audited=False, fixed_across_rounds=True, split_seed=42)
    validation_source_path = world / "assets/cohort/validation_source_manifest.json"
    freeze(validation_source_path, validation_source)

    train_tasks = []
    train_allocated = world / "assets/cohort/allocated/train"
    train_allocated.mkdir(parents=True)
    for source, domain in (("envscaler", "tool_use"), ("deepcoder_taco", "code")):
        rows = []
        for index in range(5):
            if source == "envscaler":
                rows.append({"id": f"env-task-{index}", "canonical_env_id": f"env-{index}",
                             "task": f"fixture envscaler task {index}",
                             "question": f"fixture envscaler task {index}"})
            else:
                rows.append({"task_id": f"train/code_{index}", "prompt": f"def train_{index}():\n    ",
                             "problem": f"def train_{index}():\n    ", "interface": "stdio"})
        entries, pack_hash = _write_pack(train_allocated / f"{source}.jsonl", rows)
        for (offset, length, row), index in zip(entries, range(5)):
            native_id = row.get("id") or row.get("task_id")
            if source == "envscaler":
                native_id = f"env-{index}:{native_id}"
            entry = _entry(source, domain, native_id, row, train_allocated / f"{source}.jsonl",
                           offset, length, pack_hash, f"fixture-train-{source}-hash", original_split="train")
            entry["role"] = "train_evolution"
            entry["round_id"] = 1
            entry["strata"] = {"interface": row["interface"]} if source == "deepcoder_taco" else {}
            train_tasks.append(entry)
    train_source = make_manifest("train_evolution", 1, train_tasks, split_seed=42)
    train_source_path = world / "assets/cohort/train_source_manifest.json"
    freeze(train_source_path, train_source)

    specs_dir = world / "specs"
    specs_dir.mkdir()
    evaluators = {}
    for source in ("humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"):
        entrypoint = specs_dir / f"{source}_entry.py"
        entrypoint.write_text("# fixture official entrypoint\n", encoding="utf-8")
        entries, pack_hash = other_packs[source]
        ids_path = specs_dir / f"{source}_ids.json"
        ids = [row.get("task_id") or row.get("_id") for _, _, row in entries]
        freeze(ids_path, ids)
        evaluators[source] = {"repository": "https://example.invalid/official", "commit": "b" * 40,
                              "entrypoint": str(entrypoint), "entrypoint_sha256": file_hash(entrypoint),
                              "python_executable": sys.executable,
                              "data_path": str(allocated / f"{source}.jsonl"),
                              "data_sha256": pack_hash, "task_ids_path": str(ids_path),
                              "task_ids_sha256": file_hash(ids_path),
                              "protocol": "official_2wiki_original_v1" if source == "2wiki_dev" else "fixture_v1",
                              "release_version": "v0.1.10" if source in ("humaneval_plus", "mbpp_plus") else None,
                              "timeout_seconds": 60, "metadata": {}}
    official_specs_path = specs_dir / "report-evaluators.json"
    freeze(official_specs_path, {"evaluators": evaluators})

    retrieval = world / "retrieval/search.sqlite"
    retrieval.parent.mkdir(parents=True)
    retrieval.write_bytes(b"fixture-retrieval-index")
    freeze(retrieval.with_suffix(".sqlite.manifest.json"), {"sha256": file_hash(retrieval), "sources": {}})

    limits = {"bfcl_v3": BFCL_VALID + BFCL_INVALID, **OTHER_VALIDATION}
    settings = {"benchmark_ids": list(limits), "official_specs": str(official_specs_path),
                "validation_limits": limits,
                "tool_benchmarks": {
                    "bfcl_v3": {"repository_root": str(official_repo), "official_commit": PINNED,
                                "python_executable": sys.executable, "categories": "all_v3",
                                "version_prefix": "BFCL_v3", "data_path": str(allocated / "bfcl_v3.jsonl")},
                    "acebench": {"repository_root": str(ace_repo), "language": "en", "category": "test_all",
                                 "python_executable": sys.executable, "data_path": str(allocated / "acebench.jsonl"),
                                 "user_model": "fixture-user", "user_api_key_env": "ACE_USER_API_KEY",
                                 "user_base_url_env": "ACE_USER_BASE_URL"}}}
    settings_path = world / "validation.json"
    freeze(settings_path, settings)

    config = {"schema_version": 1, "rounds": 5, "split_seed": 42, "allocated_tasks": 10,
              "training_tasks_per_pass": 2, "train_quotas_per_round": {"envscaler": 1, "deepcoder_taco": 1},
              "validation_limits": limits, "skip_acebench": True, "evaluation_schedule": "final_only",
              "data_release": "rounds_v4", "runtime_source": "runtime",
              "frozen_data_dir": str(world / "data/rounds_v4"),
              "validation_manifest": str(world / "data/validation_v4/manifest.json"),
              "source_training_manifest": str(train_source_path),
              "source_validation_manifest": str(validation_source_path),
              "validation_config": str(settings_path), "retrieval_index": str(retrieval),
              "run_name": "fixture_v4"}
    config_path = world / "train_v4.json"
    freeze(config_path, config)
    v4_receipt = prepare(config_path)

    return {"world": world, "config_path": config_path, "config": config, "settings_path": settings_path,
            "settings": settings, "validation_source_path": validation_source_path,
            "validation_source": validation_source, "train_source_path": train_source_path,
            "train_source": train_source, "allocated": allocated, "official_repo": official_repo,
            "official_ids": official_ids, "v4_receipt": v4_receipt, "limits": limits,
            "bfcl_pack_hash": bfcl_pack_hash}


@pytest.fixture()
def world(tmp_path):
    return build_world(tmp_path)


def _migrate(world, tmp_path, **overrides):
    output = tmp_path / "v5"
    kwargs = dict(source_validation_manifest=world["validation_source_path"],
                  source_training_manifest=world["train_source_path"],
                  v4_config=world["config_path"], bfcl_spec=world["settings"]["tool_benchmarks"]["bfcl_v3"],
                  output_root=output, seed=42, expected_official_commit=PINNED)
    kwargs.update(overrides)
    return mig.migrate(**kwargs)


# --- official ID universe and removal audit ----------------------------------

def test_universe_and_removal_audit_are_exact(world):
    report = mig.analyze_bfcl(world["validation_source"], world["official_ids"])
    assert len(report["retained"]) == BFCL_VALID
    assert len(report["removed"]) == BFCL_INVALID
    assert all(t["native_id"] in {i for v in world["official_ids"].values() for i in v}
               for t in report["retained"])
    labels = Counter(mig.removed_label(t["native_id"]) for t in report["removed"])
    assert labels == Counter({**{k: REMOVED_LABELS.count(k) for k in set(REMOVED_LABELS)}, None: 2})
    assert not (set(labels) - {None}) & set(world["official_ids"])


def test_qhash_fallback_is_never_accepted_as_official_identity(world):
    report = mig.analyze_bfcl(world["validation_source"], world["official_ids"])
    qhash = [t for t in report["removed"] if mig.removed_label(t["native_id"]) is None]
    assert len(qhash) == 2
    for task in qhash:
        assert len(task["native_id"]) == 64
        assert task["native_id"] == task["content_hash"]


def test_migration_rejects_bfcl_row_without_official_id_after_migration(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v5 = read(receipt["v5"]["source_validation_manifest"])
    universe = {i for v in world["official_ids"].values() for i in v}
    bfcl = [t for t in v5["tasks"] if t["source"] == "bfcl_v3"]
    assert len(bfcl) == 152
    assert all(t["native_id"] in universe for t in bfcl)
    assert len({t["native_id"] for t in bfcl}) == 152


# --- deterministic replacement ------------------------------------------------

def test_replacement_is_deterministic_and_byte_identical(world, tmp_path):
    first = _migrate(world, tmp_path / "a")
    second = _migrate(world, tmp_path / "b")
    assert first["replacements"] == second["replacements"]
    assert first["v5"]["allocated_bfcl_pack_sha256"] == second["v5"]["allocated_bfcl_pack_sha256"]
    assert Path(first["v5"]["allocated_bfcl_pack"]).read_bytes() == Path(second["v5"]["allocated_bfcl_pack"]).read_bytes()
    # The manifest hash legitimately differs because record_file is an absolute output path;
    # every identity, hash and offset must still be identical.
    strip = lambda value: [{k: v for k, v in task.items() if k != "record_file"} for task in value["tasks"]]
    assert strip(read(first["v5"]["source_validation_manifest"])) == strip(read(second["v5"]["source_validation_manifest"]))
    assert first["selection_rule"] == second["selection_rule"]


def test_replacement_preserves_denominator_and_retained_identities(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v4 = world["validation_source"]
    v5 = read(receipt["v5"]["source_validation_manifest"])
    assert Counter(t["source"] for t in v5["tasks"]) == Counter(t["source"] for t in v4["tasks"])
    assert len(v5["tasks"]) == len(v4["tasks"]) == 162
    old_bfcl = {t["task_id"]: t for t in v4["tasks"] if t["source"] == "bfcl_v3"}
    new_bfcl = {t["task_id"]: t for t in v5["tasks"] if t["source"] == "bfcl_v3"}
    retained = set(old_bfcl) & set(new_bfcl)
    assert len(retained) == BFCL_VALID
    for task_id in retained:
        for field in ("native_id", "content_hash", "group_id", "source_sha256", "version", "exposure"):
            assert old_bfcl[task_id][field] == new_bfcl[task_id][field]
    assert len(set(old_bfcl) - set(new_bfcl)) == BFCL_INVALID
    assert len(set(new_bfcl) - set(old_bfcl)) == BFCL_INVALID


def test_other_five_sources_are_byte_and_identity_identical(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v5 = read(receipt["v5"]["source_validation_manifest"])
    for source in OTHER_VALIDATION:
        old = [t for t in world["validation_source"]["tasks"] if t["source"] == source]
        new = [t for t in v5["tasks"] if t["source"] == source]
        assert old == new
    assert receipt["invariants"]["other_sources_unchanged"] is True
    assert receipt["invariants"]["training_unchanged"] is True


def test_selection_rule_is_recorded_and_no_crosswalk_is_fabricated(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    rule = receipt["selection_rule"]
    assert rule["category_crosswalk"] == "unavailable_no_authoritative_mapping"
    assert rule["allocation"] == "largest_remainder_proportional_to_retained_categories"
    assert rule["ordering"] == "fingerprint([seed, category, native_id])"
    assert rule["seed"] == 42
    for item in receipt["replacements"]:
        assert item["category_crosswalk"] == "unavailable_no_authoritative_mapping"
        assert item["removal_reason"] in ("native_id_absent_from_pinned_official_universe",
                                          "missing_native_id_qhash_fallback")
        if item["removed_category_label"] is None:
            assert item["removal_reason"] == "missing_native_id_qhash_fallback"
        assert item["new_category"] in world["official_ids"]


def test_category_allocation_matches_retained_distribution(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    allocation = Counter(item["new_category"] for item in receipt["replacements"])
    assert sum(allocation.values()) == BFCL_INVALID
    assert receipt["selection_rule"]["allocation_counts"] == dict(sorted(allocation.items()))


# --- disjoint and fail-closed -------------------------------------------------

def test_candidates_exclude_existing_ids_content_and_groups(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v4 = world["validation_source"]["tasks"] + world["train_source"]["tasks"]
    contents = {t["content_hash"] for t in v4}
    groups = {t["group_id"] for t in v4}
    ids = {t["native_id"] for t in v4}
    for item in receipt["replacements"]:
        assert item["new_native_id"] not in ids
        assert item["new_content_hash"] not in contents
        assert item["new_group_id"] not in groups
    assert receipt["disjoint"]["checks_passed"] is True
    assert_disjoint([read(world["train_source_path"]), read(receipt["v5"]["source_validation_manifest"])])


def test_insufficient_candidates_fail_closed_without_lowering_quota(tmp_path):
    short = build_world(tmp_path, eligible_shortfall=True)
    with pytest.raises(mig.MigrationBlocked):
        _migrate(short, tmp_path / "v5")


def test_existing_output_root_is_never_overwritten(world, tmp_path):
    output = tmp_path / "v5"
    output.mkdir()
    (output / "keep.txt").write_text("existing", encoding="utf-8")
    with pytest.raises(mig.MigrationBlocked):
        _migrate(world, tmp_path)
    assert (output / "keep.txt").read_text() == "existing"


def test_tampered_v4_pack_hash_fails_closed(world, tmp_path):
    with pytest.raises(mig.MigrationBlocked):
        _migrate(world, tmp_path, expected_bfcl_pack_sha256="0" * 64)


def test_tampered_v4_manifest_hash_fails_closed(world, tmp_path):
    with pytest.raises(mig.MigrationBlocked):
        _migrate(world, tmp_path, expected_source_validation_hash="0" * 64)


def test_duplicate_official_candidate_is_rejected(world, tmp_path):
    spec = dict(world["settings"]["tool_benchmarks"]["bfcl_v3"])
    data_dir = world["official_repo"] / "bfcl_eval/data"
    target = data_dir / "BFCL_v3_simple.json"
    rows = json.loads(target.read_text())
    rows.append(dict(rows[0]))
    target.write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(ValueError):
        native_layout("bfcl_v3", spec)


# --- record bytes, offsets and hashes -----------------------------------------

def test_replacement_record_bytes_offsets_and_hashes_round_trip(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    pack = Path(receipt["v5"]["allocated_bfcl_pack"])
    v5 = read(receipt["v5"]["source_validation_manifest"])
    by_id = {t["task_id"]: t for t in v5["tasks"] if t["source"] == "bfcl_v3"}
    assert file_hash(pack) == receipt["v5"]["allocated_bfcl_pack_sha256"]
    for item in receipt["replacements"]:
        entry = by_id[item["new_task_id"]]
        assert entry["record_offset"] == item["new_record_offset"]
        assert entry["record_length"] == item["new_record_length"]
        assert entry["record_sha256"] == file_hash(pack)
        with pack.open("rb") as stream:
            stream.seek(entry["record_offset"])
            payload = stream.read(entry["record_length"])
        row = json.loads(payload)
        assert row["id"] == item["new_native_id"]
        assert text_hash(public_text(row, "bfcl_v3")) == entry["content_hash"] == item["new_content_hash"]
        assert fingerprint([entry["task_id"]]) == entry["group_id"] == item["new_group_id"]
        official_file = Path(item["official_source_file"])
        assert file_hash(official_file) == item["official_source_file_sha256"]
        assert item["official_record_kind"] == "json_array_element"
        assert item["official_record_length"] is None
        official_rows = json.loads(official_file.read_text())
        assert official_rows[item["official_record_offset"]]["id"] == item["new_native_id"]
        canonical = json.dumps(official_rows[item["official_record_offset"]], ensure_ascii=False, sort_keys=True).encode()
        assert hashlib.sha256(canonical).hexdigest() == item["official_record_sha256"]
        assert json.loads(payload) == official_rows[item["official_record_offset"]]
        assert item["record_serialization"] == "json_normalized_from_official_row"


def test_official_jsonl_lines_are_located_by_byte_offset_and_hash(tmp_path):
    """Real pinned BFCL files are JSONL despite the .json suffix; provenance must be byte-exact."""
    import hashlib

    world = build_world(tmp_path / "jsonl")
    data_dir = world["official_repo"] / "bfcl_eval/data"
    for path in sorted(data_dir.glob("BFCL_v3_*.json")):
        rows = json.loads(path.read_text())
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    receipt = _migrate(world, tmp_path / "jsonl" / "v5")
    for item in receipt["replacements"]:
        assert item["official_record_kind"] == "jsonl_line"
        assert isinstance(item["official_record_offset"], int)
        assert isinstance(item["official_record_length"], int) and item["official_record_length"] > 0
        with Path(item["official_source_file"]).open("rb") as stream:
            stream.seek(item["official_record_offset"])
            raw = stream.read(item["official_record_length"])
        assert hashlib.sha256(raw).hexdigest() == item["official_record_sha256"]
        assert json.loads(raw)["id"] == item["new_native_id"]


def test_retained_record_bytes_are_preserved_exactly(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    old_pack = world["allocated"] / "bfcl_v3.jsonl"
    new_pack = Path(receipt["v5"]["allocated_bfcl_pack"])
    v5 = read(receipt["v5"]["source_validation_manifest"])
    old_by_id = {t["task_id"]: t for t in world["validation_source"]["tasks"] if t["source"] == "bfcl_v3"}
    for entry in (t for t in v5["tasks"] if t["source"] == "bfcl_v3"):
        if entry["task_id"] not in old_by_id:
            continue
        old = old_by_id[entry["task_id"]]
        with old_pack.open("rb") as stream:
            stream.seek(old["record_offset"])
            expected = stream.read(old["record_length"])
        with new_pack.open("rb") as stream:
            stream.seek(entry["record_offset"])
            actual = stream.read(entry["record_length"])
        assert actual == expected


def test_v4_assets_are_never_modified(world, tmp_path):
    before = {p: file_hash(p) for p in world["world"].rglob("*") if p.is_file()}
    _migrate(world, tmp_path)
    after = {p: file_hash(p) for p in world["world"].rglob("*") if p.is_file()}
    assert before == after


# --- release integration ------------------------------------------------------

def test_v5_release_via_prepare_is_self_consistent_and_v4_untouched(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v4_config = read(world["config_path"])
    v5_config = {**v4_config, "data_release": "rounds_v5",
                 "frozen_data_dir": str(world["world"] / "data/rounds_v5"),
                 "validation_manifest": str(world["world"] / "data/validation_v5/manifest.json"),
                 "source_validation_manifest": receipt["v5"]["source_validation_manifest"],
                 "run_name": "fixture_v5"}
    v5_config_path = tmp_path / "train_v5.json"
    freeze(v5_config_path, v5_config)
    v4_hashes = {p: file_hash(p) for p in (world["world"] / "data/rounds_v4").rglob("*") if p.is_file()}
    v5_receipt = prepare(v5_config_path)
    assert v5_receipt["status"] == "prepared_not_executed"
    assert v5_receipt["training_tasks"] == 10
    assert v5_receipt["validation_tasks"] == 162
    assert v5_receipt["validation_by_source"]["bfcl_v3"] == 152
    assert v5_receipt["evaluation_scope"]["scored_validation_tasks"] == 160
    assert v5_receipt["evaluation_scope"]["pending_validation_tasks"] == 2
    assert v5_receipt["source_training_hash"] == world["v4_receipt"]["source_training_hash"]
    assert v5_receipt["source_validation_hash"] == receipt["v5"]["source_validation_manifest_hash"]
    assert v5_receipt["config_hash"] == fingerprint(v5_config)
    assert v4_hashes == {p: file_hash(p) for p in (world["world"] / "data/rounds_v4").rglob("*") if p.is_file()}
    v4_frozen = read(v4_config["validation_manifest"])
    v5_frozen = read(v5_config["validation_manifest"])
    identity = ("task_id", "native_id", "content_hash", "group_id", "source_sha256", "version",
                "exposure", "strata", "domain", "original_split", "purpose")

    def _bytes(pack, entry):
        with Path(pack).open("rb") as stream:
            stream.seek(entry["record_offset"])
            return stream.read(entry["record_length"])

    for source in OTHER_VALIDATION:
        old = [t for t in v4_frozen["tasks"] if t["source"] == source]
        new = [t for t in v5_frozen["tasks"] if t["source"] == source]
        assert [{k: t[k] for k in identity} for t in old] == [{k: t[k] for k in identity} for t in new]
        assert [_bytes(t["record_file"], t) for t in old] == [_bytes(t["record_file"], t) for t in new]
    v4_train = {p.name: file_hash(p) for p in (world["world"] / "data/rounds_v4").glob("B*/*.jsonl")}
    v5_train = {p.name: file_hash(p) for p in (world["world"] / "data/rounds_v5").glob("B*/*.jsonl")}
    assert v4_train == v5_train


def test_v5_bfcl_ids_are_all_accepted_by_pinned_native_layout(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    _, _, ids, _ = native_layout("bfcl_v3", world["settings"]["tool_benchmarks"]["bfcl_v3"])
    universe = {i for v in ids.values() for i in v}
    v5 = read(receipt["v5"]["source_validation_manifest"])
    selected = [t["native_id"] for t in v5["tasks"] if t["source"] == "bfcl_v3"]
    assert len(selected) == 152
    assert set(selected) <= universe
    assert len(set(selected)) == 152


def test_validation_sources_freeze_and_verify_succeed_for_v5(world, tmp_path):
    from validation_sources import freeze as freeze_sources, verify as verify_sources
    receipt = _migrate(world, tmp_path)
    settings = {**world["settings"],
                "tool_benchmarks": {**world["settings"]["tool_benchmarks"],
                                    "bfcl_v3": {**world["settings"]["tool_benchmarks"]["bfcl_v3"],
                                                "data_path": receipt["v5"]["allocated_bfcl_pack"]}}}
    settings_path = tmp_path / "validation_v5.json"
    freeze(settings_path, settings)
    snapshot = tmp_path / "sources.json"
    freeze_sources(settings, snapshot)
    verify_sources(settings, snapshot)
    assert receipt["v5"]["allocated_bfcl_pack"] in read(snapshot)["file_sha256"]


def test_deleting_replacements_instead_of_replacing_them_is_rejected(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    v5_path = Path(receipt["v5"]["source_validation_manifest"])
    value = read(v5_path)
    removed_ids = {item["new_task_id"] for item in receipt["replacements"]}
    value["tasks"] = [t for t in value["tasks"] if t["task_id"] not in removed_ids]
    broken = make_manifest(value["role"], value["round_id"], value["tasks"],
                           **{k: v for k, v in value.items()
                              if k not in {"role", "round_id", "tasks", "manifest_hash", "schema_version"}})
    assert Counter(t["source"] for t in broken["tasks"])["bfcl_v3"] == BFCL_VALID
    with pytest.raises(ValueError):
        mig.assert_v5_invariants(broken, world["limits"])


# --- pinned checkout identity (behavioral) ------------------------------------

def test_missing_expected_official_commit_is_rejected(world, tmp_path):
    with pytest.raises(mig.MigrationBlocked, match="expected_official_commit"):
        _migrate(world, tmp_path, expected_official_commit=None)
    with pytest.raises(mig.MigrationBlocked, match="expected_official_commit"):
        _migrate(world, tmp_path, expected_official_commit="")


def test_declared_spec_commit_mismatch_is_rejected(world, tmp_path):
    spec = {**world["settings"]["tool_benchmarks"]["bfcl_v3"], "official_commit": "d" * 40}
    with pytest.raises(mig.MigrationBlocked, match="sealed expectation"):
        _migrate(world, tmp_path, bfcl_spec=spec)


def test_actual_checkout_head_mismatch_is_rejected(world, tmp_path):
    (world["official_repo"] / ".git/HEAD").write_text("e" * 40 + "\n", encoding="utf-8")
    with pytest.raises(mig.MigrationBlocked, match="not the pinned commit"):
        _migrate(world, tmp_path)


def test_missing_checkout_identity_is_rejected(world, tmp_path):
    import shutil

    shutil.rmtree(world["official_repo"] / ".git")
    with pytest.raises(mig.MigrationBlocked):
        _migrate(world, tmp_path)


def test_receipt_records_verified_checkout_head(world, tmp_path):
    receipt = _migrate(world, tmp_path)
    assert receipt["official"]["commit"] == PINNED
    assert receipt["official"]["verified_checkout_head"] == PINNED
    assert receipt["official"]["head_verification"] in ("git_rev_parse", "git_head_file")


def test_pinned_head_resolver_reads_detached_ref_and_packed_refs(tmp_path):
    detached = tmp_path / "detached"
    (detached / ".git").mkdir(parents=True)
    (detached / ".git/HEAD").write_text(PINNED + "\n", encoding="utf-8")
    assert mig.pinned_checkout_commit(detached)[0] == PINNED

    loose = tmp_path / "loose"
    (loose / ".git/refs/heads").mkdir(parents=True)
    (loose / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (loose / ".git/refs/heads/main").write_text(PINNED + "\n", encoding="utf-8")
    assert mig.pinned_checkout_commit(loose)[0] == PINNED

    packed = tmp_path / "packed"
    (packed / ".git").mkdir(parents=True)
    (packed / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (packed / ".git/packed-refs").write_text("# pack-refs with: peeled fully-peeled sorted\n"
                                             + PINNED + " refs/heads/main\n", encoding="utf-8")
    assert mig.pinned_checkout_commit(packed)[0] == PINNED

    nested_repo = tmp_path / "nested"
    (nested_repo / ".git").mkdir(parents=True)
    (nested_repo / ".git/HEAD").write_text(PINNED + "\n", encoding="utf-8")
    nested = nested_repo / "package/root"
    nested.mkdir(parents=True)
    assert mig.pinned_checkout_commit(nested)[0] == PINNED


def test_assert_v5_invariants_rejects_wrong_totals_and_duplicates(world):
    value = read(world["validation_source_path"])
    with pytest.raises(ValueError):
        mig.assert_v5_invariants({**value, "tasks": value["tasks"][:-1]}, world["limits"])
    duplicated = {**value, "tasks": value["tasks"] + [value["tasks"][0]]}
    with pytest.raises(ValueError):
        mig.assert_v5_invariants(duplicated, world["limits"])
    assert mig.assert_v5_invariants(value, world["limits"]) == Counter(world["limits"])


# --- mutation verification: guards must be load-bearing -----------------------
# Mutations are applied to a TEMPORARY COPY of the tool and imported under a distinct
# module name. The real script, v4 assets and the frozen v5 releases are never modified.

MUT_EXISTING_ID = [
    ('            if native_id in excluded_ids or task_id in excluded_tasks:\n'
     '                collisions["existing_id"] += 1\n'
     '                continue\n',
     '            if False:\n'
     '                collisions["existing_id"] += 1\n'
     '                continue\n'),
    ('            if content in excluded_contents:\n'
     '                collisions["content_hash"] += 1\n'
     '                continue\n',
     '            if False:\n'
     '                collisions["content_hash"] += 1\n'
     '                continue\n'),
    ('            if group in excluded_groups:\n'
     '                collisions["group_id"] += 1\n'
     '                continue\n',
     '            if False:\n'
     '                collisions["group_id"] += 1\n'
     '                continue\n'),
]
MUT_QUOTA = (
    '    if counts != Counter(limits):\n'
    '        raise ValueError(f"v5 cohort does not match the configured per-source denominator: {dict(counts)}")\n',
    '    if False:\n'
    '        raise ValueError(f"v5 cohort does not match the configured per-source denominator: {dict(counts)}")\n',
)
MUT_IMMUTABLE = (
    '    if output_root.exists():\n'
    '        raise MigrationBlocked(f"Output root already exists; v4/v5 releases are never overwritten: {output_root}")\n',
    '    if False:\n'
    '        raise MigrationBlocked(f"Output root already exists; v4/v5 releases are never overwritten: {output_root}")\n',
)
MUT_HEAD = [
    ('    if actual_head != expected_official_commit:\n', '    if False:\n'),
    ('    if package_head != expected_official_commit:\n', '    if False:\n'),
]


def _mutant(tmp_path, name, mutation):
    pairs = mutation if isinstance(mutation, list) else [mutation]
    source = (ROOT / "reproduction/scripts/migrate_bfcl_v5.py").read_text(encoding="utf-8")
    for old, new in pairs:
        assert source.count(old) == 1, f"mutation anchor is not unique for {name}: {old!r}"
        source = source.replace(old, new)
    path = tmp_path / f"mutant_{name}.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"mutant_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _mutant_kwargs(world, output):
    return dict(source_validation_manifest=world["validation_source_path"],
                source_training_manifest=world["train_source_path"],
                v4_config=world["config_path"], bfcl_spec=world["settings"]["tool_benchmarks"]["bfcl_v3"],
                output_root=output, seed=42, expected_official_commit=PINNED)


def _assert_disjoint_invariant(receipt, world):
    rows = world["validation_source"]["tasks"] + world["train_source"]["tasks"]
    contents = {t["content_hash"] for t in rows}
    groups = {t["group_id"] for t in rows}
    ids = {t["native_id"] for t in rows}
    v5 = read(receipt["v5"]["source_validation_manifest"])
    bfcl = [t for t in v5["tasks"] if t["source"] == "bfcl_v3"]
    assert len({t["native_id"] for t in bfcl}) == 152
    for item in receipt["replacements"]:
        assert item["new_native_id"] not in ids
        assert item["new_content_hash"] not in contents
        assert item["new_group_id"] not in groups


def test_mutation_disabling_existing_id_exclusion_breaks_disjoint(world, tmp_path):
    # End-to-end selection can be an equivalent mutant for a given seed, so the guard is
    # verified at its own observable boundary: the eligible candidate pool.
    real = _migrate(world, tmp_path / "real")
    _assert_disjoint_invariant(real, world)
    spec = world["settings"]["tool_benchmarks"]["bfcl_v3"]
    exclusions = mig._exclusions(world["train_source"], world["validation_source"])
    real_pool, _, _ = mig._eligible_pool(spec, exclusions)
    real_ids = {item["native_id"] for items in real_pool.values() for item in items}
    retained = {t["native_id"] for t in mig.analyze_bfcl(world["validation_source"],
                                                        world["official_ids"])["retained"]}
    assert retained and not (retained & real_ids)
    mutant = _mutant(tmp_path, "existing_id", MUT_EXISTING_ID)
    mut_pool, _, _ = mutant._eligible_pool(spec, exclusions)
    mut_ids = {item["native_id"] for items in mut_pool.values() for item in items}
    assert retained <= mut_ids, "mutated pool must admit already-used native IDs"
    assert len(mut_ids) > len(real_ids)
    assert retained.isdisjoint(real_ids)


def test_mutation_disabling_quota_check_accepts_broken_cohort(world, tmp_path):
    value = read(world["validation_source_path"])
    broken = {**value, "tasks": value["tasks"][:-1]}
    with pytest.raises(ValueError):
        mig.assert_v5_invariants(broken, world["limits"])
    mutant = _mutant(tmp_path, "quota", MUT_QUOTA)
    assert mutant.assert_v5_invariants(broken, world["limits"]) is not None


def test_mutation_disabling_immutability_loses_controlled_refusal(world, tmp_path):
    output = tmp_path / "v5"
    output.mkdir()
    (output / "sentinel.txt").write_text("existing", encoding="utf-8")
    with pytest.raises(mig.MigrationBlocked, match="already exists"):
        _migrate(world, tmp_path)
    mutant = _mutant(tmp_path, "immutable", MUT_IMMUTABLE)
    try:
        mutant.migrate(**_mutant_kwargs(world, output))
    except RuntimeError as exc:
        assert "already exists" not in str(exc), str(exc)
    except FileExistsError:
        pass
    else:
        pytest.fail("mutated tool neither refused nor crashed on an existing output root")
    assert (output / "sentinel.txt").read_text() == "existing"


def test_mutation_disabling_head_check_accepts_wrong_checkout(world, tmp_path):
    (world["official_repo"] / ".git/HEAD").write_text("e" * 40 + "\n", encoding="utf-8")
    with pytest.raises(mig.MigrationBlocked, match="not the pinned commit"):
        _migrate(world, tmp_path / "real")
    mutant = _mutant(tmp_path, "head", MUT_HEAD)
    receipt = mutant.migrate(**_mutant_kwargs(world, tmp_path / "mutant"))
    assert receipt["official"]["commit"] == PINNED
    assert receipt["official"]["verified_checkout_head"] == "e" * 40
