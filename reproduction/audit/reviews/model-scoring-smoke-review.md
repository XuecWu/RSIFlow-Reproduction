# Task C scoped code review

Spec **FAIL**；质量 **CHANGES**。

## Findings

- **Important — `reproduction/scripts/model_scoring_smoke.py:396`、`:401`**：BFCL 后验只核对 native ID 覆盖、执行/评分状态和分母，没有验证每项 native submission 非空，也没有核验能归属到任务的已完成本地 Qwen 调用回执；因此仅凭官方 runner 的 `completed` 结果就可能把缺乏真实调用证据的结果标成 `scored`。同时 BFCL 未填 `completed_model_calls`，总调用数也漏计 BFCL。
- **Minor — `reproduction/scripts/model_scoring_smoke.py:315`、`:436`**：脚本要求输出目录不存在，但未验证它位于 Git 忽略的目录；调用者可将产物写入会被跟踪的仓库路径，不符合 brief 的输出边界。

## 真实执行前必须处理

1. 为 BFCL 每个 selected native ID 建立可核验的非空官方 submission 与本地模型调用回执对应关系；缺项或调用状态不完整时必须将该 benchmark 标为 blocked/incomplete，不能记作模型失败或 `scored`。
2. 将 BFCL 的真实完成调用计入报告的 `completed_model_calls` 汇总。
3. 在 plan 和 execute 入口验证输出根目录确属 Git 忽略的新目录，并拒绝可能写入受跟踪路径的目标。
4. 修复后重新运行相应 focused 测试与真实资产 `--plan`；真实 `--execute` 仍须由 controller 在授权 GPU 作业中执行并审阅真实证据。

## 已核对范围与边界

54 项选择、BFCL 50 个被 pinned official 识别的完整任务、ACE 不执行及不写正式 `complete.json` 的实现符合要求。审阅依据为 Task C 指定的 brief、设计、实现报告与 diff；采纳主 agent 提供的 focused `29 passed`、plan r3 `execute_ready=true` 且 `blockers=[]` 结果，未重跑测试。全量测试当时由主 agent 独立复跑中，结果未包含在本审阅结论内。实现报告记载真实模型执行尚未运行，因此本审阅未覆盖真实 SCO/GPU 服务、BFCL 官方运行时行为、任务级 transport 回执映射、真实 cleanup/显存释放或实际隔离执行；plan 成功不能替代这些证据。

## Scoped re-review — Task C fix round 1

原 Important **ADDRESSED**；原 Minor **ADDRESSED**；修复 diff 新增一项 Important，故总体 **CHANGES**。

- **原 Important — ADDRESSED**：脚本现在要求每个 BFCL native ID 有非空 submission 和已完成的本地调用回执；证据缺失时 fail closed，官方 runner 报 `completed` 也不会单独让结果通过。参见 `reproduction/scripts/model_scoring_smoke.py:476`。
- **原 Minor — ADDRESSED**：plan 和 execute 都会校验输出路径位于项目内、尚不存在且被 Git 忽略，并把校验方式记入回执。参见 `reproduction/scripts/model_scoring_smoke.py:313`。
- **新 Important** — `reproduction/scripts/model_scoring_smoke.py:526` 接受 `task_call_attribution.json` 中的任务到回执映射，只验证文件里的 state hash、ID 覆盖、回执状态和唯一归属；没有验证映射由可信生产方生成，或回执请求确实对应所声称的 native ID。伪造或错误归属文件仍可能让不相关的已完成调用被计作该任务证据。需将映射与可信执行路径绑定；缺少此证据时继续 blocked。

本次结论采纳实现报告中的 focused `47 passed`、相关 `85 passed, 2 skipped` 和 plan r5 结果，未重跑测试。真实 GPU/SCO 执行未覆盖；报告说明当前上游不产出归属文件，因此真实执行中的 BFCL 会 fail closed。

## Scoped re-review — Task C fix round 2

总体 **PASS**；无新增 Critical/Important/Minor findings。

- **Fix1 新 Important — ADDRESSED**：`_bfcl_task_evidence()` 现在要求官方 `result.json` 绑定指定 generator、内部路径和 attribution SHA256，并重算文件哈希；同时校验 attribution 的 selected IDs 及其 hash。
- **每回执 request hash — ADDRESSED**：逐回执读取 `identity.request`，用 `value_hash` 重算并比对 `request_sha256`；状态、state hash、调用完成状态和唯一归属均继续校验。
- **伪造负例 — ADDRESSED**：覆盖缺失/错误 generator、外部路径、错误文件哈希、篡改 attribution、selected-ID/hash 不符、缺失或错误 request hash、共享回执等情形，均 fail closed。
- **原三项状态保持关闭**：原 Important（BFCL 非空 submission/调用证据）和原 Minor（输出根目录 Git-ignore 校验）仍为 ADDRESSED；fix round 1 新 Important 本轮也为 ADDRESSED。

本轮未发现新 breakage。当前生产 `evaluate_tool` 尚未生成可信 attribution，因此真实执行会按设计保持 BFCL `blocked/incomplete`；这是已记录的预期边界，不是本轮回归。采纳主 agent 提供的 full `358 passed, 2 skipped`、focused `59 passed` 和 plan r6 结果，未重跑测试。
