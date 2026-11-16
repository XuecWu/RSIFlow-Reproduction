# Task F training-side runner scoped review

**Spec：FAIL；质量：CHANGES。** 未发现 Critical；以下 Important 会导致真实 S1/S2/S3 失败或削弱执行安全。

## Findings

### Critical

无。

### Important

1. **S1 服务启动配置类型错误 — `reproduction/scripts/training_side_smoke.py:246`。** 配置加载为 `SimpleNamespace`，但生产 `start_services()` 调用 `config.model_dump()`，因此真实启动会报 `AttributeError`。测试把整个服务层 mock 掉，掩盖了此问题。

2. **S1 要求不存在的 trajectory 字段 — `reproduction/scripts/training_side_smoke.py:399`。** 强制要求 `trajectory_path`；生产 rollout 将完整回执持久化，却不在训练行中产生该字段。测试夹具自行添加该路径，且没有创建对应文件。正常生产 rollout 会被拒绝，也未真正验证持久调用证据。

3. **SFT 验收与生产输出合同不一致 — `reproduction/scripts/training_side_smoke.py:480`、`:494`、`:497`。** 验收读取 `ranks/num_train_epochs/loss/lora_changed/merged_weight_changed`，生产实际输出 `rank_records/num_train_epochs_completed/training_loss/changed_lora_tensors/merged_probe_changed_elements`；GPU receipt 使用 `replica_gpus`，而非 `replicas`。此外，比较 Harness 路径相等，但生产 `clone_task()` 会复制 Harness 到 child 目录。正常成功 SFT 会被判 incomplete。

4. **S2/S3 短生成解析必然失败 — `reproduction/scripts/training_side_smoke.py:518`。** 按 `response["choices"]` 取答案，生产 `LocalTaskClient` 返回归一化 `response["message"]`。只读 mock 复现得到 `KeyError: 'choices'`；现有 FakeClient 返回错误合同，因此伪绿。

5. **已发现非法 child 仍启动服务 — `reproduction/scripts/training_side_smoke.py:563`、`:570`。** 收到验收 `problems` 后仍先执行 `ensure_services()`、健康检查和生成，之后才阻断。child 越界、child=base 或证据不符时，应在任何服务副作用前失败。

6. **parent 证据未封存，SFT 可消费修改后的轨迹 — `reproduction/scripts/training_side_smoke.py:449`、`:547`。** rollout receipt 仅保存轨迹、performance、state 路径，没有文件哈希；SFT 阶段直接重读，再作正例筛选，没有重验完整五条及原调用证据。`_bindings()` 也没有绑定这些产物、实际基础配置或生产实现，不能满足阶段间防篡改合同。

7. **缓存失败可返回成功退出码 — `reproduction/scripts/training_side_smoke.py:412`、`:530`、`:598`、`:671`。** 对任何 final receipt 都直接返回 `already_completed`，既不重新核验 binding，也不保留失败语义；CLI 将其视为成功。因此首次 incomplete/零正例阻断后再次执行，可退出 0。

8. **阶段锁缺少共同互斥与锁内复查 — `reproduction/scripts/training_side_smoke.py:288`。** 使用各阶段独立锁，状态检查发生在取锁之前。等待或延迟进程可以基于过期 pending 状态再次执行，并与后续阶段争用同一 registry；其 finally cleanup 可能停止另一阶段正在使用的服务。

9. **SIGTERM 不会进入清理流程 — `reproduction/scripts/training_side_smoke.py:647`。** 没有安装信号处理器，尽管导入了 `signal`。默认 SIGTERM 会直接终止进程，绕过 finally，可能遗留独立 session 的服务/训练进程；现有测试未覆盖设计要求的 SIGTERM/SIGINT 清理。

10. **registry 路径归属不足以证明进程归属 — `reproduction/scripts/training_side_smoke.py:303`。** 只判断 registry 文件位于输出根内。若该文件被替换为另一输出根的有效服务 registry，生产 `stop_services()` 的 PID identity 和脚本路径检查仍可能通过，从而停止外部服务；应核对进程配置/启动记录确实属于本 smoke 根。

### Minor

- **覆盖结论过强 — `reproduction/audit/reviews/training-side-smoke-report.md:79`。** 当前测试使用与生产不一致的服务、trajectory、trainer metrics 和客户端响应夹具，不能支持“真实调用链、恢复、并发及清理合同已验证”的表述。

## 真实执行前必须修复项

上述 10 项 Important 均须在真实 S1/S2/S3 执行前处理，并增加使用生产接口合同的回归证据：有效配置类型、真实 rollout 持久回执、trainer/GPU metrics、LocalTaskClient 响应、非法 child 的副作用前阻断、阶段证据封存、失败缓存退出语义、共同阶段锁与锁内复查、信号清理，以及 registry 中进程的实际输出根归属。同步修正报告中过强的覆盖表述。

固定五条选择、mini release 和零正例阻断方向正确；采纳主 agent 提供的 v5 plan 字节一致及零调用证据。

## 未覆盖边界

- 本轮仅只读检查和无服务的接口复现；没有修改 runner、测试、配置或 frozen assets。
- 没有运行真实 GPU/SCO/API、rollout、SFT、merge/reload 或 recovery，也没有检查最终评分。
- 没有重复全套测试；主 agent 正在独立复跑。离线测试通过和真实资产 plan 均不能替代 S1/S2/S3 的真实执行证据。
- 本报告仅记录上一轮已有的完整审阅结论，本次写入未重新审查。

## Task F fix round 1 scoped re-review

**总体 CHANGES。** 未发现 Critical；原 10 项中仍有 4 项未完全关闭，并新增 1 项 Important。

| 原项 | Verdict | 结论 |
|---|---|---|
| 1 PipelineConfig | ADDRESSED | 返回真实 PipelineConfig，支持生产 model_dump()。 |
| 2 rollout 持久合同 | ADDRESSED | 已移除虚构 trajectory_path，检查生产主回执；但新增工具响应兼容问题，见下。 |
| 3 SFT metrics/Harness clone | NOT ADDRESSED | 字段和 clone 比较已修，但缺失训练配置及样本覆盖证据仍可通过。 |
| 4 message 客户端 | ADDRESSED | 已使用 response["message"]。 |
| 5 非法 child 提前阻断 | ADDRESSED | problems 非空时不再调用后续服务及生成。 |
| 6 parent 证据 hash | NOT ADDRESSED | 轨迹已封存，但实际训练输入 state/base config 未完整绑定。 |
| 7 缓存失败语义 | ADDRESSED | 失败缓存保留失败状态，CLI 返回非零；成功缓存检查 plan binding。 |
| 8 共同锁及锁内复查 | ADDRESSED | 三阶段共用锁，并在锁内复查阶段状态及 binding。 |
| 9 信号清理 | NOT ADDRESSED | 服务启动信号已处理，SFT 独立训练进程组仍可能遗留。 |
| 10 registry 进程归属 | NOT ADDRESSED | 仍信任 registry 自述，且服务启动前没有执行归属检查。 |
| 覆盖 Minor | NOT ADDRESSED | 已承认旧夹具问题，但报告“10 项全关闭”及安全覆盖结论仍过强。 |

### Important findings

- **原 #3 — `reproduction/scripts/training_side_smoke.py:657`**：缺失 resolved_training_config 或其中 preset 字段时跳过核验；`:646` 仅要求 sampled_indices 是列表，空列表、未覆盖正例也能通过。应拒绝缺失证据并核对样本覆盖。
- **原 #6 — `reproduction/scripts/training_side_smoke.py:530`**：封存集合遗漏 a0.json 和 launcher 引用的实际 base config；`:748` 的生产 SFT 会重新读取这些文件。修改 parent state 的 checkpoint/Harness 或 base 配置后，现有 parent hash 检查仍可通过。
- **原 #9 — `reproduction/scripts/training_side_smoke.py:737`**：信号直接异常退出 adapter.sft()；其生产 subprocess.run() 中断可杀死 phase 进程，但 DDP 使用独立 session，runner 未终止并等待该训练进程组。当前信号测试只在 mock ensure_services 中发信号，不能证明训练中断安全。
- **原 #10 — `reproduction/scripts/training_side_smoke.py:343`**：核验的是 registry 中记录的 command/config，未对照存活 PID 的实际命令行；更直接的是 `:592`、`:758`、`:816` 调用 ensure_services() 前没有归属检查，而生产 ensure_services() 自身可先调用 stop_services()。替换 registry 后，可能在 finally 的拒停检查之前已经停止外部服务。
- **新增 Important — `reproduction/scripts/training_side_smoke.py:482`**：强制每次响应含非空文本，拒绝合法的纯 tool_calls 消息。已用生产 qwen_message() 无服务复现：有效工具调用产生空 content，随后被判“lacks a real model message”，会使正常 EnvScaler rollout incomplete。

### Minor

- **`reproduction/audit/reviews/training-side-smoke-report.md:151` 起**：关闭表应保留上述未关闭边界，尤其训练中断和真实 PID 归属，不能据当前 mock 测试宣称全部关闭。

### 新 breakage 与边界

新增 breakage 为上述纯 tool_calls 合法响应被拒绝的 Important；未发现 Critical。

本轮只读，未修改文件、运行真实评分或重复全套测试；采纳提供的 focused 59 passed、相关 155 passed/2 skipped、reproduction 66 passed，全量等待主 agent 结果。本节仅追加上一轮已有复审结论，本次写入未重新审查。

## Task F fix round 2 scoped re-review

**总体 CHANGES。** 未发现 Critical；5 项中 3 项仍未关闭，并发现一项会阻断正常 S2 的问题。

- **Preset 与样本覆盖 — NOT ADDRESSED**：`reproduction/scripts/training_side_smoke.py:781` 要求四个 rank 均非空且全局索引不重复，并与筛选前的 `len(positives)` 完全一致。生产 trainer 会先按长度过滤、再去重；四卡分布式采样也可能为不足四条或不能整除四的样本补齐重复。因此正常训练证据会被拒绝，需以生产 `sft_positive.jsonl`、去重与采样报告中的实际训练数据集核对覆盖。
- **a0/base/Harness 输入封存 — ADDRESSED**：`reproduction/scripts/training_side_smoke.py:649` 已在 rollout 后封存并在 SFT 前重算 state、实际 base config、launcher 和 Harness hash；对应篡改负例覆盖了原 finding。
- **SFT 信号及 PGID 清理 — NOT ADDRESSED**：`reproduction/scripts/training_side_smoke.py:446` 只等待组长 PID 消失；组长先退出而 rank 子进程仍在同一进程组时会宣称清理完成。另在 phase 启动 DDP 后、写入 `training_pid` receipt 前被中断时，`:426` 找不到需清理的进程组。当前测试只生成单个 sleeper，未覆盖这两个真实进程树情形。
- **ensure 前及 PID cmdline 归属 — NOT ADDRESSED**：`reproduction/scripts/training_side_smoke.py:904` 调用生产 `adapter.sft()` 前未检查 registry；生产训练相位会先执行 `stop_services()`。因此替换 registry 仍可能在后续 `:916` 检查前停止外部服务。已有检查覆盖 runner 自己的 `ensure_services()` 路径。
- **纯 tool_calls — ADDRESSED**：`reproduction/scripts/training_side_smoke.py:564` 接受合法的非空工具调用，新增测试使用生产 `qwen_message()` 构造纯工具消息。

**新增 Important / breakage** — `reproduction/scripts/training_side_smoke.py:904`：S1 finally 已停止 base 服务；S2 直接调用 `adapter.sft()`，生产训练相位再次要求先 `stop_services()`，并不在训练前重启 base 服务。若 registry 为 stopped，可能在未实际完成设计要求的 base 启动和验证后继续训练，或因资源检查失败；应在 S2 训练前先核验归属并启动、验证 base 四服务。

本轮只读，未修改文件、执行真实评分或重复全套测试；采纳提供的 focused 74 passed、相关 170 passed/2 skipped、reproduction 66 passed，全量结果待主 agent 复跑。本节仅追加上一轮已有复审结论，本次写入未重新审查。

## Task F fix round 3 scoped re-review

**总体 CHANGES。** 未发现 Critical；三项中一项已关闭，两项仍有缺口。

- **生产 dedup/padding 采样覆盖 — NOT ADDRESSED**：`reproduction/scripts/training_side_smoke.py:864` 已允许 padding 重复，并按最终数据集索引并集验覆盖，修复了正常训练被误拒的问题。但 selection 为空时直接跳过对账，`sft_positive.jsonl` 缺失时也跳过行数检查。缩小自述的 `positive_trajectories` 和采样集合后，仍可缺少生产筛选证据而通过。应要求这些证据存在并相互一致。
- **全 PGID 清理及 receipt gap — NOT ADDRESSED**：`reproduction/scripts/training_side_smoke.py:483` 已等待整个进程组，且有“有 receipt、leader 先退”和“无 receipt、leader 存活”测试。但 `:468` 的 discovery 只接受存活组长；**无 training_pid receipt 且 leader 已退出、rank 仍存活**时仍会漏掉整组。需从可证明归属的存活 rank 发现 PGID，并补组合负例及外部进程拒杀测试。
- **adapter.sft 前归属检查及 base ensure/verify — ADDRESSED**：`reproduction/scripts/training_side_smoke.py:1000` 已先核验 registry、启动并验证 base GPU 0–3，再调用生产 SFT；对应顺序和外部 registry 拒绝测试覆盖了原项与上轮新增问题。

**本轮新 Important / breakage**：上述采样修复引入了 selection/positive 文件缺失时跳过核验的路径。除此未发现新增 Critical/Important。

本轮只读，未重跑全套或执行真实评分；采纳提供的 focused 80 passed、相关 176 passed/2 skipped、reproduction 66 passed，全量待主 agent 复跑。本节仅追加上一轮已有复审结论，本次写入未重新审查。

## Task F fix round 4 scoped re-review

**总体 PASS。**

- **采样证据强制对账 — ADDRESSED**：`reproduction/scripts/training_side_smoke.py:882` 拒绝缺失或空 selection、缺失 positive 文件，并核对实际行数、input/unique 数和 `positive_trajectories`；新增负例覆盖缺文件、空证据、缩小自述和计数不一致。
- **无 receipt、leader 已退出时的 PGID 清理 — ADDRESSED**：`reproduction/scripts/training_side_smoke.py:455` 从存活 rank 的实际 cmdline 发现 PGID，清理前复核归属并等待整组退出；测试覆盖组合情形和外部 rank 拒杀。

**新 breakage：本轮未发现新增 Critical/Important。**

本轮只读，未重跑全套或执行真实评分；采纳 focused 88 passed、相关 184 passed/2 skipped、reproduction 66 passed，全量待主 agent 复跑。本节仅追加上一轮已有复审结论，本次写入未重新审查。
