# BFCL v5 数据协议迁移报告

结论：**v5 已生成并冻结，数据侧 native-ID 缺口已关闭；未执行任何模型/评估/五轮/SFT/SCO。不宣称最终 BFCL 152 评分或完整 694 已解锁。**

- 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`（分支 `reproduction/4b-preflight`）
- 依据：`reproduction/audit/reviews/bfcl-v5-brief.md`、`bfcl-v5-design.md`（推荐方案 B）
- 真实资产位于主工作树 `/data/wuxuecheng/Hierarchical_level/RSI-Flow`，本次只读核实后使用；设计报告中「worktree 相对路径不存在」确实只是工作树视图问题，**不是资产缺失**。
- 未 commit/push，未联网，未读取凭据，未启动模型/API/SCO/五轮/SFT，未修改 v4 任何文件、official evaluator/data、正式结果、JSONL 账本、生产 `validate.py` 或五轮运行配置 `train_4b.json`。

## 1. 交付物

| 文件/目录 | 类型 | 说明 |
| --- | --- | --- |
| `reproduction/scripts/migrate_bfcl_v5.py` | 新建（worktree，tracked） | 单用途确定性迁移工具，452 行（fix round 1 后） |
| `reproduction/tests/test_bfcl_v5_migration.py` | 新建（worktree，tracked） | 33 项离线回归（含 4 组 mutation 验证），临时 fixture，无 GPU/网络/模型 |
| `reproduction/tests/test_frozen_release.py` | 修改（worktree，tracked） | 把 BFCL 目录名断言改为真实 pinned 契约 |
| `reproduction/configs/train_4b_v5.json` | 新建（worktree，tracked） | 仅指向 v5；未覆盖 `train_4b.json` |
| `reproduction/configs/validation_744_v5.json` | 新建（worktree，tracked） | 相对本工作树 v4 仅改 BFCL `data_path`；ACE `data_path` 沿用本工作树 v4（与主工作树 v4 的旧占位路径不同，详见 §10.3）；evaluator/ACE 记录/quota 不变 |
| `reproduction/REPRODUCTION.md` | 修改（worktree，tracked） | 新增 §9 记录 v5 与边界 |
| `reproduction/audit/reviews/bfcl-v5-report.md` | 新建 | 本报告 |
| `assets/cohort-v5/bfcl-v5-migration-r1/` | 新建（主工作树，Git 忽略 `assets/cohort*/`） | 首次迁移：pack + source manifest + receipt |
| `assets/cohort-v5/bfcl-v5-migration-r2/` | 新建（主工作树，Git 忽略） | provenance 字段修正后的重 issued receipt，数据字节与 r1 完全相同 |
| `assets/cohort-v5/bfcl-v5-migration-r3/` | 新建（主工作树，Git 忽略） | fix round 1 强制 pinned HEAD 后的 dry-run，数据字节与 r1/r2 完全相同 |
| `RSIFlow/runtime/data/rounds_3000_5x600_v5/` | 新建（主工作树，Git 忽略 `**/data/`） | 由现有 `prepare_600_744.py` 生成的完整 3000 release |
| `RSIFlow/runtime/data/validation_744_v5/` | 新建（主工作树，Git 忽略） | 744 冻结验证 release |
| `reproduction/logs/bfcl-v5-dryrun-r1/`、`reproduction/logs/bfcl-v5-verify-r1/` | 新建（主工作树，Git 忽略 `**/logs/`） | dry-run 与验证快照 |

所有真实生成路径在写入前均经 `test ! -e` 确认不存在；未覆盖任何既有目录。

## 2. TDD：RED → GREEN

### 2.1 RED（先写测试并实际运行）

```text
# 新测试文件首次运行（迁移脚本尚不存在）
reproduction/tests/test_bfcl_v5_migration.py:22: in <module>
    import migrate_bfcl_v5 as mig
E   ModuleNotFoundError: No module named 'migrate_bfcl_v5'
1 error in 0.37s
```

实现后首轮 `1 failed, 20 errors`、次轮 `1 failed, 20 errors`、第三轮 `14 failed, 7 passed`、第四轮 `3 failed, 18 passed`，逐项定位为 **fixture/期望缺陷**并修正（非实现语义错误）：

1. fixture 训练集 deepcoder 两种 interface 导致每轮配额不均 → 统一 interface。
2. `native_index` 的 `_prompt` 要求 tool_use 有 `task`、code 有 `problem` 字符串 → fixture 补齐。
3. fixture 官方池按类别顺序取前 133 会占满 `simple` 类别，使比例分配不可行 → 改为跨类别交错取样；shortfall 变体改为整体缩小池使其真正不可行。
4. 确定性测试误比 manifest hash（含绝对 `record_file` 路径，必然不同）→ 改为比 pack 字节 + 剔除路径后的任务列表 + selection_rule。
5. frozen 非 BFCL 源比较误含 `record_file/offset/sha`（repack 会重写）→ 改为比身份字段 + 记录字节。
6. 「删行代替替换」负例误用 old task_id（v5 中已是 new id）→ 改用 new task_id。

### 2.2 旧断言的失败证据（先保留，再修正）

```text
# 修正前 reproduction 全套
E   AssertionError: assert 'bfcl-ea13468' in '/data/.../assets/evaluators/bfcl-official/berkeley-function-call-leaderboard'
reproduction/tests/test_frozen_release.py:42
FAILED reproduction/tests/test_frozen_release.py::test_bfcl_evaluator_uses_pinned_source_and_isolated_python
1 failed, 32 passed in 47.78s
```

修正后该测试不再断言目录名字符串，改为核对**真实契约**：`git rev-parse HEAD`（git 因跨 uid 拒绝时，等价地从 checkout 的 `.git/HEAD`/ref/packed-refs 直读同一 commit，**未修改任何 git trust 配置**）必须等于 `ea13468e4423454d0c213704fb87cf7cb3990433`；`native_layout` 必须给出 17 个 `BFCL_v3_*` 数据文件、4441 个唯一 ID、类别与文件名一致；isolated 解释器 `rsiflow-bfcl/bin/python` 必须存在；archive SHA256 保持 `c6c3b8ff…`。实测解析出的 commit 与 pinned 值一致。

### 2.3 GREEN

```text
reproduction/tests/test_bfcl_v5_migration.py            22 passed in 3.29s
reproduction/tests（全套，含修正后的 pinned 契约测试）   55 passed in 50.67s
RSIFlow 数据协议/发布相关 8 个文件                       61 passed, 2 skipped in 8.51s
```

RSIFlow 相关集合：`test_600_744_protocol`、`test_three_round_protocol`、`test_five_round_extension`、`test_skip_ace_protocol`、`test_release_integration`、`test_tool_validation_preflight`、`test_evalplus_isolated`、`test_task_adapter`。2 个 skip 为既有环境性跳过，非本次引入。

## 3. 真实资产核实与设计修正

- pinned official universe：17 类、**4441** 个唯一 native ID；checkout commit 实测 `ea13468e4423454d0c213704fb87cf7cb3990433`。
- v4 BFCL 152 条中 **133** 条 native ID 在 official universe 内，**19** 条不在，与给定事实一致（本次独立重算，非沿用）。
- 19 条构成：`exec_simple`×6、`exec_parallel`×3、`exec_multiple`×1、`multi_turn_composite`×5、`rest`×1、`sql`×1，以及 **2 条原始行缺 `id`**（`question` 为纯字符串、`function` 为空）而退化为 qhash。
- **这些标签无一是 pinned v3 的 17 个类别**，因此不存在权威 crosswalk。回执显式记 `category_crosswalk = unavailable_no_authoritative_mapping`，**未伪造任何逐条类别等价**；2 条 qhash 项记 `removed_category_label = null`、`removal_reason = missing_native_id_qhash_fallback`。
- v4 manifest 的 BFCL `strata` 全为 `{}`，故类别信息来自 official 文件名与 native ID 本身，不来自 manifest。
- **官方数据文件虽以 `.json` 结尾，实为 JSONL**（首字节 `{`）。`data_protocol.records()` 因此产出**字节偏移 + 行长度**，而非数组下标。
- 训练 source manifest 中 BFCL 条目为 **0**，但 disjoint 检查仍覆盖全部 3000 训练条目的 `native_id/task_id/content_hash/group_id`。

### 3.1 本次发现并修正的真实缺陷（provenance 字段）

首版脚本把 `records()` 的第一个产出值命名为 `official_record_index`，在 JSONL 语义下它其实是**字节偏移**，且未记录行长度与记录哈希——不满足 brief 对「official source file/offset/record hash」的要求。

修正：回执改为记录 `official_record_offset`、`official_record_length`、`official_record_kind`（`jsonl_line` / `json_array_element`）、`official_record_sha256`（JSONL 为该行**原始字节**的 SHA256；数组元素为规范化 JSON 的 SHA256），并记 `record_serialization = json_normalized_from_official_row`。新增测试同时覆盖数组与 JSONL 两种官方格式。

因 r1 已 immutable 冻结，修正后重跑生成 **r2**，并实测：

- r2 与 r1 的 BFCL pack **逐字节相同**（同为 `6e7d81f919c15c1c84f2bf046cee611cbf2e85a6262376c1f6a40aa371fe55bc`）；
- 两者 source manifest 的任务列表在剔除 `record_file` 后**完全相同**（hash 差异仅来自绝对路径）；
- r2 的 19 条 official provenance 逐条按 offset/length 读回原始字节，SHA256 与 `id` **全部复核通过**（0 失败）。

因此 **v5 release 由 r1 的 source manifest 生成，其数据与 r2 完全一致**；r2 是 provenance 字段完整的权威机器可读回执。配置与 release 保持指向 r1，以维持 `READY.source_validation_hash` 自洽（改指 r2 会因路径不同而改变 manifest hash，触发 prepare 的不可覆盖保护）。r1、r2 均保留，未删除、未覆盖。

另记：19 条中 18 条的新 pack 记录与官方原始行**逐字节相同**；`multi_turn_miss_param_79` 官方行 1484 字节、规范化后 1481 字节（JSON 序列化差异），`json.loads` 后对象完全相同，回执同时给出官方记录哈希与新 pack 记录哈希，差异可审计。

## 4. 选择规则（发布前固定、完全确定、不看模型结果）

1. 保留 133 个已在 pinned official universe 中的项，身份/内容/字节不变。
2. 候选池 = official 全集，排除：现有 train/validation 的 `native_id` 与 `task_id`、`content_hash` 冲突、`group_id` 冲突、缺 `id` 或无可用公开 prompt 的不完整记录、重复候选。实测排除 **133 个既有 ID** 与 **38 个内容冲突**，合格池 **4270** 条。
3. 19 个名额按**保留项类别分布的最大余数比例分配**（`largest_remainder_proportional_to_retained_categories`）：`irrelevance 1、java 1、live_irrelevance 4、live_multiple 3、live_simple 1、multi_turn_base 1、multi_turn_long_context 1、multi_turn_miss_func 1、multi_turn_miss_param 1、multiple 1、parallel 1、parallel_multiple 1、simple 2`。
4. 类内按 `fingerprint([seed=42, category, native_id])` 升序取前 N；候选按 v4 manifest 顺序填入被移除槽位。
5. 任一类别候选不足即 `MigrationBlocked`，**绝不降低 152 分母**；输出目录已存在即拒绝。
6. `model_results_used = false`：选择全程不接触任何模型输出或分数。

## 5. 19 条替换清单（r2 权威回执）

`off_idx`/`off_len` 为官方 JSONL 字节偏移与长度；`new_off`/`new_len` 为新 pack 内偏移与长度；哈希为 SHA256 前 12 位（完整值见回执）。

| # | old native_id | 移除标签 | new native_id | new category | off_idx | off_len | official rec sha | new_off | new_len | new content sha | selection key |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `5021fea268d0898dfe982cd0aceffaf316124c7bf296b2bdd0827859b9841dff` | null（qhash） | `irrelevance_116` | irrelevance | 78389 | 501 | `1891d346fcc2` | 0 | 501 | `92e18d07cc94` | `0102342622d4` |
| 2 | `64066c33c16b6f3c730fa2953cdbc6a859a10ac635a1385543aa06036c81f4dd` | null（qhash） | `java_17` | java | 14218 | 859 | `d3c251d3078d` | 501 | 859 | `0ed9a8fc6107` | `0036b43856b0` |
| 3 | `exec_multiple_40` | exec_multiple | `live_irrelevance_710-234-0` | live_irrelevance | 1756976 | 656 | `962490810ad1` | 1360 | 656 | `385765a61f89` | `000b1e2c1edc` |
| 4 | `exec_parallel_17` | exec_parallel | `live_irrelevance_591-186-0` | live_irrelevance | 1258539 | 1847 | `cb7630932743` | 2016 | 1847 | `1d32eca2dfc8` | `002162a94b7a` |
| 5 | `exec_parallel_21` | exec_parallel | `live_irrelevance_785-294-0` | live_irrelevance | 1971328 | 3733 | `365b6e0b50a1` | 3863 | 3733 | `fd550882be84` | `01b093b94aee` |
| 6 | `exec_parallel_41` | exec_parallel | `live_irrelevance_679-219-1` | live_irrelevance | 1633635 | 2123 | `b932a4569eda` | 7596 | 2123 | `7bf73508e798` | `02997fc320ef` |
| 7 | `exec_simple_23` | exec_simple | `live_multiple_298-130-7` | live_multiple | 1109717 | 1911 | `b7ca18d91166` | 9719 | 1911 | `0c7d6b7d869f` | `0093c0d33865` |
| 8 | `exec_simple_27` | exec_simple | `live_multiple_596-158-2` | live_multiple | 2253524 | 2078 | `5b7453a3880e` | 11630 | 2078 | `69e80ab9c6c7` | `00ef54f8345a` |
| 9 | `exec_simple_46` | exec_simple | `live_multiple_173-71-2` | live_multiple | 667639 | 5176 | `3187e43a9228` | 13708 | 5176 | `6511cac0b541` | `010584a15db9` |
| 10 | `exec_simple_57` | exec_simple | `live_simple_167-99-1` | live_simple | 175334 | 754 | `83c181fdd382` | 18884 | 754 | `2c149688bffe` | `00db8752c93a` |
| 11 | `exec_simple_73` | exec_simple | `multi_turn_base_77` | multi_turn_base | 115912 | 3458 | `cd7263767cf5` | 19638 | 3458 | `6bec258d689d` | `01232aa09471` |
| 12 | `exec_simple_94` | exec_simple | `multi_turn_long_context_198` | multi_turn_long_context | 389312 | 2401 | `8b8c3ac0937e` | 23096 | 2401 | `fa5ed4314dc7` | `00007f6750fc` |
| 13 | `multi_turn_composite_100` | multi_turn_composite | `multi_turn_miss_func_136` | multi_turn_miss_func | 261567 | 2437 | `4d245dc1808f` | 201282 | 2437 | `ddade7db3a20` | `010905340f0e` |
| 14 | `multi_turn_composite_18` | multi_turn_composite | `multi_turn_miss_param_79` | multi_turn_miss_param | 127544 | 1484 | `08341abaa7fd` | 203719 | 1481 | `a5c95c670468` | `0014ce20a3fe` |
| 15 | `multi_turn_composite_180` | multi_turn_composite | `multiple_75` | multiple | 101968 | 1392 | `b781178ace11` | 205200 | 1392 | `f47875189c02` | `00b149aba939` |
| 16 | `multi_turn_composite_4` | multi_turn_composite | `parallel_10` | parallel | 6754 | 548 | `0dc66ec016dc` | 206592 | 548 | `3ac221d06168` | `0430d7b9fc00` |
| 17 | `multi_turn_composite_70` | multi_turn_composite | `parallel_multiple_97` | parallel_multiple | 136919 | 2462 | `d366c24f96af` | 207140 | 2462 | `7f7994f1add1` | `00569c3b76b9` |
| 18 | `rest_53` | rest | `simple_58` | simple | 39158 | 704 | `e6b8514817d1` | 274742 | 704 | `952341e0b7d8` | `00c30286952a` |
| 19 | `sql_90` | sql | `simple_147` | simple | 104555 | 611 | `838c131ccce1` | 286712 | 611 | `4aa8015426b4` | `018d5910b874` |

每条在回执中另含完整 64 位 `old/new content_hash`、`old/new group_id`、`old_record_sha256`、`old_source_sha256`、`old_record_offset/length`、`new_record_sha256`、`official_source_file` 绝对路径与 `official_source_file_sha256`、`removal_reason`、`category_crosswalk`、`selection_key`。

## 6. 哈希与 release 回执

父 v4（只读，迁移前后一致）：

- `assets/cohort/allocated/validation/bfcl_v3.jsonl` = `eb4b048a22be82b1dab8d41e260ab58044ba561040fe107e6b7fa78c96954730`
- `assets/cohort/validation_source_manifest.json` 的 `manifest_hash` = `927a8606270b8b59036cb94a740426eec76490547815aaee52dbdd76d9278617`
- `assets/cohort/train_source_manifest.json` 的 `manifest_hash` = `78863d14e2fcfb06246b02af5854fb76776ccfe3077852988050ebb3db91228f`
- v4 `READY.json` = `07f07c61bfbd6763111d3f513b9d151cf1392f8f7ca7ea305d291e50d0443585`
- v4 frozen `validation_744_v4/manifest.json` = `884f59d99a7a298e0c3bbf38d68f1e37df7c75559e8e6b9a342d9b873511eab4`
- v4 release 共 30 个文件，迁移后哈希全部未变

v5：

- allocated BFCL pack（r1 = r2，逐字节相同）= `6e7d81f919c15c1c84f2bf046cee611cbf2e85a6262376c1f6a40aa371fe55bc`
- r1 source validation `manifest_hash` = `90ffe339a89c136cafafcd68a5c7cdeae24bfe7cf1559b515e9b0aa2a8cf7f32`（release 使用）
- r2 source validation `manifest_hash` = `d4f3107b11af49ca22b3198f715a9b089261cd9233f807838dcd8f2288ff958a`（provenance 完整回执；任务列表与 r1 除路径外相同）
- r2 receipt 文件 SHA256 = `56424caf0b59e1db3cc94c66685c652eff14156ff083aa53dbf5d247144878a3`
- v5 `READY.json` = `193702603a66d67707ccb846a6909a36ec9da1b58cfb0e8ebc9dd83d0fbe49d5`
- v5 frozen `validation_744_v5/manifest.json` = `cd31242733d20d3497bb194b89a8e4be69fe28efa22d53c482e806438688e432`
- v5 frozen `bfcl_v3.jsonl` = `6e7d81f919c15c1c84f2bf046cee611cbf2e85a6262376c1f6a40aa371fe55bc`
- v5 `config_hash` = `320000983c426c1109acccaacd221510cdc3c006fdcaa2f7d953376f5f2b0f2a`，与 `protocol.json` 的 fingerprint 一致
- v5 `source_training_hash` = `78863d14…`（与 v4 相同）

v5 READY 关键字段：`status = prepared_not_executed`、`rounds = 5`、`training_tasks = 3000`、`tasks_per_round = [600×5]`、`validation_tasks = 744`、`validation_by_source = {bfcl_v3 152, acebench 50, humaneval_plus 164, mbpp_plus 178, hotpotqa_dev 100, 2wiki_dev 100}`、`evaluation_scope = {status: partial_ace_pending_audit, skip_acebench: true, frozen_validation_tasks: 744, scored_validation_tasks: 694, pending_validation_tasks: 50}`、`evaluation_schedule = final_only`。**标准 READY schema 未扩展**；release=v5、migration receipt hash、父 v4 hashes 与 exposure 限制均放在独立 migration receipt 与本报告。

## 7. 真实资产离线验证（9 项，全部通过）

1. **v4 不可变**：v4 release 30 个文件哈希不变；v4 BFCL pack、source manifests、READY、frozen validation manifest 哈希均与迁移前一致。
2. **训练逐字节不变**：v5 与 v4 的 20 个 `B*/*.jsonl` 训练 pack 哈希集合完全相同；`source_training_hash` 不变。
3. **其他五源不变**：ACE 50、HumanEval+ 164、MBPP+ 178、HotpotQA 100、2Wiki 100 的身份字段（task_id/native_id/content_hash/group_id/source_sha256/version/exposure/strata/domain/original_split/purpose）与**记录字节**均与 v4 完全一致。
4. **BFCL 152 全官方且唯一**：152 个 native ID 全部属于 pinned official 4441 集合，无重复。
5. **轮次协议**：5×600；每轮 source 配额等于 `train_quotas_per_round`；Code 每轮 120 STDIO + 80 function；跨轮 task_id 与 content_hash 均不重复；训练 ID 集合等于 source training manifest；`assert_disjoint(train, v5 validation)` 通过。
6. **select_specs**：744 条，specs 为 4 个 official（humaneval_plus/mbpp_plus/hotpotqa_dev/2wiki_dev），非 ACE = **694**。
7. **runner queue 可接受**：`validate_tool_selection("bfcl_v3", frozen, 152 tasks)` 返回 **152**（需 `formal_report_evaluation=true`、`purpose=report_only`、`evaluation_scope` 与 frozen 的 manifest/snapshot/config 哈希一致）；selected IDs 与 frozen 队列完全一致且为 official 子集，覆盖 **14** 个官方类别、152 个 ID。该检查使用**合成 binding**，仅验证队列契约，**未运行模型、未产生分数**。
8. **validation_sources**：对 v5 settings `freeze` + `verify` 成功，快照含 **284** 个文件哈希，新 BFCL pack 已被 pin。
9. **READY 自洽**：`config_hash == fingerprint(protocol.json)`，各源计数与 694/744/50 口径正确。

补充：两树的 `prepare_600_744.py`、`data_protocol.py`、`evolution_protocol.py`、`evaluation_manifest.py`、`validation_sources.py`、`extend_rounds.py`、`validation_skip.py` **逐字节相同**，仅 `tool_validation.py` 工作树更新（含 `validate_tool_selection` 的 152 形式化分支与 `attribution_from_receipts`）。因此用主工作树代码生成的 release 与工作树代码语义一致；队列验证使用工作树代码。

## 8. 自审

- **范围**：只改/新建 brief 点名的文件；v4、official evaluator/data、正式结果、JSONL 账本、`validate.py`、`train_4b.json` 均未触碰。真实数据产物只写主工作树 Git 忽略的新版本目录，写入前 `test ! -e`。
- **不降分母**：BFCL 始终 152，ACE 始终 50 pending，744 frozen / 694 scored 不变；候选不足即 fail closed，测试覆盖。
- **不伪造身份**：qhash 两条明确记为缺 native ID 的移除审计，未把内容哈希当官方 ID；无权威 crosswalk 的事实显式入回执，未编造类别等价；未用结果顺序或问题相似度匹配。
- **确定性**：同 seed 两次迁移的 replacement 清单、pack 字节、剔除路径后的 manifest 完全一致；真实 r1/r2 亦逐字节一致。
- **可审计**：每条替换含 old/new 身份与哈希、官方文件绝对路径 + 文件哈希 + 记录偏移/长度/记录哈希、选择键与规则；19 条 provenance 已逐条读回原始字节复核通过。
- **暴露与口径**：`clean_independence_audited=false`、`exposure_unknown` 原样保留；v5 是「经 pinned BFCL v3 native-ID 对齐的同协议重构」，不是作者原队列精确复现；替换改变 held-out 样本，v5 状态须重标，不得把 v4 BFCL 证据当 v5 结果。
- **未执行**：无模型/API/SCO/五轮/SFT；无 BFCL 分数、无 694 结果、无 A0 baseline。`status` 一律 `prepared_not_executed` / `migrated_not_executed`。
- **诚实记录缺陷**：provenance 字段命名/缺失缺陷、1 条 3 字节序列化差异、fixture 与测试期望的 6 处修正、旧目录名断言的失败证据，均如实记录，未隐瞒。
- **权限**：git 因跨 uid 拒绝时未修改任何 trust/全局配置，改用等价的 `.git/HEAD` 直读；`git diff --check` 在本工作树仍被跨 uid 阻断，需属主用户/主 agent 补跑（与既有记录一致）。

## 9. 遗留与下一步（需 controller 决策）

1. **是否把五轮运行配置切换到 v5**：`train_4b.json` 与 v4 目录本次未改；切换需另行授权，并同步 PROGRESS/公开 JSON/审计。
2. **正式 BFCL 152 评分与完整 694 仍未解锁**：本次只关闭数据/ID/queue 预检。真实评分仍需四卡 Qwen 服务、隔离执行、ACE 用户模拟凭据（50 条 pending）与独立 preflight review；`evaluate_tool` 的 152 形式化分支虽已存在，但**未在本次运行**。
3. **v5 暴露重标**：替换后的 held-out 样本需重新记录 exposure；若不接受该污染，应另行批准独立 smoke 数据。
4. **r1/r2 并存**：r1 为 release 源、r2 为 provenance 完整回执，数据字节相同；后续如需单一权威链，可由 controller 决定以 r2 重建新版本 release（本次未做，避免覆盖既有不可变产物）。
5. `git diff --check` 与完整 RSIFlow 套件由属主用户/主 agent 复跑确认。

## 10. Fix round 1（回应 `bfcl-v5-review.md`）

Review 结论为 Spec FAIL / 质量 CHANGES，两项 Important 与三项 Minor 均已处理。**未修改正式 v4/v5 数据、未重建或覆盖 v5 release、未改生产代码。**

### 10.1 Important 1 — 行为级 TDD RED 不足（如实承认，不伪造历史）

**承认**：本报告 §2.1 记录的原初 RED **只有** `ModuleNotFoundError: No module named 'migrate_bfcl_v5'`（模块尚不存在），其后各轮失败均被归因为 fixture/期望缺陷。**原初没有展示迁移协议断言在实现前因行为错误而失败**，因此不满足 brief 对严格行为级 TDD RED 的要求。该历史事实保留原样，不追改、不补造当时的输出。

本轮补充**可失败的行为回归**并做 **mutation 验证**：

新增行为测试（先写、先实跑 RED）：

- `test_missing_expected_official_commit_is_rejected`（None 与空串均拒绝）
- `test_declared_spec_commit_mismatch_is_rejected`
- `test_actual_checkout_head_mismatch_is_rejected`
- `test_missing_checkout_identity_is_rejected`（删除 `.git` 后拒绝）
- `test_receipt_records_verified_checkout_head`
- `test_pinned_head_resolver_reads_detached_ref_and_packed_refs`（detached / loose ref / packed-refs / 嵌套 package root 四种形态）
- `test_assert_v5_invariants_rejects_wrong_totals_and_duplicates`

**行为级 RED 实测**（实现 pinned-HEAD 强制之前）：

```text
33 项中：7 failed, 26 passed in 2.95s
FAILED test_missing_expected_official_commit_is_rejected
FAILED test_actual_checkout_head_mismatch_is_rejected
FAILED test_missing_checkout_identity_is_rejected
FAILED test_receipt_records_verified_checkout_head
FAILED test_pinned_head_resolver_reads_detached_ref_and_packed_refs
FAILED test_mutation_disabling_existing_id_exclusion_breaks_disjoint
FAILED test_mutation_disabling_head_check_accepts_wrong_checkout
```

这些失败是**协议行为缺失**（可省略 expected commit、不核验真实 HEAD、回执无 verified head、resolver 不存在），不是 import 或 fixture 问题。

**Mutation 验证**（在**临时副本**上做文本变异并以独立模块名导入；真实脚本、v4 资产、已冻结 v5 release 全程未被修改，无需"恢复"因为从未改动原件）：

| 变异 | 关闭的守卫 | 真实模块行为 | 变异模块行为（RED 证据） |
| --- | --- | --- | --- |
| `MUT_EXISTING_ID`（3 处） | 候选池的 native_id/task_id、content_hash、group_id 排除 | 合格池 4270，与 133 个保留 ID **完全不相交** | 合格池**纳入全部 133 个已用 ID**，池严格变大 → disjoint 断言失败 |
| `MUT_QUOTA` | `assert_v5_invariants` 的逐源分母校验 | 少 1 条的 cohort 抛 `ValueError` | **接受**残缺 cohort（返回 counts 不抛） |
| `MUT_IMMUTABLE` | `output_root.exists()` 拒绝 | 抛 `MigrationBlocked("…already exists…")`，sentinel 文件保留 | 失去受控拒绝，改为 `FileExistsError` 崩溃；sentinel 仍未被覆盖 |
| `MUT_HEAD`（2 处） | 真实 HEAD 与 package root HEAD 双重校验 | HEAD 为 `e*40` 时抛 `MigrationBlocked("…not the pinned commit…")` | **接受错误 checkout** 并产出自称 pinned 的回执（`verified_checkout_head = e*40`） |

过程中如实记录两次 mutation 设计修正：

1. `MUT_HEAD` 最初只关闭一处 HEAD 校验，变异模块仍被**第二处 package-root 校验**拦住 → 说明双重校验确为载荷性；改为同时关闭两处后才观察到"接受错误 checkout"的 RED。
2. `MUT_EXISTING_ID` 最初只关闭 native_id 排除，端到端选择在该 seed 下为**等价变异**（content_hash 与 group_id 过滤同样覆盖保留项，且选择结果未变）→ 改为关闭整个候选 disjoint 排除块，并把断言放在守卫自身的可观测边界（合格池集合），得到确定性 RED。

对应变异测试：`test_mutation_disabling_existing_id_exclusion_breaks_disjoint`、`test_mutation_disabling_quota_check_accepts_broken_cohort`、`test_mutation_disabling_immutability_loses_controlled_refusal`、`test_mutation_disabling_head_check_accepts_wrong_checkout`。四者均同时断言**真实模块守住不变量**且**变异模块违反它**。

### 10.2 Important 2 — 强制真实 checkout pinned HEAD

实现（`reproduction/scripts/migrate_bfcl_v5.py`）：

- `expected_official_commit` 改为**必填**：缺失/空值即 `MigrationBlocked`；CLI `--expected-official-commit` 设为 `required=True`（实测缺失时 argparse 拒绝且**不创建任何输出目录**）。
- 新增 `pinned_checkout_commit(repository_root)`：向上查找 `.git`（支持 gitfile 形式），优先 `git -C <repo> rev-parse HEAD`；git 拒绝时只读解析 `.git/HEAD`（detached）、loose ref、`packed-refs`，返回 `(commit, method)`，`method ∈ {git_rev_parse, git_head_file}`。**不修改任何 git trust/全局配置。**
- 迁移同时核验三者一致：spec 声明的 `official_commit`、`repository_root` 的真实 HEAD、`native_layout` 解析出的 package root HEAD；任一不符即 `MigrationBlocked`。
- 回执 `official` 段新增 `verified_checkout_head`、`head_verification`、`package_root_head`。
- 所有调用与 fixtures 已更新：测试 fixture 建立 `.git/HEAD`（`PINNED = "c"*40`），`_migrate`/`_mutant_kwargs` 均传 `expected_official_commit`。

**真实资产 r3 dry-run**（全新目录 `assets/cohort-v5/bfcl-v5-migration-r3`，写入前 `test ! -e`）：

```json
{"allocated_bfcl_pack_sha256": "6e7d81f919c15c1c84f2bf046cee611cbf2e85a6262376c1f6a40aa371fe55bc",
 "bfcl_denominator": 152, "ok": true, "replaced": 19, "retained": 133,
 "source_validation_manifest_hash": "c9ff1e2352f87eee0c9b7139a50b95a62691fb047aed5f83d098af575b504d11",
 "status": "migrated_not_executed"}
```

- `verified_checkout_head = ea13468e4423454d0c213704fb87cf7cb3990433`，`head_verification = git_head_file`（git 因跨 uid 属主拒绝，走只读 `.git/HEAD` 回退），`package_root_head` 同值。
- **r3 pack 字节与 r1/r2 完全相同**（同 `6e7d81f9…`）；三者 source manifest 任务列表在剔除绝对 `record_file` 后**完全相同**；r3 的 19 条 replacements 与 r2 **逐字段相同**；合格池仍 4270、133 保留 / 19 替换 / 152 分母。
- r3 receipt 文件 SHA256 = `0ebd4eb469bb88aceaaa5fc4ce4ca89f06dcd865be4e79a10073b55051cff9f5`。
- **未重建、未覆盖 v5 release**：`rounds_3000_5x600_v5/READY.json` 仍为 `193702603a66d677…`、`validation_744_v5/manifest.json` 仍为 `cd31242733d20d34…`；v4 BFCL pack 仍为 `eb4b048a…`、v4 READY 仍为 `07f07c61…`。
- 负例实测：省略 `--expected-official-commit` 时 CLI 直接报 `the following arguments are required: --expected-official-commit`，且 `assets/cohort-v5/bfcl-v5-migration-should-not-exist` **未被创建**。

### 10.3 Minor 修正

1. **ACE `data_path` 说明不准确** → 已在 `REPRODUCTION.md` §9 与本报告 §1 表格如实区分基线：
   - 主工作树 v4：`assets/cohort/acebench/test.jsonl`（旧占位路径，与 BFCL 同属早前 data_path 缺口）
   - 本工作树 v4：`assets/cohort/allocated/validation/acebench.jsonl`
   - v5：与本工作树 v4 **相同**（`v5 == wt_v4: True`），与主工作树 v4 **不同**（`v5 == main_v4: False`）
   - 两种基线下 ACE 的 50 条 validation 记录**身份与字节都与 v4 相同**，未替换任何 ACE 样本；这是路径差异而非样本变更。原文"仅改 BFCL data_path"已限定为"相对本工作树 v4"。
2. **测试计数过时** → `REPRODUCTION.md` 的"21 项"与报告原"22 项"统一更新为 **33 项**（本轮新增 11 项：7 项 pinned-HEAD/不变量行为测试 + 4 项 mutation 验证）。
3. **receipt runner note 过时** → 脚本的 `runner_protocol_note` 改为准确描述：`evaluate_tool` 接受 50 条日常有界队列，并**经 formal-report 分支**在 `formal_report_evaluation=true`、`purpose=report_only` 且 `evaluation_scope` 绑定 manifest/snapshot/config 哈希时接受**恰好 152** 条冻结 BFCL 任务；本迁移只关闭 v5 数据/ID/queue 预检，不执行评分、不产生 BFCL 分数、不解锁完整 694。r3 回执已携带新文案；**r1/r2 回执为不可变历史，仍保留旧文案**，此处如实标注而不追改。

### 10.4 本轮测试与验证

```text
reproduction/tests/test_bfcl_v5_migration.py    33 passed in 3.74s
reproduction/tests（全套）                      66 passed in 50.12s
```

- mutation RED 证据见 §10.1（行为级 RED `7 failed, 26 passed`；四组变异均观察到真实模块守住、变异模块违反）。
- 真实资产 r3 dry-run 与 r1/r2 字节一致性、pinned HEAD 核验、CLI 必填拒绝见 §10.2。
- v4/v5 release 哈希复核未变（§10.2）。
- 完整 RSIFlow 套件与 `git diff --check` 仍由主 agent/属主用户最终复验；本轮未冒充其结果。

### 10.5 本轮自审

- **不伪造历史**：原初 RED 仅为 import/fixture 级的事实明确承认并保留；本轮 RED 与 mutation 输出均为实际运行所得。
- **mutation 不碰正式产物**：变异只作用于 `tmp_path` 下的脚本副本，以独立模块名导入；真实脚本、v4 资产、r1/r2/r3 与 v5 release 均未被修改，因此不存在"恢复"步骤的遗漏风险。
- **范围**：只改 v5 任务允许的脚本/测试/`REPRODUCTION.md`/本报告；未改生产代码、v4/v5 数据、JSONL 账本、`train_4b.json`。
- **pinned 身份**：三重核验（spec 声明 + repository_root 真实 HEAD + package root HEAD），git 不可用时只读回退，未改 trust；错误 checkout 无法再产出自称 pinned 的回执（已由 mutation 证明该守卫载荷性）。
- **不可变与分母**：输出目录必须不存在；152/744/694/50 口径不变；r3 与 r1/r2 数据逐字节一致，未重建 release。
- **未执行**：无模型/API/SCO/五轮/SFT；无 BFCL 分数、无完整 694 结论。
- **遗留**：r1/r2 回执的旧 runner note 与 r1 的 provenance 字段命名属不可变历史，已在报告中标注；是否以 r3 重建单一权威 release 链由 controller 决定。
