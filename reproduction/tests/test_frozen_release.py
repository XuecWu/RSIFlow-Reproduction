import json
from collections import Counter
from pathlib import Path

from evolution_protocol import assert_disjoint, fingerprint, read
from evaluation_manifest import select_specs
from rsiflow.task_meta.round_evolution import RoundStore


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "reproduction/configs/train_4b.json"


def test_machine_config_names_actual_release_and_backbone_sockets():
    config = read(CONFIG)
    assert config["data_release"] == "rounds_3000_5x600_v4"
    assert Path(config["frozen_data_dir"]).name == config["data_release"]
    assert Path(config["validation_manifest"]).parent.name == "validation_744_v4"
    assert "4b" in Path(config["meta_worker_socket"]).name
    assert "4b" in Path(config["meta_relay_socket"]).name


def test_real_frozen_release_has_complete_counts_and_partial_scope(tmp_path):
    config = read(CONFIG)
    data = Path(config["frozen_data_dir"])
    receipt = read(data / "READY.json")
    assert receipt["config_hash"] == fingerprint(read(data / "protocol.json"))
    assert receipt["training_tasks"] == 3000
    assert receipt["validation_tasks"] == 744
    assert receipt["evaluation_scope"] == {
        "status": "partial_ace_pending_audit",
        "skip_acebench": True,
        "frozen_validation_tasks": 744,
        "scored_validation_tasks": 694,
        "pending_validation_tasks": 50,
    }
    store = RoundStore(data / "tasks.sqlite")
    seen, content = set(), set()
    for number in range(1, 6):
        store.select_round(number)
        rows = store.current["tasks"]
        assert len(rows) == 600
        assert Counter(row["source"] for row in rows) == config["train_quotas_per_round"]
        assert Counter(row["strata"]["interface"] for row in rows if row["domain"] == "code") == {"stdio": 120, "function": 80}
        assert not seen.intersection(row["task_id"] for row in rows)
        assert not content.intersection(row["content_hash"] for row in rows)
        seen.update(row["task_id"] for row in rows)
        content.update(row["content_hash"] for row in rows)
        assert all(store._task(row).task_id == row["task_id"] for row in rows)
    training = read(config["source_training_manifest"])
    validation = read(config["validation_manifest"])
    assert seen == {row["task_id"] for row in training["tasks"]}
    assert_disjoint([training, validation])
    assert len(validation["tasks"]) == 744
    assert not validation["clean_independence_audited"]
    assert {row["exposure"] for row in validation["tasks"]} == {"exposure_unknown"}
    settings = read(ROOT / config["validation_config"])
    selected, specs = select_specs(settings, config["validation_manifest"], "independent_validation", tmp_path / "inputs")
    assert len(selected["tasks"]) == 744
    assert set(specs) == {"humaneval_plus", "mbpp_plus", "hotpotqa_dev", "2wiki_dev"}
    assert sum(row["source"] != "acebench" for row in selected["tasks"]) == 694
