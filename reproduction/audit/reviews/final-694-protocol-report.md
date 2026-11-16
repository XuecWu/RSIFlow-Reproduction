# 正式 694 协议修复报告

结论：三项一般协议修复完成，BFCL attribution 已接入 pinned 官方 task context 并采用 fail-closed 校验。当前 v4 frozen BFCL 队列仍有 19/152 native ID 不属于 pinned official pool，正式 BFCL/694 在数据队列校正前仍不可执行或完成。本任务未替换数据、降低分母、改 evaluator、运行真实评估或宣称 694 已得分。

## 范围与修改

仅修改 brief 允许的生产文件、对应测试、`reproduction/REPRODUCTION.md` 和本报告：

- `RSIFlow/dynamic_predictions.py`：将声明性 spec 中的 `benchmark` 字段从 runtime kwargs 中移除，再由 `generate_dynamic` 以当前 evaluator ID 传入 `OfficialEvaluatorSpec`，其余 spec 字段和原始 hash 输入保留。
- `RSIFlow/tool_validation.py`：daily independent validation 继续严格 50 项；正式 BFCL 152 仅接受 `report_only`、`formal_report_evaluation` 且 frozen manifest、source role、snapshot/config hash 全部相符的 evaluation scope。验证 frozen task ID 去重及其与 selected BFCL native IDs 的精确集合一致。保留 ACE 对完整 conversation 的既有检查。
- `RSIFlow/validate.py`：`final_test` 完成轮数取有效 pipeline config 的 `max_generations`，校验其为正整数；原有 completed、clean independence、exposure 和 ACE skip 限制未放宽。冻结 binding 显式保留 formal report scope。
- `RSIFlow/official_tool_entry.py`、`tool_sandbox.py`、`harness_api.py`：固定 BFCL official commit `ea13468e4423454d0c213704fb87cf7cb3990433` 和 `BaseHandler.py` SHA256 `4a2f4e677d1f6930f396dbc92c50138f56268f970de7e4f815fb801607cafad1`。仅在 `BaseHandler.inference(test_entry)` 调用期间从当前官方 task row 取 native ID，并从冻结 selected category 映射 category；context 在 `finally` 中复位。RPC envelope 将审计 metadata 放在独立 `attribution` 字段，sandbox 再传入内部调用 metadata，`HarnessAPI` 在建立 `identity.request` 前剥离该字段，故模型可见的 `messages/tools` 不变。commit 或源码 hash 不匹配、selected queue 外的 task 均直接失败。
- `RSIFlow/tool_validation.py`：官方 generate/evaluate 后按 receipt 中显式 native context 生成 `task_call_attribution.json`。精确覆盖 selected IDs，记录 generator、state hash、ID hash、category、receipt key 和从 `identity.request` 重算的 request hash。要求所有 transport receipts 均显式归属、completed、本地模型调用非空且全部 completed；重复/共享/未知归属、缺失回执或状态不符都 fail closed。完成结果绑定 attribution 路径、SHA256 和 generator，并计入 `completed_model_calls`。缓存命中重新验证归属文件、回执和调用状态。此逻辑不使用 result 顺序或模糊 question matching。
- `RSIFlow/tests/test_600_744_protocol.py`、`RSIFlow/tests/test_tool_validation_preflight.py`：增加 spec normalization、50/152 gating 和伪造 binding、5 轮/既有 3 轮配置、RPC metadata 隔离、context 生命周期、pinned 来源不匹配、request hash、重复 receipt、未归属和不完整调用、无 attribution 缓存拒绝等测试。
- `reproduction/REPRODUCTION.md`：记录 production attribution 行为及 v4 ID 缺口和 694 状态。

## TDD 与验证

RED 阶段先加入对目标行为的测试，再执行 focused 测试。首轮 `test_600_744_protocol.py` 预期行为测试有 4 项因 helper/接口尚不存在而失败；tool validation focused 测试中，RPC frame 和 pinned handler instrumentation 两项因实现缺失失败。开发中出现过一次暂时的 `harness_api.py` 缩进问题，修正后恢复为针对缺失函数的 RED。随后实现代码并迭代修正 fixture 与错误断言，GREEN 后增加了 pinned hash mismatch、未归属 receipt、未完成调用及 3-round fixture 覆盖。

实际验证命令与结果：

1. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python -m pytest -q RSIFlow/tests/test_600_744_protocol.py RSIFlow/tests/test_tool_validation_preflight.py RSIFlow/tests/test_model_scoring_smoke.py` — **77 passed, 2 skipped**。
2. `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python -m pytest -q RSIFlow/tests` — **363 passed, 2 skipped, 6 failed**。失败为当前解释器缺少 `jsonschema`、`rich`、`fastapi` 的既有环境依赖：`test_custom_provider.py` 1 项缺 jsonschema，`test_runtime_adapter_regressions.py` 1 项缺 rich，另一项 harness validation 因依赖缺失而返回 validation_failed，`test_service_startup.py` 3 项缺 fastapi。未安装依赖或修改这些测试。
3. `git diff --check` — 通过。
4. `python -m py_compile RSIFlow/dynamic_predictions.py RSIFlow/tool_validation.py RSIFlow/validate.py RSIFlow/harness_api.py RSIFlow/official_tool_entry.py RSIFlow/tool_sandbox.py` — 通过。

## 剩余阻塞与边界

- 已知 frozen v4 BFCL selected queue 中 19 个 native ID 不在 pinned official pool；`evaluate_tool` 会在构造/校验队列时拒绝无效 ID。没有降为 133 项或生成新数据版本，所以正式 BFCL 和 694/744 仍 blocked。需要另行审定 frozen release 与来源 manifest/hash 后才能执行完整分母。
- 当前环境没有运行 pinned BFCL 官方 evaluator 进程来做端到端 RPC 验证。instrumentation 有离线 context/RPC 单元测试和源码 SHA/commit fail-closed gate；真实服务、GPU、SCO、外部 API 均未启动。pinned hook 实际运行时若签名或依赖源码有变化，将因 hash gate 阻止运行，不能退回模糊匹配。
- 全量 pytest 有上述 6 个环境依赖失败；其余 363 项通过。focused 相关协议及 model-scoring smoke 测试全部通过。
- 本次未读取凭据，未改 v4/v5 数据或 evaluator，未生成分数，未执行 commit/push。

## Task D fix round 1

本轮按 Kimi findings 增补集成回归并修复：

1. **Critical：validate 完成缓存重验** — `validate.py` 的 `complete.json` 快速返回前，逐个调用工具 benchmark 的 `evaluate_tool` 缓存验证；正常评分循环也将工具 benchmark 分支置于一般 completed-result 快速读取之前。`evaluate_tool` 对 BFCL/ACE 缓存重算官方 category 输出/分母/指标与 task rows、检查 frozen state，并对 BFCL 重新验证 attribution、receipt 和 sidecar。新增完整 validation completed-cache fixture：缓存结果 state/hash 都有效但缺 BFCL attribution 时必须失败。
2. **Important：主 transport receipt 与 `.call_NNN.json` sidecar 分离** — `transport_evidence()` 只将主 JSON 收入 receipt 集合，并单独读取 sidecar；按主回执 key、连续 call index、完成状态和数量进行对账。新增实际命名与 HarnessAPI schema 一致的 primary receipt + `.call_000.json` 集成 fixture。
3. **Important：完成调用计数来源修正** — attribution task entry 保持 runner 合同 `{receipt_key, request_sha256}`；`completed_model_calls` 改从 receipt key 查已验证主回执，再汇总其 `calls`，不再访问不存在的 `item['calls']`。上述完整 `evaluate_tool` 集成测试断言一个 sidecar 及一个 completed call 得到计数 1。
4. **Important：评分期 OfficialEvaluatorSpec 归一化** — validate 评分阶段先用 `dynamic_predictions.runtime_spec()` 去掉声明性 `benchmark`，再构造 `OfficialEvaluatorSpec`；同一 evaluator 实例用于 `evaluate_official` 和逐任务评分。selected spec JSON 在生成调用期间保留原始内容与 hash。新增 `validate()` 层 selected-spec 生成/评分路径集成测试，覆盖原始 spec 保持不变及 scorer 拿到规范 benchmark。
5. **Minor：报告精度** — 此前“缓存重验”仅有 `evaluate_tool` 层测试，不能证明 validate 的完成缓存路径会调用它；本轮新增了 `complete.json` 快速返回集成测试后，才将这一覆盖写入正文。sidecar/call-count 也由模拟 receipt helper 提升为 `evaluate_tool` 完成路径 fixture。

### RED / GREEN 证据

- 新增的 completed tool-result cache 测试在 validate 旧分支下 `DID NOT RAISE`，证明旧路径仅接受已完成分数并绕过 `evaluate_tool`。后续将夹具扩展为含有效 `complete.json` 和结果文件 hash，并保持缺 attribution 时 fail closed。
- primary receipt + `.call_000.json` fixture 在修复前失败于 `BFCL attribution contains receipts without explicit native task context`：sidecar 被错误纳入 `receipts`。修复后该 sidecar 单独核验，测试走完整 `evaluate_tool` 成功路径并验证计数为 1。
- selected-spec 集成测试从 `validate()` 真实控制流进入生成阶段并评分阶段；运行时去重前的构造形式 `OfficialEvaluatorSpec(**spec, benchmark=identifier)` 对 spec 自带 `benchmark` 会产生 duplicate-keyword `TypeError`。修复后测试确认传入评分的 benchmark 正确，磁盘 spec 字节 hash 在生成和评分之间不变。
- 集成 GREEN 命令：`PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python -m pytest -q RSIFlow/tests/test_600_744_protocol.py RSIFlow/tests/test_tool_validation_preflight.py RSIFlow/tests/test_model_scoring_smoke.py` — **80 passed, 2 skipped**。
- `git diff --check` 在最终文档和代码变更后通过。主 agent 独立运行的全量结果为 **369 passed, 2 skipped**；本轮遵照要求未重复全量测试。

本轮没有修改 smoke runner、数据或官方 evaluator，也没有运行真实 GPU/SCO/API。v4 BFCL 的 19 个无效 native ID 和正式 694/744 blocker 维持不变。
