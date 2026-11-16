# 正式 694 生成/评分协议修复任务（后续实施）

工作树：`.worktrees/preflight`。先读 AGENTS、experiment-preflight-progress、model-scoring report 中生产路径附带发现、`bfcl-attribution-design.md`，以及 validate/dynamic_predictions/tool_validation 相关测试。

范围仅：
- `RSIFlow/dynamic_predictions.py`
- `RSIFlow/tool_validation.py`
- `RSIFlow/validate.py`
- `RSIFlow/harness_api.py`
- `RSIFlow/official_tool_entry.py`
- `RSIFlow/tool_sandbox.py`（仅在 RPC envelope 传递审计上下文所需，模型请求本身不得改变）
- 对应现有 `RSIFlow/tests/test_*.py`
- `reproduction/REPRODUCTION.md` 和任务报告

必须 TDD：
1. spec 已含 `benchmark` 键时，`generate_dynamic` 不得重复传 benchmark；保留官方其余字段/hash，正式 selected_specs 路径有回归测试。
2. `evaluate_tool` 既有 daily independent_validation 非 full 仍严格要求 50；只有 `purpose=report_only` 且绑定 frozen manifest/evaluation scope 的正式报告路径，才可按 frozen selected_tasks 执行 BFCL 152。ACE 仍按完整 conversation 和配置分母。测试覆盖伪造/缺 manifest binding 不能放宽、daily 50 不变、BFCL 152 与 ACE 50 的 queue/denominator。
3. `validate.py` final_test 的完成轮数不得硬编码 3；应由有效 frozen config/snapshot 的协议轮数判定，并对 5 轮和既有 3 轮 fixture 分别测试。不得放宽 `clean_independence_audited`、exposure、final state completed 或 ACE skip 限制。
4. 为 BFCL 真实评分生成可信 `task_call_attribution.json`：由 `evaluate_tool` 在官方执行后根据 frozen selected_tasks 与 transport 请求建立确定性、无歧义的一一归属；无法唯一对应即 fail closed。文件声明 `generator=evaluate_tool_native_request_attribution_v1`、state hash、selected IDs/hash、每回执 identity.request 的 SHA256；官方 `result.json` 同步写入 attribution 路径/SHA256/generator 绑定，供 smoke runner 复算。禁止按结果顺序猜映射，ACE 多轮无法归属时保持 blocked。
5. 不改 v4/v5 数据、官方 evaluator、分数或现有 smoke；不启动最终评估/五轮。
6. 运行 focused、相关协议测试和完整 RSIFlow pytest；记录所有失败。

目标只是让正式协议代码与冻结配置一致，不宣称 v5 切换、真实 694 或五轮已执行。具体实施顺序和报告路径由 controller 在派发时补充。
