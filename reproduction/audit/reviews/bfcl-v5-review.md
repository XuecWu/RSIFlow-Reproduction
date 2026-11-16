# BFCL v5 scoped review

## 结论

**Spec：FAIL。质量：CHANGES。** 真实 v5 release 的主要数据不变量有独立只读复算证据支持；但 brief 要求的行为级 RED 证据不足，迁移工具也没有强制确认实际 BFCL checkout 是否为 pinned commit。

## Findings

### Critical

无。

### Important

- **`reproduction/audit/reviews/bfcl-v5-report.md:31-48` — TDD 证据不足。** 首个 RED 只是迁移模块尚不存在时的 `ModuleNotFoundError`；后续行为测试失败均被报告归因为 fixture/期望问题，没有展示迁移协议断言在实现前因行为错误而失败，因此不满足 brief 对严格行为级 TDD RED 的要求。
- **`reproduction/scripts/migrate_bfcl_v5.py:171-173,187-189,369-382` — migration 工具没有强制 pinned checkout 身份。** `expected_official_commit` 可省略；代码只在提供该值时比较 spec 声明的 commit，没有核验 `repository_root` 的真实 HEAD。迁移测试使用合成 checkout，也没有验证迁移输入确实来自 pinned commit。当前已生成的 release 经独立只读核对确实来自 `ea13468e4423454d0c213704fb87cf7cb3990433`，但工具再次运行时可在错误 checkout 上产出自称 pinned 的 receipt。

### Minor

- **`reproduction/configs/validation_744_v5.json:17`、`reproduction/audit/reviews/bfcl-v5-report.md:18`、`reproduction/REPRODUCTION.md:198` — 配置变更说明不准确。** v5 的 ACE `data_path` 也从 v4 的 `assets/cohort/acebench/test.jsonl` 改到分配后的 50 条 validation pack；文档说只改 BFCL `data_path`。实际 ACE validation 记录身份和字节与 v4 相同，这项是路径差异，应如实列出。
- **`reproduction/REPRODUCTION.md:195` 与 `reproduction/scripts/migrate_bfcl_v5.py:353-355` — 记录过时。** 手册称迁移测试有 21 项，测试文件实际有 22 项；migration receipt 的 runner note 仍称 `evaluate_tool` 对 independent validation 硬编码 50，而当前工作树已有 formal 152 分支，报告也记载该分支存在。

## 独立只读复算证据

- pinned evaluator checkout 的 `git rev-parse HEAD` 为 `ea13468e4423454d0c213704fb87cf7cb3990433`。
- r1/r2 BFCL pack SHA256 相同；两份 source manifest 的任务记录在忽略绝对 `record_file` 路径后相同。对 r2 的 19 条替换逐项按官方 source offset/length 读取原始字节并复算文件 hash、record hash、native ID，再映射到 r1 manifest/pack，**0 项不匹配**。
- v4/v5 frozen validation source 计数一致：BFCL 152、ACE 50、HumanEval+ 164、MBPP+ 178、HotpotQA 100、2Wiki 100，总计 744；v5 READY 记 694 scored、50 pending。
- v4/v5 的 20 个训练 pack SHA256 集合相同；ACE 和其他四个非 BFCL validation source 的身份字段及 record bytes 相同。
- `validation_sources_v5.json` 中 284 个文件 hash 全部与当前文件匹配。
- 相关测试结果、完整 reproduction 测试结果和真实 v5 prepare 验证采用 bfcl-v5-report.md 所记录的证据；本轮 review 没有重跑测试或重建 release。

## 未覆盖边界

- 未重跑 reproduction 或 RSIFlow 测试；主 agent 正在独立复跑。
- 未运行 BFCL evaluator、GPU/SCO/API、五轮演化、SFT 或真实评分；没有因此确认模型分数，也没有宣称完整 694 已执行。
- v4 release 全目录迁移前后 30 文件的逐项基线比对、`validation_sources.freeze/verify` 的代码级重跑及生成路径不存在检查，沿用 bfcl-v5-report.md 中记录的结果，本轮未重复执行。

## BFCL v5 fix round 1 scoped re-review

**总体 PASS。**

- **Important 1 — ADDRESSED**：`bfcl-v5-report.md:199` 起明确保留原初 TDD 证据不足的历史；新增行为 RED 和临时副本 mutation 验证，没有把补充证据冒充原初历史。
- **Important 2 — ADDRESSED**：`reproduction/scripts/migrate_bfcl_v5.py:213` 起强制 expected commit，并核对 spec、真实 repository HEAD、package root HEAD；CLI 参数必填，回执记录核验结果。
- **Minor：ACE 路径 — ADDRESSED**：`reproduction/REPRODUCTION.md:199` 起已区分主工作树与 preflight 的 v4 基线。
- **Minor：计数及 runner note — ADDRESSED**：`bfcl-v5-report.md:278` 起更新为 33 项，准确描述 daily 50/formal 152，并注明 r1/r2 旧文案作为不可变历史保留。

**新 breakage：未发现新增 Critical/Important。** 独立只读复算确认 r1/r2/r3 pack 哈希相同，r3 记录两个 pinned HEAD，既有 v5 READY 和 validation manifest 哈希未变。

本轮未修改文件、重建 release、重跑测试或检查真实评分；reproduction 全套结果待主 agent 独立复跑。本节仅追加上述已有复审结论，没有重新审查。
