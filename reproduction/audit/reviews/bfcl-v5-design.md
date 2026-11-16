# BFCL v4 native-ID 协议修复设计（只读）

## 结论与建议

推荐 **B：新建不可变 v5 frozen release，保持 BFCL 152 分母，对 19 个无 pinned official 对应的 v4 项作确定性替换**。不修改 v4，不降低 152 分母，不改 official evaluator/data/结果。替换源必须是 pinned `ea13468e4423454d0c213704fb87cf7cb3990433` checkout 的 BFCL-v3 正式数据，候选必须在发布前固定、去重、满足任务完整性和 disjoint 约束。v5 应标注为“经 pinned BFCL v3 native-ID 对齐的同协议重构”，不得称作者原队列精确复现，也不能消除公开来源重构和 exposure 未知的限制。

只读边界：本工作树中公开 cohort assets/source validation manifest 不在磁盘上（预期绝对路径 `assets/cohort/validation_source_manifest.json`、`assets/cohort/allocated/validation/manifest.json` 均不存在），因此本文对用户给出的 133/152、19 个 mismatch（17 个版本/类别不匹配、2 个缺原 ID）作为既定事实，不声称在此独立重算。未联网、未读取凭据、未执行重建、实验、SCO 或 evaluator。

## 现有协议与风险

- `reproduction/scripts/build_cohort.py:31-61` 读取源 JSON/JSONL；`:100-112` 只按 `task_id/id/_id` 去重；`:224-271` 将规范化六源打包进 allocated training/validation 和 source manifests。它目前没有显式要求 BFCL 行具备 pinned official ID，也没有在分配前按 pinned BFCL official ID universe 过滤。
- `RSIFlow/data_protocol.py:44-50` 的 `native_id()` 会优先取 `task_id/question_id/id/_id`，全部缺失时退化为公开 prompt 的 `qhash`。`:75-138` 扫描源文件并把 `source_sha256`、record offset/length、`native_id`、`task_id`、`content_hash`、`group_id` 写进 manifest。内容 hash 是可审计的内部行身份，但它不是 BFCL official native ID，不能作为 runner 可寻址的 benchmark ID。
- `RSIFlow/data_protocol.py:223-240` 按配置 quota 抽取 evaluation 并对训练/验证分离；`:144-158` 特别把 ACE 对话按完整 conversation 聚合。BFCL 替换需保留 152 个完整 benchmark task 的语义，并重验跨 cohort 的 `task_id/group_id/content_hash` 不交叉。
- `RSIFlow/prepare_600_744.py:77-130` 从 source manifests 重新 pack，并验证 source role、disjoint、per-source quotas；旧输出存在时只接受完全相同的 READY/config/source hashes，否则以 `FileExistsError` 要求新 release，禁止覆盖。变更 BFCL 后需要新的 validation source manifest、source hash、validation manifest、data release 和 READY；不能把更新的 source manifest 偷换进 v4 目录。
- `RSIFlow/tool_validation.py:19-39` 的 `native_layout('bfcl_v3', spec)` 由 pinned `repository_root` 定位官方 package/data，读取 `BFCL_v3_*.json`，并拒绝无数据/重复 ID。`:104-118` 会把 selected native IDs 与官方 ID 集合求交验证；`:148-152` 会核验实际 runner queue。现有评分链路需要 ID 可映射到 pinned data，无法把 19 个内容 hash 当 official tasks。
- 已知事实为 v4 frozen validation 的 BFCL 152 条里 133 ID 可在 pinned official 4441 IDs 找到，19 条不在：17 条源版本/类别不匹配，2 条原始行缺 ID 后使用 qhash。任何“对官方结果补别名”“评分时跳过缺失项”或只把计数写成 152 都会破坏分母与逐任务证据。

## 方案比较

| 方案 | 做法 | 优点 | 代价/风险 | 判断 |
| --- | --- | --- | --- | --- |
| A：另一官方版本 | 查找本地已有或有可审计来源的 BFCL 官方版本，其官方 ID 集合与现有 152 项精确匹配；锁定对应 evaluator commit、source hash、category layout 和官方版本 | 若确有来源版本，可能只需更新 evaluator pin/机器 spec，减少样本迁移 | 当前 pinned `native_layout` 明确绑定 `ea13468`；不能臆测存在“恰好匹配 152”的官方 release。若改 evaluator 版本，需同步完整 native checkout/data/spec hash，所有 BFCL 分数和既有 gate 使用新协议；仍要解决两条无原 ID 的可审计映射 | 仅在找到官方发布物并证明 152 条逐 ID、逐 category 对齐时才可选；否则不推荐 |
| B：新 v5 替换 | 保留 quota=152，固定移除 19 个不兼容项，从 pinned official 4441 集合选择 19 个未被 validation/training/其他任务占用且完整的 official tasks；重写 BFCL validation pack/manifest，再生成全新 v5 训练/validation release | evaluator 版本不变；分母、runner 和 native-ID 一致；变更可逐条追溯；v4 历史不变 | 需要 source rebuild/migration 和新 hashes；抽样改变 held-out cohort，所有 v5 状态须重标。官方 ID 候选可能已在旧 native cohort 的训练/验证或别的 source 中出现，必须完整检查 disjoint | **推荐**，最符合“不降分母、不改官方结果”且可审计 |
| C：显式 crosswalk | 对 17 项用原始记录字段/官方映射建立一对一、可证据复核的 official ID crosswalk；对 2 项从权威原始源恢复真实 ID，不用 qhash；保留 152 原内容 | 如果每条都能找到可靠身份映射，可最大程度保留 cohort 内容 | 17 项已知版本/类别 mismatch，身份相似不能证明官方 task 等价；2 项缺少原始 ID 不能靠内容相似补造。错误 crosswalk 会把不同题目送入 official scorer 并污染结果 | 可作为证据发现路径；只有逐项来自权威 source 的原生映射且语义/category 完全匹配才可采用，否则拒绝 |

其他不应做的方案：把 denominator 改成 133（降低原 152 quota）；在 `metric_rows` 忽略 19 个不支持 ID（完成数与分母不一致）；改 pinned official JSON/score 来接受新 ID；把内容 hash 重写成官方 ID（伪造身份）；按原先 152 顺序只替换 runner 的请求却保留旧 manifest/预测 hash；重建并覆盖 v4 文件；只改机器配置但不更新 source manifest、record hash、source hash、validation manifest 和 READY。

## 推荐的最小可审计迁移

1. **先锁定输入与 v4 封存证据。** 读取并校验 v4 source-validation manifest、allocated validation pack、validation v4 pack、train source manifest、旧 READY/protocol 及其 hash；记录全部当前 BFCL IDs 和 19 条原因分类。验证其他五源计数、任务记录 bytes、IDs 与 hashes，将其作为 v5 不变性基线。v4 的目录和 receipt 保持只读。
2. **固定替换清单，先于生成。** 在 pinned official commit 的 4441 个 official ID/category/data 里，按明确排序和固定 seed/hash 选择 19 条候选；排除已在 v4 152 内的 133 IDs、重复 candidate、已有其他 source/native training/validation IDs，以及与现有任务 group/content 冲突的任务。选择规约必须完全确定、不得看模型结果。每条清单保存 old task ID、old record/source hash、移除原因、new official ID/category、official source file hash/offset/length/record hash、选择规则及 disjoint 检查结果。若要复用同一公开样本用于测试，须显式处理 exposure；不得把重构 cohort 宣称 clean/unexposed。
3. **构造新源清单而非改旧清单。** 从源记录读取新 official task 的原始完整 row，写入 `assets/cohort/allocated/v5/validation/bfcl_v3.jsonl`（或等价版本化新路径），在 source-validation manifest 对应 19 个条目重建 `native_id=official ID`、`task_id=bfcl_v3:<official ID>`、`source_sha256`、record offsets/length/sha、content/group hash、category strata 和 exposure；其他源条目内容/身份保持一致。更新 manifest hash，并保存 machine-readable migration receipt 与 new source file hash。新清单应固定新 release/version 字段，避免和 v4 误混。
4. **从独立配置做 v5 pack。** 复制机器配置到新版本，例如 `reproduction/configs/train_4b_v5.json`，将 `data_release`、`frozen_data_dir`、`validation_manifest`、`source_validation_manifest` 与必要 source data root 指向新位置，其他 quotas/config 维持一致。用现有 `prepare_600_744.py` 生成新的 `rounds_3000_5x600_v5` / `validation_744_v5`；确认它没有覆盖任何 v4 路径。`source_training_manifest` 与 training 数据可复用原 hash，但新 config hash/READY 会变化；若该工具仍重打包训练数据，需验证其 record bytes 和五轮任务 IDs 完全保持一致。validation 的五个非 BFCL source task/bytes/IDs 必须与 v4 一致；BFCL 始终 152，ACE 50 pending，744 总数、694 partial 口径不变。
5. **核对 pinned runner mapping 与来源快照。** 新 config `tool_benchmarks.bfcl_v3.data_path` 应指向新 frozen allocated BFCL validation pack（与官方 runner 使用的 pinned checkout data 分工明确）；运行 `native_layout` 检查 152 IDs 全部在 ea13468 official universe、category 分布/ID 唯一、无 unsupported ID。重新运行 `validation_sources.freeze/verify` 到新 ignored output directory，确保 official specs、pinned checkout、所有 data_path 的 hash 均覆盖 v5 数据。不能仅手工编辑旧 snapshot。
6. **冻结与公开边界。** 新 READY/status 必须记 `release=v5`、source-validation manifest hash、BFCL replacement receipt hash、每源 quota、`clean_independence_audited` 原值/暴露事实、`status=prepared_not_executed`。更新复现手册、PROGRESS/公开 JSON 和 audit 记录时明确 v4 保留为历史、v5 是变更后的协议版本；不得把旧 v4 BFCL 评分当 v5 结果，不得将 smoke 结果等同完整 BFCL/694。修改范围需另行授权后实施。

## 实施时必须增加的 RED 测试

测试使用临时目录和小型 fixture，不依赖 GPU、网络或第三方包；先对旧行为实际 RED，再实现迁移工具/规则。

- **Pinned ID 集合检查（RED）**：构造含 pinned official IDs 与不在集合 IDs 的 BFCL rows；当前扫描/manifest 验证应在旧 cohort 接受不支持 ID 的行为处暴露失败。实现后要求每个 `bfcl_v3.native_id` 属于以 commit+data hashes 冻结的 official universe；qhash fallback 禁止用于 BFCL（其他 source 既有 fallback 行为不在本变更中扩散）。
- **可重复替换与 immutable release**：输入恰好 152 rows（如 133 valid + 19 invalid），fixture official pool 提供 19+ 可选 IDs；两次同 seed migration 输出逐字节相同、保留 152 个唯一 IDs、同一个候选清单 hash。目标目录已存在应 fail closed，不覆盖 v4 或既有 v5。
- **disjoint/候选筛选**：candidate 若与 133 existing ID 重复、已在 train manifest 中、同 `group_id`/`content_hash` 交叉、类别或记录不完整均不得选；候选不足 19 时明确阻断，不静默降 quota。
- **原行身份与offset**：替换条目的 source file hash、record offset/length/sha 必须能读回相同 JSON bytes；`native_id` 与 pinned record ID 一致，task_id 前缀、category strata、group/content hash 可复算。旧 qhash 两条需有显式 removed record audit，不伪装 official identity。
- **全 cohort 不变量**：BFCL=152、ACE=50、其他四源计数与 ID/content/record bytes 不变、总体=744、skip ACE scored=694；`assert_disjoint` 通过。将 19 改成删行应 RED；篡改 official fixture hash、manifest hash、source hash/offset、增加重复 ID 或再写 v4 路径应 fail closed。
- **release 与 evaluator 集成**：fixture 跑 `prepare_600_744.prepare()` 到 v5 临时目录，断言 v4 目录/receipt 未改、v5 READY/config/source hashes 自洽、`validation_sources.verify` 成功；`native_layout`/queue 校验应确认 selected official IDs 152/152 完全被 pinned official runner 接受。测试官方 result 的 expected/completed 必须 152，禁止过滤结果后仍报告 152。

测试不得写真实 release、修改 public pinned evaluator 数据或依赖模型 API。真实 `prepare` 只可在新 v5 输出目录、经任务授权后进行，并先 dry-run migration receipt。

## 需要修改的文件（实施阶段建议）

- `reproduction/scripts/build_cohort.py`：在 BFCL normalization/validation 阶段增加 pinned official ID 校验或迁移输入校验，避免 v6 以后再次把版本不兼容/无 ID 行分配进 BFCL quota；如不希望它承担 release migration，则新增独立、单用途迁移脚本。
- `RSIFlow/data_protocol.py`：只在 BFCL 来源拒绝无 official native ID，不改变其他数据源的 hash fallback；如迁移脚本直接构造 manifest，提取共享验证逻辑避免两套规则。
- `RSIFlow/prepare_600_744.py`：仅在现有 immutable 新目录语义下复用；若需新增版本标记/receipt 字段，保持向后兼容并更新 READY 验证。避免扩大改动，优先由迁移脚本输出合规 source manifest 后复用现有 prepare。
- `reproduction/configs/train_4b_v5.json`（新版本文件）与新 validation config（或显式独立 `data_path` 机器 config）：仅指向 v5 数据；不覆盖原 `train_4b.json` 或 v4 manifests。是否要新 official specs 文件取决于是否保留原 pinned official spec，只更改独立 tool data path；推荐复制 validation config 到 v5，保留 evaluator commit 和 `repository_root` 不变。
- `reproduction/tests/` 现有数据构建/release 测试：加入上述确定性选择、ID universe、hash、disjoint、quota、immutable 与旧版本不变测试；不修改旧断言来迁就 v5。
- `reproduction/REPRODUCTION.md`、`reproduction/PROGRESS.md`、`web/data/reproduction.json`、`web/data/repository.json` 及审计 evidence/operation 报告：实施后同步区分 v4/v5 与新证据；不得提前写入成功运行状态。若控制协议/配置有行为变化，同步 `AGENTS.md`。

## 证据限制

本次在目标工作树里没有 source validation manifest/allocated cohort 实体，故无法审计那 19 条原始记录、candidate 的具体官方类别/ID、跨 source/training disjoint，也无法给出实际替换 ID 清单或 source hashes。用户提供的 133/19 分类作为输入事实；实施前必须由主 agent 提供可读且受信的 v4 manifests 和 pinned official assets，在本地只读核实后才能固定 migration manifest。若 official version A 的完整源无法本地验证，也不要联网临时抓取或猜测版本。
