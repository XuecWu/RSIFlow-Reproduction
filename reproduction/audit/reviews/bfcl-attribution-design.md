# BFCL task attribution 设计（只读）

## 结论

仅凭当前 `HarnessAPI.complete()` 的 `identity.request` 做事后 question matching 不能作为默认可信归属。当前 identity 在 `RSIFlow/harness_api.py:38-41` 由 `state_hash + request body + protocol + evaluation_scope` 组成；`official_tool_entry.py:47-53` 的 RPC envelope 只携带 `rpc_id` 和 `request`，没有 BFCL native task ID。`evaluate_tool()`/`metric_rows()` 只保存官方预测、类别、native ID 和 trajectory path，现有 transport receipt 也没有 native ID。因此设计必须采用：

1. 先做冻结记录与请求的**完全、唯一、可复算匹配**；
2. 匹配不到、匹配多个、请求结构不足以区分任务时直接 `blocked`；
3. 真实 BFCL 评分优先使用一个由 pinned official runner 当前任务上下文产生的最小 attribution instrumentation；它只增加审计元数据，不改官方生成/评分逻辑；
4. 永远不按结果文件顺序、线程完成顺序、receipt 创建顺序或“最相似问题”猜映射。

ACE 多轮和任何没有可靠 task context 的路径继续 blocked；本设计不改变 evaluator、分数、数据或分母。

## 当前数据和请求的可用身份

`evaluate_tool()` 已经拥有 `selected_tasks`、`selected_task_ids`、`native_layout()` 产生的官方 category→ID 集合，以及每条冻结 task 的 `record_file`、`record_offset`、`record_length`、`record_sha256`、`content_hash`、`group_id` 和 `task_id`。归属前必须重新按 offset/length 读取每条 record，并验证 pack hash、record bytes 和内容 hash；不能从完整 native data 的同序行读取，也不能把 `task_id` 的字符串前缀当作证明。

BFCL 任务记录的可匹配公开结构必须从冻结 row 中提取并规范化。规范化对象只允许官方问题输入字段，例如 `messages`/`question`/`query`、工具 schema、category 需要的公开参数；排除 gold、possible answers、score、运行时结果和凭据。数组顺序、message role、tool name/schema、字符串内容都保留；对象键递归排序，Unicode 使用 UTF-8，数值和空值采用唯一 JSON 编码。生成：

- `record_public_sha256 = SHA256(canonical_json({source, category, native_id(s), public_input}))`；
- `request_sha256 = SHA256(canonical_json(identity.request))`；
- `request_input_sha256 = SHA256(canonical_json({messages, tools}))`，仅在请求确实含这些字段时生成；
- `request_context_sha256 = SHA256(canonical_json({model, messages, tools, relevant generation parameters}))`，用于防止把不同模型/协议的请求混用。

匹配函数只能返回一个 candidate。必须证明请求中的完整结构等于冻结任务某个已声明 turn/prefix 的规范化结构，或由可信 instrumentation 直接声明 native ID 并让该结构 hash 与冻结记录一致。大小写、空白、字符串包含、截断文本、token 数、相似度和顺序推断都不是匹配证据。

## 方案一：事后完全匹配（仅作兼容验证）

在 `evaluate_tool()` 的 `generate` 完成、`evaluate` 之前或之后，读取所有 transport receipts，筛选 `status=completed`、`user_simulator=false` 且 state/evaluation scope 一致的记录。对每个 receipt 的 `identity.request` 计算上述 hash，再与 selected task 的**预先生成的允许请求签名集合**做集合匹配。

允许签名集合必须来自冻结 record 和 pinned runner 的已审阅输入转换规则，而不是从已生成结果反推。每个 task 可有多个 turn signature；每个 signature 记录 `turn_index`、canonical public input hash、期望的 message/tool 结构 hash 和是否允许重复。若当前 pinned runner 不能从 frozen row 明确推导它实际发送的 `messages/tools`，就没有允许签名，直接 blocked。

唯一匹配条件：

- 一个 request 只能匹配一个 task/turn；
- 一个 task 可以匹配多个 receipts，前提是每个 receipt 对应不同、已声明的 turn 或同一 task 明确允许的重复调用；
- 相同 question 在两个 native ID 间重复时，即使请求文本相同也视为多候选并 blocked；
- 同一 task 的重复问题没有 turn/index 证据时也 blocked；不能用先后顺序拆分；
- 短问题必须使用完整结构和 tools，不接受前缀/子串命中；
- 缺 receipt、未完成 call、改变后的 record bytes、额外未知 receipt、重复 receipt key 或一对多 candidate 均 blocked。

这种方案对最简单的单轮、唯一问题可能有效，但不应宣称普适：`HarnessAPI` 的 local Task prompt 还会包装官方 turn（`harness_api.py:79-84`），BFCL runner 可能改变消息结构，且请求中没有 native ID。若不能在 pinned commit 上证明转换规则，必须走方案二或保持 blocked。

## 方案二：最小可信 instrumentation（推荐）

不修改 BFCL 官方 comparator、数据或评分；只在官方调用到 RPC 的边界增加审计上下文，并把上下文绑定到实际请求 hash。实现时应固定为 pinned BFCL commit 的适配层，记录适配层源码 hash。

### 生成阶段

1. `evaluate_tool()` 写入不可变 `attribution_contract.json`，包含 selected tasks 的完整 native ID、category、record/source hashes、公开输入 hash、允许的 turn 数和 `state_hash`。contract 的 `selected_ids_sha256`、`selected_tasks_sha256`、`native_layout/source_hashes` 和 evaluator commit 都固定。
2. `official_tool_entry.py` 的 BFCL bridge 在官方 runner 当前 task context 可用处捕获 `{native_id, category, turn_index, task_record_sha256, public_input_sha256}`。最小可靠来源是 pinned runner 的当前 data row/context hook；如果该版本的 API model wrapper 没有 task context，增加一个版本化 adapter hook，使官方 runner 在发出模型请求前把该 context 写入 context variable/线程局部，而不改变传给官方 evaluator 的消息、tools 或结果。
3. bridge 发给 `HarnessAPI.complete()` 的内部 RPC envelope 增加 `attribution` 元数据；该字段不进入 Qwen 的 `messages/tools`，不影响模型输入。`HarnessAPI.complete()` 将它复制进 receipt identity，并要求：native ID 属于 contract、category/turn 与 contract 一致、`public_input_sha256` 等于当前 request 的 canonical hash、`request_sha256` 等于实际 body hash。
4. 每次本地 Task call 的 receipt 仍使用既有 request hash key，但额外保存 `attribution`、`attribution_contract_sha256`、`native_id`、`category`、`turn_index`、`request_sha256` 和 `request_input_sha256`。同一 receipt key 不能拥有两个 attribution；已有 receipt 与新 metadata 不一致时 fail closed，不覆盖或重试。

如果 pinned runner 无法在调用边界提供 native ID，禁止用结果顺序或问题相似度补上；生成阶段应明确输出 `attribution_unavailable`，BFCL result 最终为 blocked。

### 归属文件

`evaluate_tool()` 在确认官方 `generate`/`evaluate` 完成、且所有 receipts 已 reconcile 后，原子写入 `<output>/task_call_attribution.json`。建议 schema：

```json
{
  "schema_version": 1,
  "generator": "evaluate_tool_native_request_attribution_v1",
  "attribution_mode": "official_context_bound",
  "benchmark": "bfcl_v3",
  "official_commit": "ea13468e4423454d0c213704fb87cf7cb3990433",
  "state_hash": "...",
  "evaluation_scope": {"manifest_hash": "...", "source_role": "independent_validation", "system_snapshot_sha256": "...", "evaluation_config_sha256": "..."},
  "selected_ids_sha256": "...",
  "selected_tasks_sha256": "...",
  "attribution_contract_sha256": "...",
  "native_layout_sha256": "...",
  "task_count": 50,
  "native_count": 50,
  "no_order_matching": true,
  "tasks": {
    "<native_id>": {
      "task_id": "bfcl_v3:<native_id>",
      "category": "...",
      "task_record_sha256": "...",
      "record_file_sha256": "...",
      "record_offset": 0,
      "record_length": 0,
      "public_input_sha256": "...",
      "request_signatures": [
        {"receipt_key": "...", "request_sha256": "...", "request_input_sha256": "...", "identity_request_sha256": "...", "turn_index": 0, "call_count": 1}
      ],
      "native_submission_sha256": "..."
    }
  },
  "receipt_keys_sha256": "...",
  "created_after": "generate_and_evaluate_reconciled"
}
```

`tasks` 的 key 集合必须精确等于 selected native IDs；每个 receipt key 只能出现一次。`native_submission_sha256` 只摘要官方 prediction row 中已验证的非空 submission，不把答案内容写入额外日志。所有路径必须位于 output root 或由可信 receipt 指向，拒绝符号链接和路径逃逸。文件使用现有 immutable/atomic 写入语义，重复运行若内容不同直接 fail closed。

`result.json` 必须同步写入：

```json
{
  "attribution_path": "task_call_attribution.json",
  "attribution_sha256": "...",
  "attribution_generator": "evaluate_tool_native_request_attribution_v1",
  "attribution_mode": "official_context_bound",
  "attribution_state_hash": "...",
  "attribution_selected_ids_sha256": "...",
  "attributed_tasks": 50,
  "attributed_receipts": 50,
  "completed_model_calls": 50,
  "no_order_matching": true
}
```

这些字段必须在写入 `status=completed` 之前由 `evaluate_tool` 自己复算；smoke runner 只读并复算 file hash、state/ID scope、task/result coverage、receipt status、非空 submission 和 generator/mode。归属缺失或任何 hash/scope 不一致时，result 必须是 `blocked`/`partial`，不能返回 completed。

## 多轮、重复和歧义处理

- **多轮 BFCL**：一个 native task 对应多个 turn，允许一对多 receipts，但每个 receipt 必须带同一 native ID、明确 `turn_index`，且 turn index 不重复；每个 turn 的 request/input hash 与 contract 中的预期一致。任何 turn 缺失、跨 task、顺序之外的额外请求或同一 receipt 二次归属都 blocked。工具观察不作为新的 task 身份。
- **重复问题**：相同 canonical question、messages 或 tools 出现在多个 native ID 时，事后匹配不可用；只有官方 context instrumentation 能区分，且 context 必须绑定 record hash/native ID，否则 blocked。
- **短问题**：不做 substring、prefix、编辑距离或 token overlap。仅完整 canonical 结构且 candidate 唯一时可匹配；否则 blocked。
- **缺回执/未完成**：任何 selected task 无 receipt、receipt `dispatching/requires_audit`、局部 call 未 completed、state/scope/hash 不符，整个 BFCL benchmark blocked；不把该任务算模型错误，不写 0 分。
- **一对多/多对一**：task→many 只限声明的多轮；receipt→many 永远拒绝；一个 request 对多个 candidate 永远拒绝；一个 task 有额外未声明 receipts 也拒绝。不得按生成/完成时间补齐。
- **官方输出顺序变化**：归属完全由 native ID/context/request hash 建立，打乱 result 文件或 category 文件顺序不得改变结果；测试必须证明这一点。

## 测试设计（不运行）

使用临时目录和 synthetic receipts/predictions，不启动 GPU/API，不改官方 evaluator。测试必须先对旧行为 RED，再实现 instrumentation 后 GREEN：

1. **唯一单轮匹配**：一个冻结 row、一个唯一 messages/tools 请求、一个 completed receipt，得到一一归属；重排官方 result 行顺序仍通过。
2. **重复问题 fail closed**：两个 native ID 具有相同 canonical public input；同一 request 必须报 ambiguous，不得任选一个；有官方 context metadata 时才可分别归属。
3. **短问题 fail closed**：`"weather"` 与 `"weather today"` 不得用 substring/prefix 命中；没有完整 canonical equality 时 blocked。
4. **多轮一对多**：同一 native ID 的 turn 0/1 两个 receipts 均完成、turn index 唯一且 hash 匹配时通过；缺 turn、重复 turn、跨 task receipt 或额外 turn 时 blocked。
5. **伪造请求**：修改任何 record bytes/offset/record hash、request hash、identity request、state/evaluation scope、official commit、contract hash 或 receipt status，必须 blocked；不接受只改 attribution JSON 的结果。
6. **一对多/多对一**：一个 receipt key 归两个 native IDs、一个 request 匹配两个 task、一个 task 绑定未知/重复 receipt，均 blocked。
7. **缺回执**：删除 receipt、保留 dispatching/requires_audit、call 列表缺 completed，整个 BFCL blocked；官方 result 仍为 completed 也不能绕过。
8. **submission 绑定**：预测 row 的 native ID、category、非空 submission hash 改变时 attribution/result hash 不一致并 blocked。
9. **instrumentation provenance**：缺少或伪造 adapter/source hash、context native ID 不在 contract、context public hash 不等于 request hash，均 blocked；确认 metadata 不进入模型 messages/tools。
10. **无顺序依赖**：打乱 task_results、预测文件行、receipt 文件名和线程完成顺序，结果保持相同；删除任何显式 identity 后必须失败。

## 生产实施边界与未覆盖项

最小生产变更应限于 `tool_validation.py`、`harness_api.py`、`official_tool_entry.py` 及其 focused tests：复用现有 native layout、freeze/immutable、receipt hash 和 official runner；不改 `validate.py` 的评分算法、不修改 BFCL 数据/evaluator、不降低分母。若 pinned commit 没有可用的当前-task hook，宁可保留 BFCL blocked，也不要实现 fuzzy question matching。

本设计没有运行实验、网络、GPU、SCO 或 API，也没有读取凭据。当前工作树缺少可读的 v4 source manifest/allocated cohort 实体，故无法核对所有 BFCL question 的真实字段形态、category 文件布局和多轮 turn schema；实现前必须用受信 pinned assets 做只读 fixture/adapter 审查，并记录其 hash。不能因为设计测试通过就宣称真实 BFCL/694 已执行或已得分。
