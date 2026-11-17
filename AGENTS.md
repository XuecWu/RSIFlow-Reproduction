# RSI-Flow Agent 工作指南

本文件适用于整个仓库。上游调研基线为 commit `496d3cfb9ff39997d107d0ff29e746b07e638180`（GitHub 上游 `main`）。**当前交付分支为 `reproduction/4b-main8`**，隔离分支 `reproduction/4b-bwrap` 已 fast-forward 合入；两个复现分支已备份到内部 GitLab（`gitlab` remote），`origin` 仍是 GitHub 上游且不向其推送，GitLab 既有 `main` 不强推/不覆盖，GitLab CI 未验证。环境、4B 模型、公开数据同协议重构、显式 bubblewrap 隔离与最终 A100 最小 smoke 已验证，独立安全审阅已关闭；尚未运行五轮演化、SFT、最终 694/744 或模型 benchmark。当前状态以 `reproduction/PROGRESS.md` 和 `web/data/reproduction.json` 为准，本次调研/隔离修复/SCO 排障与交付经验见 `reproduction/RETROSPECTIVE.md`。具体 HEAD、服务 PID 与公网 URL 会随交付变化，一律以 `reproduction/PROGRESS.md`、`web/RESULTS.md`、`web/OPERATIONS.md` 的实测记录为准，不在本指南写死。

## 1. 项目是什么

RSI-Flow 用持久化 Meta agent 迭代改进 Task agent。Task 状态由模型 checkpoint、可执行 Harness 和任务 artifacts 组成；Meta 根据父代轨迹和既有经验，每次尝试只选择 HARNESS、MODEL、ARTIFACTS 之一构造一个候选，在同一轮完整任务集上配对评估，并明确决定接受候选还是保留父代。

当前发布是 **skill-based variant**：Meta 的实验方法论与验收协议固定，跨轮积累写入技能记忆；操作库不是封闭白名单，Meta 可按协议修复或新增 operation 并使用原生 Codex 工具，但不得自主改写核心实验与验收规则。可执行 Meta-program evolution 变体和历史实验副本不在本发布中。4B/8B 入口共用代码和协议，仅 Qwen3 Task checkpoint 不同。

## 2. 目录与职责

- `RSIFlow/launcher.py`：选择 `4b`/`8b`，可选准备数据，然后调用 `launch_meta.py`。
- `RSIFlow/launch_meta.py`：实验入口；负责配置检查、运行目录、持久 Codex app-server、GPU 等待和恢复。
- `RSIFlow/persistent_meta.py`：维护同一 Codex thread、原生动态工具、持久 jobs 与断点续接。
- `RSIFlow/controller_tools.py`：文件导向工具分发和事实回执；不负责选择组件或自动接受候选。
- `RSIFlow/task_adapter.py`：把 launcher JSON 规范化为有效 runtime 配置，执行 bootstrap、rollout、SFT、Harness、Artifacts 和评估阶段。
- `RSIFlow/meta_loop.py`：旧 CLI 循环与主线约束；当前 `PersistentMeta` 使用 app-server/native tools。
- `RSIFlow/skill_memory.py`、`RSIFlow/META_SKILLS.md`：技能检索、追加、维护与版本语义。
- `RSIFlow/experiment_progress.py`、`monitor_meta.py`：从回执重建阶段进度和监控信息。
- `RSIFlow/prepare_600_744.py`、`data_protocol.py`、`evolution_protocol.py`：冻结队列重打包、hash、角色与 disjoint 约束。
- `RSIFlow/runtime/rsiflow/task_meta/`：共享 Task engine、领域适配、检索、GPU 阶段、训练、报告和存储。
- `RSIFlow/runtime/scripts/`：四卡服务、四卡 DDP SFT 等实际进程入口。
- `RSIFlow/seed_harness/`：初始 Harness bundle 与 manifest。
- `RSIFlow/upstream/RSIFlow_task/`：适配后的 Task 组件、Harness production templates 和参考实现。
- `RSIFlow/configs/`：实验、backbone、Codex 和验证配置；含大量 `/path/to/...` 占位符。
- `RSIFlow/tests/`：离线测试，覆盖协议、配置等价、技能记忆、控制工具、恢复和评估隔离；real-isolation 用例逐项显式选择 bwrap。
- `RSIFlow_4B/`、`RSIFlow_8B/`：薄启动入口，最终都调用共享 `launcher.main()`。
- `third_party/`：评测工具与 Codex provenance；保留对应许可证和 notices。
- `reproduction/PROGRESS.md`、`reproduction/REPRODUCTION.md`：复现进度与显式 bwrap 隔离/smoke 手册（命令与已接受证据）。
- `reproduction/RETROSPECTIVE.md`：本次调研、隔离修复、SCO 排障与交付复盘——阶段历程与 ruling、真实证据与计数归属、失败根因（区分已确认根因与外部 provider/控制面故障）、可复用经验、五轮前置门与追踪方式。
- `reproduction/LEARNING.md`：持续维护的 Task/Meta 机制学习问答与完整流程图；后续学习结论按主题更新，代码机制变化时与正文、图示和维护日志同步修订，不冒充实验结果报告。
- `reproduction/audit/`：追加式 `operations.jsonl`/`key_results.jsonl`、`daily/`、`reviews/`、`evidence-hashes.json` 与 release 对账文件。**审计单根在主 checkout**；其他工作树内的同名账本可能已分叉（同号 sequence 不同语义），引用前需逐条对账，禁止覆盖或拼接。
- `reproduction/scripts/`：`audit_exec.py`/`record_operation.py`/`record_result.py` 审计包装、`build_status.py` 过程页快照、`install_bwrap.py` 与各最小 smoke 入口。
- `web/`：本次新增的中文调研/复现展示站、实际结果和操作日志。

## 3. 实际控制流

```text
RSIFlow_4B/launch.py 或 RSIFlow_8B/launch.py
  -> RSIFlow/launcher.py
  -> RSIFlow/launch_meta.py
  -> PersistentMeta + Codex app-server（同一 thread）
  -> NativeExperimentTools / SubprocessToolExecutor
  -> controller_tools.ControllerTools
  -> task_adapter.TaskAdapter
  -> runtime/rsiflow/task_meta 与 runtime/scripts
```

A0 固定初始 Task/Meta 状态。B1–B5 每轮主线为：

1. 在当前冻结 B_r 上运行完整父代 rollout。
2. 汇总所有结果，抽取最多 48 条代表轨迹：每领域 16 条，尽可能 8 成功/8 失败。
3. 检索相关技能、反例和上轮 context，保存 `decision.json` 与 `skill_use.json`。
4. 每次 attempt 只选一个组件、构造一个候选，写入新的 `round_N/attempts/attempt_K/`。
5. 在完全相同的 B_r 上运行候选，比较完整配对分数和逐任务差异。
6. Meta 明确决定 accept/retain；只有完整配对结果严格正增益才允许激活候选。
7. 每个 attempt 都记录事实 case、技能使用、预测与结果；拒绝时复用未变父代结果，在同一轮重新选组件，不进入下一轮。
8. 保存 Task/Meta snapshot 和简洁 context。`final_only` 下只在 B5 接受并快照后做独立 744 评估。

`compare_scores` 只报告事实，`activate_task` 只在 Meta 显式请求时改变 active Task。不要把控制器当成自动策略引擎。

## 4. 三类 Task 更新

- **HARNESS**：先 materialize 父代 bundle，依次完成模块定位报告、改进方向报告、完整候选生成，再调用验证；同一候选最多三次可执行检查。只跑 validator 不等于完整 production 流程。
- **MODEL**：只使用当前父代训练 rollout 中经评估验证的成功轨迹，执行一轮 positive-only LoRA SFT，合并为新 checkpoint；训练失败不得发布候选，应恢复最后有效 checkpoint。
- **ARTIFACTS**：修改已有 task submissions，当前协议用 `direct_submission` 直接重评分，不重新生成整条轨迹。

技能记忆 `meta/skills.jsonl` 是 append-only ledger，`skills_index.json` 可重建。记录类型包括 `case`、`rule`、`incident`、`rule_update`、`procedure`、`skill_maintenance`。引用技能不等于理解，关联不等于因果；置信度必须来自证据。

## 5. 数据与评估协议

每轮 600 个训练任务：EnvScaler 200、DeepCoder/TACO 200（120 STDIO + 80 function）、HotpotQA 100、2Wiki 100。五轮共 3000 个不同任务，seed=42；EnvScaler 环境定义可复用，但任务/内容不能重复。

最终独立评估共 744：BFCL-v3 152、ACEBench 50、HumanEval+ 164、MBPP+ 178、HotpotQA-dev 100、2Wiki-dev 100。评估只用于报告，不参与 SFT、候选接受或 Meta 学习；当前协议不包含 LiveCodeBench，也不做 A0 baseline validation。

`prepare_600_744.py` **只重打包已有 source manifests**，不是原始数据下载器或重新抽样器。它验证 source hash 和角色，生成 B1–B5、validation manifest、索引和 `READY.json`。重复准备只允许配置差异为 `base_config`/`run_name`；其他协议变化需要新 frozen release，不能覆盖旧目录。

若拿不到原始 source training/validation manifests，就不能声称精确复现原始任务队列。自行从原始数据重构只能标为“同协议重构”，需单独审批。

## 6. 有效训练配置与 GPU 约束

不要只看 `runtime/configs/base_*.json`。`task_adapter.TaskAdapter._config()` 会合并 launcher 配置并应用 `evolution_protocol.SFT_PRESET`。当前有效 MODEL 更新参数为：

- one epoch、`max_steps=-1`、learning rate `2e-6`；
- LoRA rank 8、alpha 16、dropout 0.05；
- target modules：q/k/v/o/gate/up/down projection；
- batch size 1、gradient accumulation 8、max length 16384；
- BF16、gradient checkpointing、4-rank DDP，训练/ rollout seed 42。

base JSON 中的 rank 16 和 learning rate `2e-5` 会被覆盖，不应写成最终有效值。

同理，launcher JSON 的 `max_wall_seconds`、`meta_worker_socket`、`meta_relay_socket` 现在会进入有效配置：分别覆盖 base 的 `max_wall_seconds` 与 `meta.remote_worker_socket` / `meta.remote_relay_socket`；**launcher 未提供这三个键时保留 base 值**。因此全局 wall 预算与 Meta socket 身份必须以有效 `PipelineConfig` 为准，不能只读 launcher 或 base 任一侧 JSON；机器 JSON 里写了不同 socket 名也不等于已隔离，仍只允许单条 4B、逻辑 GPU 0–3 顺序流水线。

GPU 是硬要求，无 CPU/mock 回退：

- 四个推理 replica 必须是逻辑 GPU 0/1/2/3，各自加载 BF16 checkpoint。
- 每个推理服务启动前，`start_multidomain_service.py` 要求对应卡至少 20,000 MiB 空闲。
- MODEL 更新前停止四个服务；`gpu_phases.py` 要求 GPU 0–3 每卡至少 40,000 MiB 空闲，然后启动四个 local DDP ranks。
- 服务健康检查同时校验 GPU、checkpoint 路径和 weights manifest，不能只看端口存活。
- launcher 的 4B/8B `ports` 当前都为 `[8271,8272,8273,8274]`，按顺序映射 GPU 0–3；base JSON 中其他端口会被 launcher 配置覆盖。
- 服务与训练子进程会自行重写 `CUDA_VISIBLE_DEVICES`。仅在父进程设置 `CUDA_VISIBLE_DEVICES=4,5,6,7` 不能把第二套流水线可靠映射到物理 4–7。

因此，未修改源码时优先使用单节点 4×A800 80GB，4B/8B 顺序运行。主分区 8×A800 若希望同时运行两套四卡流水线，除独立 ports、socket、output/run dirs 外，还需要可配置物理 GPU 映射及相应测试；不能直接沿用默认子进程环境覆盖逻辑。

## 7. 环境与外部资产

环境安装命令（已执行；实际解释器为 `/data/wuxuecheng/miniforge3/envs/rsiflow`，依赖 lock 在 `web/`）：

```bash
conda create -n rsiflow python=3.11 -y
conda activate rsiflow
python -m pip install -U pip setuptools wheel
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128
cd RSIFlow
python -m pip install -e './runtime[dev,pipeline]'
python -m pip install -r upstream/RSIFlow_task/requirements.txt
```

PyTorch/CUDA 组合来自现有 SCO 环境的已验证记录，仍须在目标 A800 作业中重新验证。评测器依赖冲突时使用独立 evaluation 环境。

完整运行另需：Qwen3-4B/8B 本地 checkpoints、原始任务和 source manifests、检索语料/索引、各 benchmark evaluator 与 official specs、匹配 Codex binary 及 source checkout、Meta API，以及 ACEBench 用户模拟 API。Codex provenance 固定 tag `rust-v0.153.4`、commit `3d2ee51ca2d5db578f328aa75e20aa22c0197c9a` 和二进制 SHA256 `56ef98ab4032d317ab26e9b5e5a175650717351edb16ed9cde0cb6d1734d62da`。本次 BFCL 固定 `ea13468e4423454d0c213704fb87cf7cb3990433`，使用独立 `rsiflow-bfcl` 环境，不降级生产依赖。

凭据只通过安全环境注入或私有文件提供，不写入仓库、网页或日志。`RSIFLOW_PYTHON` 只选择 launcher 解释器，不会自动替换 JSON 内的所有 interpreter 路径。复制配置到机器专用位置后再替换 `/path/to/...`，避免污染上游基线。自定义 OpenAI provider 仅允许登记的 `http://token.acerobotics.com/v1`、`gpt-6-astra`、`OPENAI_API_KEY`，不允许任意 endpoint 或模型 fallback。`PersistentMeta` 的原生线程模型和 provider 必须来自有效 base 配置的 `meta`，不得在控制流中另写一套硬编码身份。

真实隔离必须显式设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`；未设置时仅为上游兼容保留 legacy Landlock 默认。bwrap 失败不得回退 host、Landlock 或 `danger-full-access`，必须归类为 infrastructure error，不能记成模型零分。固定 bwrap 0.6.1 通过以下 installer 放入 Git 忽略的 `assets/isolation/`，binary SHA256 必须为 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`：

```bash
python reproduction/scripts/install_bwrap.py \
  --source /absolute/path/to/verified-bwrap-0.6.1 --root "$PWD/assets/isolation"
```

三类 profile 为 `candidate`（`candidate_session` 共用 seccomp）、`native_benchmark`、`persistent_meta`。候选只有专用 scratch 可写；native 仅保留两个 RPC FD、允许 thread-only clone/atomic rename；Meta 保留持久 `codex_home` 和 host-side jobs。真实 `OPENAI_API_KEY` 只在 host `ResponsesTransport`，sandbox 只有 `RSIFLOW_RELAY_KEY=pipe-only` 和私有 loopback relay。恢复必须采用同一 thread/job，不重复 bootstrap。

installer 的 ignored `assets/isolation/install-receipt.json` 同时固定 manifest/launcher 的相对路径和 SHA256；candidate/native/Harness 默认 backend 每次 launch 都必须重新验证，tamper 或 receipt 缺失/符号链接均 fail closed。Persistent Meta 继续使用既有 static trusted copies 和强制 hash。不要用 diagnostics 或 writable source 中的常量替代 receipt。

Persistent Meta 在旧内核上只对 `pidfd_open=ENOSYS` 启用后备：绑定 owned `start_new_session` PGID，使用 `/proc` PGID/start_ticks 双快照和逐 PID 信号前后核验，其他 errno 继续 fail closed，不回退 host 或宽泛 `killpg/pkill`。只有 ENOENT/zombie 算确认退出，未知读取或解析错误向上传播。`/proc` 后备仍有最终检查到信号之间的 PID 复用窗口；post-check 失败可能已发出部分信号并留下进程，必须按 runbook 做外部 identity-checked 调和。dev4 Linux 4.18 真实修复链路仍待实测，根因、提交和 scoped PASS 边界见 `reproduction/audit/reviews/dev4-pidfd-enosys-fix.md`。

生产 `prepare_codex_home()` 必须以原子 no-follow 方式创建并验证 0644 `installation_id`，保留有效 inode/content，拒绝 symlink、非普通文件和非法 UUID；smoke 不另维护 helper。`prepare_meta_storage()` 以 central `.run-owner.json` 绑定 resolved full run path：same-basename 不同 fresh run fail closed，same-path restart 保留状态，只有可证明 same-path 的 legacy links 才可采用无 owner 的旧目录。不得删除或重写历史 artifacts。

Codex native-shell descendant hygiene 尚未验证：legacy Landlock 不可用，nested vendor bwrap 与已审阅 anti-escape seccomp 冲突。批准的 smoke 只覆盖 `shell_tool=false` 的 construction-level hygiene；不得放宽 seccomp 或宣称 native-shell 通过。完整命令与证据见 `reproduction/REPRODUCTION.md`。

## 8. 后续命令与运行边界

以下命令用于离线检查和后续完整实验；离线检查已执行不代表五轮已运行：

```bash
# 完整离线测试（DeepCoder canary Task 5 兼容性修复当前实测：516 passed, 2 skipped；此前 Task 4 为 489 passed, 2 skipped，五轮前 preflight 为 299 passed, 2 skipped）
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
cd ..

# 当前 694 配置预检仍是五轮前门槛，尚未通过。必须显式传绝对机器配置并使用 rsiflow 解释器；
# 裸 `python RSIFlow_4B/launch.py --check` 会落到 RSIFlow/configs/train_4b.json 占位配置（skip_acebench=false、eval744、v1），不能证明 694。
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python RSIFlow_4B/launch.py \
  --config /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/configs/train_4b.json --check
# --check 只输出路径存在性/rounds/每轮任务数等 facts，不校验 skip-ACE、694 计数、validation manifest 内容、
# 评测器可执行性、GPU 或 API；返回 0 不等于 694 协议或评估链路已验证，须另行核对有效配置。

# 数据准备；需要真实 source manifests
python RSIFlow/prepare_600_744.py --config /absolute/path/to/train_4b.json

# Candidate 最小 smoke
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
  python reproduction/scripts/candidate_bwrap_smoke.py --output reproduction/logs/candidate-new.json

# EvalPlus 单任务 official comparator smoke，不是 benchmark 分数
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD/reproduction/scripts" \
  python reproduction/scripts/evalplus_bwrap_smoke.py --config reproduction/configs/report-evaluators-v4.json \
  --output reproduction/logs/evalplus-new

# BFCL 两次受控 native RPC；使用机器专用 validation config
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python - <<'PY'
import json
from pathlib import Path
from tool_validation import check_native
config = json.loads(Path("reproduction/configs/validation_744.json").read_text())
check_native("bfcl_v3", config["tool_benchmarks"]["bfcl_v3"], Path("reproduction/logs/bfcl-new"))
PY

# Meta 离线检查；真实 smoke 另需安全注入 OPENAI_API_KEY 和独立授权
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" python reproduction/scripts/persistent_meta_bwrap_smoke.py --check
```

训练侧真实 smoke 的 `execute-rollout` 也必须显式设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`；缺失或错误时在 `TaskAdapter`、bootstrap 和服务副作用之前 fail closed，不得回退 legacy Landlock。首轮真实评分/S1 失败与修复证据见 `reproduction/audit/reviews/real-preflight-fix1-report.md`，离线测试通过不替代真实重跑。

`start_5round_600.sh` 固定加入 `--prepare-data --detach`，且不带 `--config`，因此**不能直接用于 ACP 正式控制器**：默认 detach 会让脚本返回后作业结束并清理进程，默认配置又会落到上游占位 JSON。ACP 中必须让控制器**以前台方式**运行，并**显式传入绝对机器配置**（`reproduction/configs/train_4b.json`），例如以 rsiflow 解释器直接调用 `RSIFlow_4B/launch.py --config <绝对机器配置>`（不加 `--detach`；数据已冻结时不要加 `--prepare-data`）。每个 backbone 使用新的 run dir；相同命令、相同配置和相同 run dir 用于 resume。恢复时先检查 active state、回执和 running jobs，不重复 bootstrap，不覆盖历史 attempts。五轮、SFT 与最终 694/744 仍未运行。

DeepCoder function 16 canary 是独立的有界 preflight：`reproduction/configs/train_4b_deepcoder_canary.json` 固定一轮、16 条 code-only function、无独立 validation、单 attempt，`retain_parent` 在比较、技能、context 和 snapshot 后以 `completed_retained` 终止，不重选或进入下一轮。dev4 的访问信息按 `reproduction/audit/reviews/dev-four-a800-access.md` 实时核实；真实 canary 只提供已验证的 detached supervisor 入口，不提供操作员 shell/tee 所在 PGID 的 foreground 启动备选。launcher 前台进程 4 小时监督、显式 bwrap、既有 Meta 凭据存在性检查、monitor、output/log/status、cleanup/resume 命令见 `reproduction/audit/reviews/deepcoder-canary-runbook.md`；该监督不自动停止独立 session 的 durable jobs/services/app-server，active jobs 未获自然最终回执或 identity-checked 调和前禁止 cleanup completed/resume。不要把动态 HEAD、PID 或 TCP 入口写入本指南。真实 attempt `0001` 已在 A0 前因 dev4 `pidfd_open=ENOSYS` 基础设施失败，尚无有效 canary 结果；修复后的真实 Linux 4.18 链路仍未实测，详见 `reproduction/audit/reviews/dev4-pidfd-enosys-fix.md`。结果必须报告 Meta 实际选择的组件；除非实际选择 MODEL 且父代自然正例存在并留下训练回执，否则不得声称 SFT，HARNESS/ARTIFACTS 也不能改记为 MODEL。该 canary 不是五轮或最终 694/744。

典型输出：

- `Rollout_logs/runs/<run>/`：Task trajectories、round/attempt 回执、候选、active state、snapshots、评估结果。
- `Meta_logs/<run>/`：集中 Meta session、skills/context 及链接；传输和部分大日志仍由 Rollout_logs 持有。
- `meta_session/RUN_STATUS.md`、`workflow_events.jsonl`：阶段与恢复证据。
- `meta/skills.jsonl`、`meta/context.json`：跨轮经验与简洁 handoff。

## 9. SCO 使用约束

后续 GPU 资源参考 `/data/wuxuecheng/sco_usage-1010.md`，不得复制其中的凭据示例。未修改源码的低风险方案是 dev `sensebot-a800-dev-liujianbo`、workspace `sensebot-a100-a800-liujianbo`、单节点规格 `N3lS.Ii.I60.4`（4×A800 80GB、48 vCPU、480 GiB）。

主分区只提交合规的 8 卡及倍数任务。若使用主分区 8 卡，先解决双流水线 GPU 映射、端口、socket、服务 registry 和 output_root 隔离，否则不要并行。提交前做短时 probe，任务不使用 `sleep inf`，多次提交至少间隔 5 秒，结束或失败后确认资源释放。

Task 6 的接受证据 `pt-zj1l9q1t` 是用户授权后的特殊 A100 smoke：外部 actor `huangsiyuan1` 提交，8×A100、N3lS+N2lS candidate specs、`root_mapping=true`，worker Succeeded；report SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 启动前失败，不得宣称 A800 pass 或把该提交归到 `main-kimi`。它仅作历史证据。

当前 `313b6bc` 的最终 A100 gates 已完成且 `root_mapping=false`：combined `pt-v6m10c4t` 整体/worker FAILED 仅因 Meta 外部 provider completion failure；candidate/Harness/BFCL exit 均 0。candidate SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`，Harness `1 passed in 23.26s`，BFCL `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4` 且两次 RPC/key hygiene 通过。combined Meta ordinal 0 HTTP 502、retry HTTP 200 incomplete SSE，failed report `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`。唯一 Meta retry `pt-x6b4ista` SUCCEEDED/worker Succeeded/exit 0，report `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`；0644 installation_id 与精确 run-owner 已验证。不得把 combined job 写成 SUCCEEDED。最终独立 re-review 已关闭全部 Critical/Important，结论为 Ready to merge Yes；native-shell 未验证及五轮前其他 preflight 边界仍保留。

## 10. 网站维护

`web/index.html` 是核心展示，`web/data/repository.json` 固定源码事实，`web/data/reproduction.json` 记录当前阶段状态。已完成准备项必须链接真实证据并保留限制；五轮、模型服务和最终评估在真实证据出现前保持未执行。当前为 4B、主分区八卡仅用 GPU 0–3、同协议重构、694/744 partial 口径。

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
python3 -m http.server 8000 --bind 127.0.0.1 --directory web
```

cpolar 只暴露 `web/`。公开前运行密钥扫描，不能把私有轨迹、数据集、凭据或大体积 checkpoint 链入公开目录。操作写入 `web/OPERATIONS.md`，交付结论写入 `web/RESULTS.md`。当前公网 URL、本地端口与 HTTP/cpolar 的任务 ID/PID 只记录在这两份文件与 `reproduction/PROGRESS.md` 中，并须随每次重启重新实测；本指南不写死这些会变化的值，已停止的旧 URL/PID 一律标注为历史入口。

## 11. 修改要求

- 不修改上游测试断言来掩盖环境或协议错误。
- 修改控制流、有效配置、入口参数、数据协议或 GPU 映射时，同步更新本文件、复现手册和网页 JSON。
- 文档中的命令与实际 argparse 不一致时以代码为准。已知例子：`META_SKILLS.md` 写 `--meta-skill-seed`，当前 `launch_meta.py` 实际参数是 `--skill-seed`。
- 跳过 ACE 时必须使用 `validation_skip.py` 按 settings/manifest 计算 partial 计数，不能复用旧 250/300 硬编码；当前 744 协议为 694/744，已有完整缓存不能当作 skip-ACE 结果复用。
- 区分离线测试、配置预检、真实 smoke、完整五轮实验和最终评估，任何前序成功都不能替代后序证据。
- 不执行 git commit/push，除非用户后续明确要求。
