CHANGES

# 独立 experiment preflight 预审（Kimi r1）

日期：2026-11-16  
工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow`  
结论：**NO-GO，不得启动 A0+B1–B5、SFT 或最终 694/744 评估。**

本审阅只核对实验启动边界，不重复 bubblewrap/seccomp 隔离代码审阅。除写入本报告外，未修改文件，未运行 `--check`、pytest、模型服务、SCO、API 或实验，未读取凭据。下文“已通过”均指所引历史证据的范围，不表示本审阅重新执行。

## 1. 四道强制门现状

门的定义见 `reproduction/RETROSPECTIVE.md:258-263`、`reproduction/REPRODUCTION.md:122-124`。

| 门 | 当前判定 | 可接受证据与缺口 |
| --- | --- | --- |
| 1. 五类 benchmark 最小 smoke | **未满足** | 只有 HumanEval+ 单任务 official comparator、BFCL 两次 native RPC、HotpotQA/2Wiki 各一条金答案 evaluator 记录；MBPP+ 未找到可接受报告。candidate 与 Codex dynamic/resume 是基础设施检查，不是另外两类 benchmark。`web/data/reproduction.json:265-269` 自己也标为 `partial`。 |
| 2. 逻辑 GPU 0–3 上健康的四卡 Qwen 服务 | **未满足（历史诊断通过）** | `pt-ox3o5he2` 曾完成四个 BF16 A800 服务、绑定和短生成，但报告明确 `services_stopped_and_training_memory_released=true`，registry 为 `services_stopped`；不能作为当前健康服务。见 `reproduction/audit/smoke/model-service-r3-result.json:2-29`、`model-service-r3-registry.json:2-4,162-164`。 |
| 3. 当前 694 配置预检 | **未满足** | 当前状态仍是 `historical_precheck_current_694_check_pending`（`web/data/reproduction.json:210-214`；`reproduction/RETROSPECTIVE.md:256`）。且文档命令没有传机器配置，详见 Critical 3。 |
| 4. 最终独立 experiment preflight review | **已执行，未通过** | 本报告为 CHANGES；Critical/Important 未关闭前不能转为 PASS。 |

因此不能表述为“四道门只剩形式审阅”或“隔离审阅通过即可启动”。

## 2. 已成立但不等于启动许可的事实

- 机器配置的目标是五轮、每轮 600、共 3000，v4 frozen release，最终评估 `final_only`，不做 A0 baseline validation：`reproduction/configs/train_4b.json:2,102-113,151-163`。
- v4 READY 记录 3000 个训练任务、五轮各 600、744 frozen / 694 scored / 50 pending，状态仍是 `prepared_not_executed`：`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json:2-23`。
- 当前 694 由 BFCL-v3 152、HumanEval+ 164、MBPP+ 178、HotpotQA-dev 100、2Wiki-dev 100 构成；ACE 50 跳过。配置与计数见 `reproduction/configs/validation_744.json:2-13,36-43` 和 `reproduction/configs/train_4b.json:90-93,127-134`。
- skip-ACE 的实现会要求五个非 ACE benchmark、694 个 scored、overall 不 complete 且不得出现 ACE result：`RSIFlow/validation_skip.py:22-43`。这支持“694/744 partial”口径，不是 744 完整评估。
- launcher 的 8271–8274 ports 会按逻辑 GPU 0–3 重建 replicas，覆盖 base 的 807x 地址：`reproduction/configs/train_4b.json:96-101`、`RSIFlow/task_adapter.py:57-68`。
- Meta 最小真实 smoke 的最终 accepted 证据是 A100 Meta-only retry `pt-x6b4ista`，不是 A800 pass，也不是五轮可靠性证据；combined job 整体 FAILED 必须保留：`reproduction/PROGRESS.md:30-36`。
- 数据仅是公开数据同协议重构；`clean_independence_audited=false`、`exposure_unknown` 和 EnvScaler 共享环境定义等保留意见仍成立：`reproduction/audit/cohort-release-v4.json:3-5,79-91`。在当前“同协议重构”范围内这些不是新增启动 blocker，但不得改称作者原始队列或完整独立性通过。

## 3. Critical

### Critical 1 — 五类 scored benchmark 的最小 smoke 未闭环，现有“completed”项混有非 benchmark

**证据与复现依据**

1. 五个实际会评分的 benchmark 是 BFCL-v3、HumanEval+、MBPP+、HotpotQA-dev、2Wiki-dev；ACE 被跳过。见 `reproduction/configs/train_4b.json:93,127-134` 和 `RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json:5-10,24-32`。
2. 公开状态把 `candidate`、`Codex dynamic/resume` 放进 `benchmark_smoke.completed`，但它们不对应任何 validation benchmark ID：`web/data/reproduction.json:265-269` 对照 `reproduction/configs/validation_744.json:2-9`。
3. HumanEval+ 证据是 canonical/wrong 答案的 official comparator，单任务且不是模型分数：`reproduction/PROGRESS.md:34`。BFCL 是恰好两次受控 native RPC，也不是模型分数：`reproduction/PROGRESS.md:35`。
4. HotpotQA/2Wiki 记录只是 perfect-one 金答案输入和官方 evaluator 链路：`reproduction/audit/smoke/qa-v1/hotpotqa_dev-perfect-one.json:1`、`2wiki_dev-perfect-one.json:1`；其口径见 `reproduction/audit/reviews/preflight-main-r2.md:12`。没有真实 Qwen 生成、检索、预测文件到 official score 的完整最小链路。
5. `reproduction/configs/report-evaluators-v4.json:19-33` 虽有 MBPP+ spec 和 hash，但 spec 存在不等于 smoke 执行；当前汇总仍明确为 partial：`reproduction/RETROSPECTIVE.md:260`。

**关闭条件**

为五个 scored benchmark 分别保留最小、可定位、带 hash 的真实报告，至少证明实际 Task checkpoint 预测、对应 official/native evaluator、任务计数、基础设施错误分类和退出码。ACE 继续标记 skipped/pending，不为凑第五类把 candidate 或 Codex 改名为 benchmark。smoke 不进入 SFT、候选接受或 Meta 学习，也不得宣称模型 benchmark 分数。

### Critical 2 — 四卡 Qwen 只有已停止的历史诊断，当前健康门没有证据

**证据与复现依据**

- 成功报告的范围是 `four_real_bf16_services_and_one_short_generation_not_a_benchmark`，同时明确服务已停止、显存已释放：`reproduction/audit/smoke/model-service-r3-result.json:22-29`。
- registry 最终 phase 是 `services_stopped`：`reproduction/audit/smoke/model-service-r3-registry.json:2-4,162-164`。
- 公开状态也写明 `current_service_running=false`：`web/data/reproduction.json:245-253`，并把正式 Qwen 服务列为未运行：`reproduction/RETROSPECTIVE.md:249`。

**关闭条件**

在获授权的目标作业中重新启动并验证四个逻辑 GPU 0–3 replica；逐个记录 GPU、BF16 checkpoint、weights manifest、8271–8274 绑定、健康检查和短生成，再与同一作业的启动衔接。启动前对应卡至少 20,000 MiB 空闲；MODEL 更新前仍须执行停服务及每卡至少 40,000 MiB 的既有约束。历史 PID/端口存活不能替代本次验证。

### Critical 3 — 文档中的 `--check` 和默认启动脚本并不选择当前 694 机器配置

**证据与复现依据**

1. `RSIFlow_4B/launch.py` 进入共享 `launcher.main('4b')`，而共享 launcher 默认路径是 `RSIFlow/configs/train_4b.json`，只有显式 `--config` 才覆盖：`RSIFlow/launcher.py:9,12-27`。
2. 默认文件仍是 `/path/to/...` 占位配置，`skip_acebench=false`、run name 为 `eval744`，v1 data release，且 socket 名还写成 8B：`RSIFlow/configs/train_4b.json:8-9,90-95,153-165`。机器文件才是 `skip_acebench=true`、`eval694`、v4：`reproduction/configs/train_4b.json:90-93,153-165`。
3. 因此 `reproduction/PROGRESS.md:47`、`reproduction/REPRODUCTION.md:124`、`reproduction/RETROSPECTIVE.md:262` 和 `AGENTS.md:148-150` 所写的裸命令 `python RSIFlow_4B/launch.py --check` 不能证明当前 694 配置通过。
4. 实际启动脚本同样没有补 `--config`，还固定加入 `--prepare-data --detach`：`RSIFlow_4B/start_5round_600.sh:3-4`。在 ACP 中默认 detach 也与必须前台保持控制器的运行约束冲突：`reproduction/RETROSPECTIVE.md:269`。
5. `--check` 本身只输出路径存在性、rounds 和每轮任务数等 facts，不检查 skip-ACE、694 计数、validation manifest 内容、评测器可执行性、GPU 或 API：`RSIFlow/launch_meta.py:507-527`。

**关闭条件**

后续获授权预检必须显式传入绝对机器配置，例如：

```bash
python RSIFlow_4B/launch.py \
  --config /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/configs/train_4b.json \
  --check
```

保存输出并确认 `config` 正是该文件，再独立核对 effective config 的 v4、`skip_acebench=true`、694/744、五个 scored benchmark、validation manifest 和输出目录。正式运行命令也必须显式选择同一机器配置；ACP 控制器保持前台，不能直接使用当前固定 detach 的脚本。修正文档命令前不得把裸 `--check` 记为当前 694 gate pass。

### Critical 4 — 声明的 30 天全局预算没有进入有效 runtime 配置，实际 deadline 是 base 的 96,245 秒

**证据与复现依据**

- 机器 JSON 声明 `max_wall_seconds=2592000`（30 天）：`reproduction/configs/train_4b.json:88-89`。
- base JSON 却是 `max_wall_seconds=96245`（约 26.73 小时）：`reproduction/configs/base_4b.json:25-28`。
- `TaskAdapter._config()` 的 `base.update(...)` 传播 rounds、output_root、training timeout 等，但没有传播 launcher 的 `max_wall_seconds`：`RSIFlow/task_adapter.py:56-72`。
- pipeline 将 `config.max_wall_seconds` 作为全局 `max_wall_time`：`RSIFlow/runtime/rsiflow/task_meta/pipeline.py:505-507`；BudgetManager 持久化 UTC deadline，停机时间也计入，恢复时不允许改变预算：`RSIFlow/runtime/rsiflow/task_meta/loop.py:34-62`。

**影响与关闭条件**

按当前静态控制流，五轮全局预算不是配置表面声明的 30 天，而是约 26.73 小时；恢复还会保留这个较早 deadline。启动前必须以有效配置构造/聚焦测试证明预期预算，明确授权 wall-time、单次 SFT 604,800 秒、Meta 2 小时/63 requests/896,000 output tokens 及 provider 900 秒 response timeout 的组合语义（`reproduction/configs/train_4b.json:88-89`；`reproduction/configs/base_4b.json:63,86-95`）。若需要代码修复，应另走 TDD 和审阅，不能在本只读任务中静默改配置。

## 4. Important

### Important 1 — final-only benchmark smoke 不覆盖训练域、配对候选评估或 MODEL/SFT 路径

- validation 设置明确 `frequency=after_final_round`、`feedback_to_training_or_meta=false`：`reproduction/configs/validation_744.json:10-13`；`final_only` 还移除 A0 和非最终轮验证：`RSIFlow/launch_meta.py:470-485`。
- 五轮训练实际每轮包含 EnvScaler 200、DeepCoder/TACO 200（两种 code 模式）、HotpotQA 100、2Wiki 100：`reproduction/configs/train_4b.json:14-82,108-113`。BFCL/HumanEval+/MBPP+ final evaluator smoke 不会执行这些训练任务；QA 金答案 evaluator 也不覆盖训练检索 rollout。
- 项目明确没有运行五轮或 SFT：`reproduction/PROGRESS.md:8-10,47-48`。因此不能把 final-only smoke 写成训练领域已覆盖，更不能推断四卡 DDP SFT、merge、reload 和 same-round 配对评估可用。

建议启动前增加独立的训练侧最小 smoke：至少覆盖 EnvScaler、DeepCoder/TACO STDIO 与 function、HotpotQA、2Wiki 的真实 rollout/评分/回执，以及一次受控 positive-only 四卡 LoRA SFT→merge→四服务 reload→恢复检查。若不做，必须由用户明确接受该未验证风险；不能用 final benchmark 的“五类”名称掩盖训练侧空白。

### Important 2 — Meta/API 仅有最小 A100 成功证据，ACE skip 合法但不等于完整外部依赖已关闭

- combined Meta 曾因 HTTP 502 和 incomplete SSE 失败，唯一成功是单独 retry：`reproduction/PROGRESS.md:30-32`。这证明受控 Responses/native tools/thread resume 路径曾工作，不证明目标 A800 作业、长时间、多轮请求和预算耗尽行为可靠。
- ACE 跳过是明确的 partial 协议，不需要为当前 694 启动 ACE 用户模拟 API；但 50 个任务始终 pending，不能转为完整 744 或通过 ACE 能力验证：`reproduction/configs/train_4b.json:93`、`RSIFlow/runtime/data/rounds_3000_5x600_v4/READY.json:5-10`。

启动方案需固定安全注入方式、完整运行请求/token/费用上限、外部 provider 失败分类、有限重试和“再失败即保留 blocker”的停止规则。不得读取或打印 key，不得把 Meta-only A100 retry 写成 A800 或五轮 API 稳定性通过。

### Important 3 — 实际 run 的输出、恢复和存储审批尚未形成可执行验收单

- 默认 run path 是 `output_root/runs/run_name`：`RSIFlow/launch_meta.py:506`；当前机器值为主工作树 `Rollout_logs` 下的 `rsiflow_skill_4b_600x5_eval694`：`reproduction/configs/train_4b.json:90-92`。
- central Meta 状态按 resolved full run path 绑定 owner，并保留 `meta_session`、`meta`、receipts/transport：`RSIFlow/launch_meta.py:130-159`。最小 smoke 的 same-thread/no duplicate bootstrap 只证明该机制的一次受控恢复，不是实际五轮 run 的中断恢复。
- 本审阅时主工作树尚无 `Rollout_logs/` 或 `Meta_logs/`，所以没有发现旧 run 冲突；这也不是存储容量、保留周期或长跑恢复已批准的证据。项目仍列出安全 API 注入与预算、长跑和输出存储审批：`reproduction/RETROSPECTIVE.md:272`、`web/data/reproduction.json:301-306`。

启动前需明确 fresh run 还是 resume、唯一 run dir、Meta central 路径、磁盘容量/保留与私有轨迹访问边界。fresh run 不得复用旧状态；resume 必须同命令、同配置、同 resolved run dir，先核对 active state、workflow events、receipts、running jobs 和原 persisted deadline，不重复 bootstrap，不覆盖 attempts。应保存一次真实 run 的停止/恢复演练证据，而不仅是 smoke 报告。

### Important 4 — 机器 JSON 的独立 socket 名不是有效隔离配置；只能坚持单流水线方案

- 机器文件声明 `meta_worker_socket=/tmp/rsiflow_4b_meta_worker.sock` 和对应 relay：`reproduction/configs/train_4b.json:94-95`。
- backend schema 实际字段是 `meta.remote_worker_socket` / `meta.remote_relay_socket`：`RSIFlow/runtime/rsiflow/task_meta/meta_backends/contracts.py:53-54`，实际 RPC 也读取这些字段：`remote_execution.py:39-46,147-150`。
- base 仍使用 `/tmp/rsi_meta_worker.sock` 和 `/tmp/rsi_meta_relay.sock`：`reproduction/configs/base_4b.json:76-79`；`TaskAdapter._config()` 没有把 launcher 的两个 `meta_*_socket` 映射进去：`RSIFlow/task_adapter.py:58-84`。

所以“机器 JSON 里名字不同”不是隔离证据。当前只允许已记录的单条 4B、逻辑 GPU 0–3 顺序方案；不得并行 4B/8B 或两套四卡流水线。若未来需要并行，必须另行实现并测试物理 GPU、有效 socket、ports、output roots、service registry 和恢复隔离。现有 bwrap smoke 通过不替代这些资源隔离要求。

## 5. Minor

### Minor 1 — 部分 accepted 原始 smoke 报告不在主工作树，文档相对路径需要限定工作树或归档位置

`reproduction/PROGRESS.md:30-36` 和 `web/data/reproduction.json:73-138` 使用 `reproduction/logs/...` 相对路径，但本审阅在主工作树未找到 final Meta/candidate/BFCL/EvalPlus 报告。只读核对在 `.worktrees/bwrap-isolation` 中找到它们，SHA256 分别与已发布值一致：

- final Meta：`a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`；
- candidate：`56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`；
- BFCL：`ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`；
- EvalPlus：`537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`。

这不妨碍把报告作为历史证据，但引用时应写明实际工作树/归档位置，避免在主 checkout 中把“路径存在”当作已复核。不得为消除此问题复制覆盖受保护的审计账本或公开 raw logs。

## 6. 启动前最小验收顺序

1. 修正并执行显式机器配置的 `--check`，同时核验 effective config；解决 Critical 3、4。
2. 在授权 smoke 中补齐五个 scored benchmark，并单独覆盖训练域与 SFT/reload；保存脱敏报告和 hash。
3. 在目标作业重新验证四卡 Qwen 当前健康、显存门槛和资源释放；不沿用历史 PID。
4. 固定单流水线资源方案、前台控制器、安全 API 注入、费用/wall/storage 上限及停止规则。
5. 明确 fresh/resume，验证实际 run 的输出、owner、jobs、active state 和非破坏性恢复。
6. 对 Critical/Important 做 scoped re-review；只有全部关闭且四道门均为 PASS，才可另行申请五轮实验。

本报告不授权也不暗示已启动五轮、SFT、ACE、最终 694/744 或任何模型 benchmark。
