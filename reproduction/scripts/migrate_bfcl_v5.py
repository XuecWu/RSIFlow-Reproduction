"""Deterministic BFCL v4 -> v5 native-ID alignment migration.

Single-purpose migration tool. It never modifies v4 assets, the pinned official evaluator or
its data, and never lowers the BFCL denominator. Replacements are selected from the pinned
official BFCL-v3 universe by a fixed, published rule before any model output exists.

The removed v4 entries carry BFCL-v4 style labels (exec_*, rest_*, sql_*, multi_turn_composite_*)
or no native ID at all. None of those labels is a pinned v3 category, so there is no authoritative
crosswalk; this tool records that fact instead of fabricating one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from data_protocol import public_text, records, text_hash
from evolution_protocol import (file_hash, fingerprint, freeze, make_manifest, manifest, read)
from tool_validation import native_layout

GENERATOR = "migrate_bfcl_v5.py"
RELEASE = "v5"
CROSSWALK = "unavailable_no_authoritative_mapping"
ALLOCATION = "largest_remainder_proportional_to_retained_categories"
ORDERING = "fingerprint([seed, category, native_id])"
REASON_ABSENT = "native_id_absent_from_pinned_official_universe"
REASON_QHASH = "missing_native_id_qhash_fallback"


class MigrationBlocked(RuntimeError):
    """Evidence or candidate shortfall; never silently lower a quota or overwrite a release."""


def removed_label(native_id):
    """Source-version label of a removed entry, or None when the row had no native ID."""
    text = str(native_id)
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return None
    return text.rsplit("_", 1)[0] if "_" in text else text


def analyze_bfcl(source_manifest, official_ids):
    universe = {str(item) for values in official_ids.values() for item in values}
    bfcl = [task for task in source_manifest["tasks"] if task["source"] == "bfcl_v3"]
    retained = [task for task in bfcl if str(task["native_id"]) in universe]
    removed = [task for task in bfcl if str(task["native_id"]) not in universe]
    return {"retained": retained, "removed": removed, "bfcl_total": len(bfcl),
            "official_id_count": len(universe)}


def assert_v5_invariants(value, limits):
    counts = Counter(task["source"] for task in value["tasks"])
    if counts != Counter(limits):
        raise ValueError(f"v5 cohort does not match the configured per-source denominator: {dict(counts)}")
    ids = [task["task_id"] for task in value["tasks"]]
    if len(ids) != len(set(ids)):
        raise ValueError("v5 cohort contains duplicate task identities")
    return counts


def _exclusions(train_source, validation_source):
    rows = train_source["tasks"] + validation_source["tasks"]
    return ({str(task["native_id"]) for task in rows},
            {task["task_id"] for task in rows},
            {task["content_hash"] for task in rows},
            {task["group_id"] for task in rows})


def _eligible_pool(bfcl_spec, exclusions):
    excluded_ids, excluded_tasks, excluded_contents, excluded_groups = exclusions
    _, _, ids, paths = native_layout("bfcl_v3", bfcl_spec)
    categories = sorted(ids)
    pool = {category: [] for category in categories}
    collisions = Counter()
    for category, path in zip(categories, paths):
        digest = file_hash(path)
        for offset, length, row in records(path):
            if not isinstance(row, dict) or row.get("id") in (None, ""):
                collisions["incomplete_record"] += 1
                continue
            native_id = str(row["id"])
            task_id = "bfcl_v3:" + native_id
            try:
                content = text_hash(public_text(row, "bfcl_v3"))
            except ValueError:
                collisions["incomplete_record"] += 1
                continue
            if native_id in excluded_ids or task_id in excluded_tasks:
                collisions["existing_id"] += 1
                continue
            if content in excluded_contents:
                collisions["content_hash"] += 1
                continue
            group = fingerprint([task_id])
            if group in excluded_groups:
                collisions["group_id"] += 1
                continue
            pool[category].append({"native_id": native_id, "task_id": task_id, "row": row,
                                   "content_hash": content, "group_id": group,
                                   "official_source_file": str(Path(path).resolve()),
                                   "official_source_file_sha256": digest,
                                   "official_record_offset": offset,
                                   "official_record_length": length,
                                   "official_record_kind": "json_array_element" if length is None else "jsonl_line",
                                   "official_record_sha256": _official_record_sha256(path, offset, length, row)})
    return pool, categories, collisions


def _official_record_sha256(path, offset, length, row):
    """Hash the exact official record bytes (JSONL line) or the canonical array element."""
    if length is None:
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
    else:
        with Path(path).open("rb") as stream:
            stream.seek(offset)
            payload = stream.read(length)
    return hashlib.sha256(payload).hexdigest()


def _allocate_slots(retained, removed_count, pool):
    """Largest-remainder allocation proportional to the retained category distribution."""
    distribution = Counter(task["strata_category"] for task in retained)
    total = sum(distribution.values())
    if not total:
        raise MigrationBlocked("No retained BFCL tasks; cannot derive a category distribution")
    exact = {category: distribution[category] / total * removed_count for category in distribution}
    counts = {category: int(value) for category, value in exact.items()}
    remainder = removed_count - sum(counts.values())
    order = sorted(distribution, key=lambda category: (-(exact[category] - counts[category]), category))
    for category in order[:remainder]:
        counts[category] += 1
    for category, needed in counts.items():
        if needed > len(pool.get(category, [])):
            raise MigrationBlocked(
                f"Only {len(pool.get(category, []))} eligible official candidates in category {category}, "
                f"but {needed} are required; the BFCL denominator must not be lowered")
    if sum(counts.values()) != removed_count:
        raise MigrationBlocked("Category allocation does not cover the replacement count")
    return counts


def _select_candidates(pool, counts, seed):
    selected = []
    for category in sorted(counts):
        needed = counts[category]
        if not needed:
            continue
        ordered = sorted(pool[category], key=lambda item: fingerprint([seed, category, item["native_id"]]))
        for item in ordered[:needed]:
            selected.append({**item, "category": category,
                             "selection_key": fingerprint([seed, category, item["native_id"]])})
    if len(selected) != sum(counts.values()):
        raise MigrationBlocked("Deterministic candidate selection produced an unexpected count")
    if len({item["native_id"] for item in selected}) != len(selected):
        raise MigrationBlocked("Deterministic candidate selection produced duplicate official IDs")
    return selected


def _read_record(entry):
    path = Path(entry["record_file"])
    with path.open("rb") as stream:
        stream.seek(entry["record_offset"])
        payload = stream.read(entry["record_length"])
    json.loads(payload)
    return payload


def pinned_checkout_commit(repository_root):
    """Resolve the real checkout HEAD; never trust a directory name or a declared value alone.

    `git rev-parse` is preferred. When git refuses the checkout (e.g. cross-uid ownership), the
    same commit is read directly from HEAD/ref/packed-refs. No git trust or global config is
    ever modified.
    """
    root = Path(repository_root).resolve()
    git_dir = next((path / ".git" for path in (root, *root.parents) if (path / ".git").exists()), None)
    if git_dir is None:
        raise MigrationBlocked(f"No git checkout identity found above {root}")
    repo = git_dir.parent
    completed = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                               capture_output=True, text=True, check=False)
    if completed.returncode == 0 and completed.stdout.strip():
        return completed.stdout.strip(), "git_rev_parse"
    if git_dir.is_file():
        target = git_dir.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
        git_dir = Path(target) if Path(target).is_absolute() else (repo / target).resolve()
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        raise MigrationBlocked(f"Cannot read checkout HEAD: {head_path}")
    head = head_path.read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head, "git_head_file"
    ref = head[5:]
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip(), "git_head_file"
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split()[0], "git_head_file"
    raise MigrationBlocked(f"Cannot resolve {ref} in {git_dir}")


def migrate(*, source_validation_manifest, source_training_manifest, v4_config, bfcl_spec,
            output_root, seed=42, expected_bfcl_pack_sha256=None,
            expected_source_validation_hash=None, expected_official_commit=None):
    """Write a NEW immutable v5 BFCL pack, source validation manifest and migration receipt."""
    if not expected_official_commit:
        raise MigrationBlocked("expected_official_commit is required; a migration must seal the pinned "
                               "official commit and verify the real checkout against it")
    output_root = Path(output_root)
    if output_root.exists():
        raise MigrationBlocked(f"Output root already exists; v4/v5 releases are never overwritten: {output_root}")
    validation = manifest(source_validation_manifest, "independent_validation", None)
    training = manifest(source_training_manifest, "train_evolution", 1)
    if expected_source_validation_hash and validation["manifest_hash"] != expected_source_validation_hash:
        raise MigrationBlocked("v4 source validation manifest hash does not match the sealed expectation")
    config = read(v4_config)
    limits = config["validation_limits"]
    if Counter(task["source"] for task in validation["tasks"]) != Counter(limits):
        raise MigrationBlocked("v4 validation cohort does not match the configured denominator")

    universe_repo, _, official_ids, official_paths = native_layout("bfcl_v3", bfcl_spec)
    if bfcl_spec.get("official_commit") != expected_official_commit:
        raise MigrationBlocked("Pinned official BFCL commit does not match the sealed expectation")
    actual_head, head_method = pinned_checkout_commit(bfcl_spec["repository_root"])
    if actual_head != expected_official_commit:
        raise MigrationBlocked(f"Actual BFCL checkout HEAD {actual_head} is not the pinned commit "
                               f"{expected_official_commit}")
    package_head, _ = pinned_checkout_commit(universe_repo)
    if package_head != expected_official_commit:
        raise MigrationBlocked(f"BFCL package root HEAD {package_head} is not the pinned commit "
                               f"{expected_official_commit}")
    report = analyze_bfcl(validation, official_ids)
    retained, removed = report["retained"], report["removed"]
    if not removed:
        raise MigrationBlocked("No incompatible BFCL entries found; a v5 migration is not justified")
    if len(retained) + len(removed) != limits["bfcl_v3"]:
        raise MigrationBlocked("BFCL denominator would change; migration refuses to lower the quota")

    bfcl_entries = [task for task in validation["tasks"] if task["source"] == "bfcl_v3"]
    pack_paths = {task["record_file"] for task in bfcl_entries}
    if len(pack_paths) != 1:
        raise MigrationBlocked("v4 BFCL entries span multiple packs; migration requires a single sealed pack")
    old_pack = Path(next(iter(pack_paths)))
    old_pack_hash = file_hash(old_pack)
    if any(task["record_sha256"] != old_pack_hash for task in bfcl_entries):
        raise MigrationBlocked("v4 BFCL pack hash does not match the sealed manifest entries")
    if expected_bfcl_pack_sha256 and old_pack_hash != expected_bfcl_pack_sha256:
        raise MigrationBlocked("v4 BFCL pack hash does not match the sealed expectation")

    category_of = {str(item): category for category, values in official_ids.items() for item in values}
    for task in retained:
        task["strata_category"] = category_of[str(task["native_id"])]
    pool, categories, collisions = _eligible_pool(bfcl_spec, _exclusions(training, validation))
    counts = _allocate_slots(retained, len(removed), pool)
    selected = _select_candidates(pool, counts, seed)

    output_root.mkdir(parents=True)
    allocated = output_root / "allocated/validation"
    allocated.mkdir(parents=True)
    new_pack = allocated / "bfcl_v3.jsonl"
    replacements = []
    chosen = iter(selected)
    removed_by_id = {task["task_id"]: task for task in removed}
    new_entries = []
    with new_pack.open("xb") as stream:
        for entry in bfcl_entries:
            if entry["task_id"] not in removed_by_id:
                payload = _read_record(entry)
                offset = stream.tell()
                stream.write(payload)
                new_entries.append({**entry, "record_file": str(new_pack.resolve()),
                                    "record_offset": offset, "record_length": len(payload)})
                continue
            candidate = next(chosen)
            row = candidate["row"]
            payload = (json.dumps(row, ensure_ascii=False) + "\n").encode()
            offset = stream.tell()
            stream.write(payload)
            label = removed_label(entry["native_id"])
            reason = REASON_QHASH if label is None else REASON_ABSENT
            new_entries.append({
                "source": "bfcl_v3", "domain": entry["domain"], "task_id": candidate["task_id"],
                "native_id": candidate["native_id"], "content_hash": candidate["content_hash"],
                "group_id": candidate["group_id"], "strata": {},
                "version": f"pinned-official-{str(bfcl_spec.get('official_commit', 'unknown'))[:8]}",
                "original_split": entry["original_split"], "role": entry["role"],
                "round_id": entry["round_id"], "purpose": entry["purpose"],
                "exposure": entry["exposure"], "date": None,
                "record_file": str(new_pack.resolve()), "record_offset": offset,
                "record_length": len(payload), "record_sha256": "",
                "source_sha256": candidate["official_source_file_sha256"],
            })
            replacements.append({
                "old_task_id": entry["task_id"], "old_native_id": entry["native_id"],
                "old_content_hash": entry["content_hash"], "old_group_id": entry["group_id"],
                "old_record_sha256": entry["record_sha256"], "old_source_sha256": entry["source_sha256"],
                "old_record_offset": entry["record_offset"], "old_record_length": entry["record_length"],
                "removal_reason": reason, "removed_category_label": label,
                "category_crosswalk": CROSSWALK,
                "new_task_id": candidate["task_id"], "new_native_id": candidate["native_id"],
                "new_category": candidate["category"], "new_content_hash": candidate["content_hash"],
                "new_group_id": candidate["group_id"], "new_record_offset": offset,
                "new_record_length": len(payload), "new_record_sha256": "",
                "official_source_file": candidate["official_source_file"],
                "official_source_file_sha256": candidate["official_source_file_sha256"],
                "official_record_offset": candidate["official_record_offset"],
                "official_record_length": candidate["official_record_length"],
                "official_record_kind": candidate["official_record_kind"],
                "official_record_sha256": candidate["official_record_sha256"],
                "record_serialization": "json_normalized_from_official_row",
                "selection_key": candidate["selection_key"],
            })
    new_pack_hash = file_hash(new_pack)
    for entry in new_entries:
        entry["record_sha256"] = new_pack_hash
    for item in replacements:
        item["new_record_sha256"] = new_pack_hash

    other = [task for task in validation["tasks"] if task["source"] != "bfcl_v3"]
    tasks = []
    bfcl_iter = iter(new_entries)
    for task in validation["tasks"]:
        tasks.append(next(bfcl_iter) if task["source"] == "bfcl_v3" else task)
    metadata = {key: value for key, value in validation.items()
                if key not in {"role", "round_id", "tasks", "manifest_hash", "schema_version"}}
    v5_source = make_manifest("independent_validation", None, tasks, **metadata)
    assert_v5_invariants(v5_source, limits)
    v5_source_path = output_root / "validation_source_manifest.json"
    freeze(v5_source_path, v5_source)

    train_hash_before = training["manifest_hash"]
    train_hash_after = manifest(source_training_manifest, "train_evolution", 1)["manifest_hash"]
    if train_hash_before != train_hash_after:
        raise MigrationBlocked("Training source manifest changed during migration")
    other_unchanged = other == [task for task in v5_source["tasks"] if task["source"] != "bfcl_v3"]
    if not other_unchanged:
        raise MigrationBlocked("Non-BFCL validation entries were altered")

    universe = {str(item) for values in official_ids.values() for item in values}
    v5_bfcl = [task for task in v5_source["tasks"] if task["source"] == "bfcl_v3"]
    if not all(str(task["native_id"]) in universe for task in v5_bfcl):
        raise MigrationBlocked("v5 BFCL native IDs are not all inside the pinned official universe")
    if len({task["native_id"] for task in v5_bfcl}) != limits["bfcl_v3"]:
        raise MigrationBlocked("v5 BFCL native IDs are not unique at the configured denominator")

    receipt = {
        "schema_version": 1, "generator": GENERATOR, "release": RELEASE,
        "status": "migrated_not_executed",
        "selection_rule": {
            "category_crosswalk": CROSSWALK,
            "category_crosswalk_note": "Removed labels are BFCL-v4 categories or absent; none is a pinned "
                                       "v3 category, so no per-item category equivalence is claimed or invented.",
            "allocation": ALLOCATION, "allocation_counts": dict(sorted(counts.items())),
            "ordering": ORDERING, "seed": seed,
            "assignment": "selected candidates are assigned to removed slots in v4 manifest order",
            "candidate_filters": ["pinned official universe membership", "complete record with native id",
                                  "not an existing train/validation native_id or task_id",
                                  "no content_hash collision", "no group_id collision", "no duplicate candidate"],
            "model_results_used": False,
        },
        "parent_v4": {
            "source_validation_manifest": str(Path(source_validation_manifest).resolve()),
            "source_validation_manifest_hash": validation["manifest_hash"],
            "source_training_manifest": str(Path(source_training_manifest).resolve()),
            "source_training_manifest_hash": training["manifest_hash"],
            "allocated_bfcl_pack": str(old_pack.resolve()),
            "allocated_bfcl_pack_sha256": old_pack_hash,
            "config_hash": fingerprint(config),
            "data_release": config.get("data_release"),
        },
        "official": {
            "repository_root": str(universe_repo), "commit": bfcl_spec.get("official_commit"),
            "verified_checkout_head": actual_head, "head_verification": head_method,
            "package_root_head": package_head,
            "category_count": len(categories), "id_count": len(universe),
            "data_file_sha256": {str(Path(path).resolve()): file_hash(path) for path in official_paths},
        },
        "denominator": {"bfcl_v3": limits["bfcl_v3"], "retained": len(retained), "replaced": len(removed)},
        "removed_label_counts": dict(sorted(Counter(str(removed_label(t["native_id"])) for t in removed).items())),
        "disjoint": {"eligible_pool_total": sum(len(items) for items in pool.values()),
                     "eligible_per_category": {c: len(pool[c]) for c in categories},
                     "excluded_collisions": dict(collisions), "checks_passed": True},
        "replacements": replacements,
        "invariants": {"other_sources_unchanged": other_unchanged, "training_unchanged": True,
                       "validation_total": len(v5_source["tasks"]),
                       "validation_by_source": dict(sorted(Counter(t["source"] for t in v5_source["tasks"]).items())),
                       "bfcl_ids_all_official": True, "bfcl_ids_unique": True},
        "exposure": {"clean_independence_audited": validation.get("clean_independence_audited", False),
                     "exposure": sorted({task["exposure"] for task in v5_source["tasks"]}),
                     "note": "v5 is a pinned-official-native-ID-aligned protocol reconstruction, not the "
                             "authors' original queue; public-source reconstruction and unknown exposure "
                             "limitations are unchanged and must stay public."},
        "v5": {"allocated_bfcl_pack": str(new_pack.resolve()),
               "allocated_bfcl_pack_sha256": new_pack_hash,
               "source_validation_manifest": str(v5_source_path.resolve()),
               "source_validation_manifest_hash": v5_source["manifest_hash"]},
        "runner_protocol_note": "evaluate_tool accepts a bounded 50-task daily queue and, via the formal-report "
                                "branch, exactly 152 frozen BFCL tasks when formal_report_evaluation=true, "
                                "purpose=report_only and evaluation_scope binds the manifest/snapshot/config "
                                "hashes. This migration closes the v5 data/ID/queue preflight only; it does not "
                                "execute scoring, produce a BFCL score, or unlock a complete 694 evaluation.",
    }
    freeze(output_root / "bfcl_v5_migration_receipt.json", receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-validation-manifest", required=True, type=Path)
    parser.add_argument("--source-training-manifest", required=True, type=Path)
    parser.add_argument("--v4-config", required=True, type=Path)
    parser.add_argument("--validation-settings", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--expected-bfcl-pack-sha256")
    parser.add_argument("--expected-source-validation-hash")
    parser.add_argument("--expected-official-commit", required=True,
                        help="sealed pinned BFCL commit; the real checkout HEAD must match it")
    args = parser.parse_args(argv)
    settings = read(args.validation_settings)
    try:
        receipt = migrate(source_validation_manifest=args.source_validation_manifest,
                          source_training_manifest=args.source_training_manifest,
                          v4_config=args.v4_config,
                          bfcl_spec=settings["tool_benchmarks"]["bfcl_v3"],
                          output_root=args.output_root, seed=args.seed,
                          expected_bfcl_pack_sha256=args.expected_bfcl_pack_sha256,
                          expected_source_validation_hash=args.expected_source_validation_hash,
                          expected_official_commit=args.expected_official_commit)
    except MigrationBlocked as exc:
        print(json.dumps({"ok": False, "status": "blocked", "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, "status": receipt["status"], "release": receipt["release"],
                      "retained": receipt["denominator"]["retained"],
                      "replaced": receipt["denominator"]["replaced"],
                      "bfcl_denominator": receipt["denominator"]["bfcl_v3"],
                      "output_root": str(args.output_root),
                      "source_validation_manifest_hash": receipt["v5"]["source_validation_manifest_hash"],
                      "allocated_bfcl_pack_sha256": receipt["v5"]["allocated_bfcl_pack_sha256"]},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
