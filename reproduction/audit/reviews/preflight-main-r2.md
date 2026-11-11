# 主 agent 对 Qwen preflight r1 的回源处理

- Qwen 结论为 CHANGES；没有把完成标记当通过。
- v4 取代 v2/v3 成为当前机器配置和公开状态的唯一 frozen release；旧 release 保留不覆盖。
- READY 新增 evaluation_scope：744 frozen、694 scored、50 pending。
- 新增真实 release 测试，直接读取机器配置和 RoundStore，不依赖上游占位配置中的两个 skip。
- 公开首页、RESULTS、REPRODUCTION、机器 JSON 明确同协议重构、694/744、数据保留意见、probe 和私有凭据阻断。
- 新操作记录 evidence_sha256；历史记录使用 evidence-hashes.json，不重写 operations.jsonl。
- process.json 增加 generated_at、as_of_sequence、is_snapshot 和中文快照说明；process.js 显示截至 sequence。
- cohort-release-v4.json 对账 source build 与 v4 READY。旧 summary 的 quarantine 只有总数，明确不编造逐源值；build_cohort.py 对未来构建增加逐源 accounting。
- 2Wiki 回源到上游 prepare_report_specs.py：当前 HF 数据没有 answer_id/evidences_id，必须使用 v1。下载的 v1.1 aliases 只作补充资产，不改变评测协议。
- 四类 official spec 已从 v4 生成并验证；HotpotQA/2Wiki 各一条金答案在 100 固定分母下 EM=F1=0.01，仅作 evaluator smoke。

Ruling: 原始证据不批量公开或强制入 git，避免泄露；以本地原文件、SHA256 sidecar 和脱敏公开快照共同审计。若原文件被删除，hash 不能恢复内容。
Ruling: 8B stage 仍保留为未执行占位，但本次没有准备或运行承诺，需 4B 审阅后决定。
