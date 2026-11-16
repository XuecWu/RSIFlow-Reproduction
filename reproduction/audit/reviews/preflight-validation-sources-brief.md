# Preflight 验证资产路径修复任务

工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight`
分支：`reproduction/4b-preflight`

先读 `AGENTS.md`、主工作树 `reproduction/audit/reviews/experiment-preflight-qwen-r1.md` B1，以及 `RSIFlow/validation_sources.py`。用户已授权按 preflight 建议修复。

允许修改：
- `reproduction/configs/validation_744.json`
- `reproduction/tests/test_frozen_release.py`（若现有测试结构适合；否则先报告，不新增测试框架）
- `reproduction/REPRODUCTION.md`

要求：
1. 先增加/调整一个现有 reproduction 测试，证明当前 `tool_benchmarks.bfcl_v3.data_path` 与 `acebench.data_path` 必须存在且分别对应冻结 allocated validation 的 152/50 条；在配置修复前实际运行并观察因路径不存在 RED。
2. 将两个 `data_path` 最小修改为：
   - `/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/cohort/allocated/validation/bfcl_v3.jsonl`
   - `/data/wuxuecheng/Hierarchical_level/RSI-Flow/assets/cohort/allocated/validation/acebench.jsonl`
   选择依据是它们与冻结配额 152/50 精确匹配，且主 agent 已复核 allocated 文件与 `RSIFlow/runtime/data/validation_744_v4/{bfcl_v3,acebench}.jsonl` 分别字节一致；测试应固定这个来源关系。不改 normalized 全量池、不改官方 evaluator、不删除来源校验。另注意 BFCL manifest 另有 19 个 ID 不在官方 runner 中，这是独立协议 blocker，本任务不得通过修改 ID、官方数据或断言来掩盖。
3. 测试转 GREEN，并运行现有 reproduction 测试套件。不要依赖新增第三方包。
4. 在 Git 忽略的新输出目录实际执行 `validation_sources.freeze(settings, path)`，随后 `verify(settings,path)`，证明完整外部输入快照可生成且复核一致；记录路径和 SHA256。该步骤只读公开资产并写 ignored 回执，不运行模型/API/SCO。
5. 文档说明这两个路径是冻结 allocated validation 输入，`validation_sources` 仍固定 official specs、native checkout 和全部外部哈希。
6. 不修改 `validate.py`、`tool_validation.py`、effective-config 代码或任何 JSONL 账本；不 commit/push；不启动五轮/最终评估。

把 RED/GREEN、测试、快照证据和自审写入 `reproduction/audit/reviews/preflight-validation-sources-report.md`。完成后回复状态与报告路径。
