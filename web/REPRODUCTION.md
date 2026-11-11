# RSI-Flow 复现手册与当前执行记录

> 当前已批准 4B 准备和主分区八卡 probe：仅用 GPU 0–3、公开数据同协议重构、Meta `gpt-6-astra`、ACE 跳过。环境、模型、v4 数据和部分离线 smoke 已执行；五轮演化、最终评估和 8B 尚未运行。下文保留通用复现步骤，实际状态以 `data/reproduction.json` 和 `../reproduction/PROGRESS.md` 为准。

## 1. 复现目标与判定标准

源码基线：`496d3cfb9ff39997d107d0ff29e746b07e638180`。

完整复现不是“仓库能导入”或“`--check` 返回 0”，而是同时取得：

1. 相同冻结数据：五轮各 600，共 3000；独立验证 744；source/frozen manifest hash 与 disjoint 证据完整。当前 v4 是同协议重构，不能声称作者原始队列。
2. 相同有效配置：Qwen3-4B/8B 只改变 checkpoint 和 run name，其他协议一致。当前只执行 4B，8B 需审阅后另行决定。
3. 完整过程：A0、B1–B5 的父代/候选轨迹、每次 attempt 的 decision/selection、技能记忆和快照。
4. 真实计算：四卡 BF16 推理和 MODEL 更新时的四卡 DDP LoRA SFT。主分区申请八卡，但当前只用 GPU 0–3。
5. 最终结果：完整协议应评估六类 744；本次明确跳过 ACE，只接受 694/744 partial 回执，ACE 50 为 pending，不能写成完整 744。

若原始 source manifests 不可得，只能讨论“同协议重构”，不能直接声称原始结果精确复现。不得把离线测试、合成数据测试或部分 benchmark 当作完整实验。

## 2. 硬件与执行阶段

| 阶段 | 源码强制要求 | 说明 |
| --- | --- | --- |
| Task rollout | 逻辑 GPU 0–3，每卡至少 20,000 MiB 空闲 | 四个本地 BF16 Qwen 服务，分别绑定 checkpoint |
| MODEL LoRA SFT | 同一四卡；停止推理后每卡至少 40,000 MiB 空闲 | 4-rank DDP、BF16、gradient checkpointing，无 CPU 回退 |
| HARNESS 构造 | 构造主要在 CPU，候选配对 rollout 仍需四卡 | 定位 → 方向 → 完整生成 → 最多三次验证 |
| ARTIFACTS 更新 | 编辑在 CPU，直接 submission 评分依赖对应执行环境 | 当前为 `direct_submission` |
| Meta 控制 | 不要求本地 GPU，但需要 Codex 和 Meta API | 本次为自定义 `gpt-6-astra`，私有凭据未提供 |
| ACEBench 用户模拟 | 不需要单独本地 GPU，但需要外部用户模拟 API | 本次显式跳过，50 条 pending，不是零分 |
| 最终评估 | 选中 Task 的推理服务和官方评测器 | 冻结 744；本次五类 694 partial，只报告，不反馈训练或 Meta |

推荐硬件是单节点 4×A800 80GB。80GB 不是仓库声明的理论最低总显存，而是满足源码 20GB/40GB 空闲门槛并留有余量的集群选择。

有效 MODEL 超参数以 `TaskAdapter._config()` 合并后的结果为准：one epoch、LR `2e-6`、LoRA rank 8/alpha 16/dropout 0.05、q/k/v/o/gate/up/down projections、batch 1、gradient accumulation 8、max length 16384。base JSON 的 rank 16/LR `2e-5` 会被覆盖。

## 3. 准备机器专用目录和配置

后续执行时，先复制配置，不直接改上游基线：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
mkdir -p reproduction/configs reproduction/logs assets
cp RSIFlow/configs/train_4b.json reproduction/configs/train_4b.json
cp RSIFlow/configs/train_8b.json reproduction/configs/train_8b.json
cp RSIFlow/configs/validation_744.json reproduction/configs/validation_744.json
cp RSIFlow/runtime/configs/base_4b.json reproduction/configs/base_4b.json
cp RSIFlow/runtime/configs/base_8b.json reproduction/configs/base_8b.json
```

需要替换的字段包括：

- 两个 train JSON：`base_config`、`dataset_root`、`output_root`、`frozen_data_dir`、`retrieval_index`、`validation_vault`、`validation_manifest`、`source_training_manifest`、`source_validation_manifest`、`validation_config`。
- 两个 base JSON：`task_checkpoint`、`source_manifest`、`data_dir`、`envscaler_root`、`envscaler_utils`、`meta.codex_executable`、`meta.model_catalog_json` 和其他 `/path/to/...`。
- validation JSON：`official_specs`、每个 benchmark 的 `data_path`、`repository_root`、`python_executable`。
- 路径字段使用绝对路径。两个 backbone 的 frozen cohort 必须相同；顺序运行时 output_root 可一致，run_name/run_dir 必须不同。
- `RSIFLOW_PYTHON` 只控制 launcher 解释器，不会替换 JSON 内的 trainer/evaluator interpreters。

凭据通过目标平台的安全注入机制提供，不写入这些 JSON、网页或命令摘要。需要的变量名包括 `AUTODL_API_KEY`、`ACE_USER_API_KEY`、`ACE_USER_BASE_URL`，以及实际的 `RSIFLOW_CODEX_EXECUTABLE` 路径。

## 4. Conda 与 Python 依赖（通用步骤；当前完成情况见顶部）

```bash
conda create -n rsiflow python=3.11 -y
conda activate rsiflow
python -m pip install -U pip setuptools wheel
python -m pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu128

cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow
python -m pip install -e './runtime[dev,pipeline]'
python -m pip install -r upstream/RSIFlow_task/requirements.txt
```

选择 torch 2.8.0+cu128 是因为现有 SCO 文档记录该组合已在 A800 容器识别 GPU，不代表本项目已经验证。后续 probe 必须打印实际 torch/CUDA/GPU/BF16 信息。

评测器有冲突依赖时创建独立 evaluation 环境，并把 validation JSON 的 `python_executable` 指向该环境。不要为消除依赖冲突而降级 NumPy 到 runtime 要求的 2.0 以下。

环境证据应保存：

```bash
conda env export -n rsiflow > web/rsiflow-environment.yml
python -m pip freeze > web/rsiflow-requirements.lock.txt
```

这两个导出命令同样属于后续步骤，本次未生成 lock 文件。

## 5. 离线测试和配置预检（通用步骤；当前完成情况见顶部）

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" python -m pytest -q tests

cd ..
python RSIFlow_4B/launch.py --check
python RSIFlow_8B/launch.py --check
```

记录测试通过、失败和 skip 的准确数量。`--check` 只报告配置和路径，不调用模型/API，也不保证 benchmark 可运行。placeholder 导致的 `checkpoint_exists=false` 等结果应标为资产未准备，不是代码测试通过。

## 6. 准备冻结队列（通用步骤；当前完成情况见顶部）

先获取并校验原始 source training/validation manifests，然后：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
python RSIFlow/prepare_600_744.py --config "$PWD/reproduction/configs/train_4b.json"
python RSIFlow/prepare_600_744.py --config "$PWD/reproduction/configs/train_8b.json"
```

第一次应生成 `READY.json`，状态为 `prepared_not_executed`；第二次在仅 backbone/run name 不同的情况下应返回 `already_prepared`。核验：

- `training_tasks=3000`，`tasks_per_round=[600,600,600,600,600]`。
- `validation_tasks=744`，六类数量为 152/50/164/178/100/100。
- train/validation 以及不同轮次的 task/group/content disjoint。
- B1–B5 manifest、validation manifest、retrieval index 和 hash 均存在。
- 第二个 backbone 没有改变任何 frozen 文件 hash。

若已有目录但配置、source hash 或协议不同，使用新 release 目录，不能覆盖。

## 7. SCO 四卡 probe（未提交）

未修改源码时，推荐 dev 四卡顺序方案：

- Region：`cn-sh-01`
- Workspace：`sensebot-a100-a800-liujianbo`
- AEC2：`sensebot-a800-dev-liujianbo`
- Worker spec：`N3lS.Ii.I60.4`，单节点 4×A800 80GB、48 vCPU、480 GiB
- 共享卷：`019fdaf3-33db-7bbf-992b-bc2f64d0b2fd:/data`

建议 probe 命令：

```bash
/root/.sco/bin/cnsh01/sco --region=cn-sh-01 acp jobs create \
  --workspace-name=sensebot-a100-a800-liujianbo \
  --aec2-name=sensebot-a800-dev-liujianbo \
  --job-name=rsiflow-env-probe \
  --container-image-url='registry.cn-sh-01.sensecore.cn/ccr-sensebot-ccr/starvla7:lihao-dev-20260426172526' \
  --training-framework=pytorch \
  --worker-spec='N3lS.Ii.I60.4' \
  --worker-nodes=1 \
  --priority=NORMAL \
  --command='nvidia-smi; /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -c "import torch; print(torch.__version__, torch.cuda.device_count(), torch.cuda.is_available()); print([torch.cuda.get_device_name(i) for i in range(4)]); print(torch.cuda.is_bf16_supported())"' \
  --storage-mount='019fdaf3-33db-7bbf-992b-bc2f64d0b2fd:/data' \
  --env='NCCL_IB_TIMEOUT:22,NCCL_IB_RETRY_CNT:13,NCCL_IB_AR_THRESHOLD:0' \
  --quota-type=reserved
```

probe 还应验证本地 checkpoint 能加载、四卡空闲显存满足门槛、8271–8274 可绑定，以及共享盘写入正常。记录 ACP job ID、排队/运行/结束状态和日志，结束后确认资源释放。

主分区允许 8×A800，例如 `N3lS.Ii.I60.8`，但**不能只靠在父进程设置 `CUDA_VISIBLE_DEVICES=4,5,6,7` 并行第二套流水线**：服务和训练子进程会重写该变量。主分区双四卡方案还需修改 GPU 映射，并隔离 ports、socket、output_root/services registry、run dirs 和日志，完成测试后才可提交。未修改源码时不建议直接采用。

## 8. Meta API 与 benchmark smoke（通用步骤；当前完成情况见顶部）

`api-smoke` 需要专用配置，顶层 `mode` 和 `meta.run_mode` 都为 `api_smoke`。在复制出的配置准备好 Codex binary/catalog 和安全凭据后：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
PYTHONPATH="$PWD/RSIFlow/runtime" python -m rsiflow.task_meta.pipeline api-smoke \
  --config "$PWD/reproduction/configs/api_smoke.json" \
  --run-dir "$PWD/Rollout_logs/preflight/api-smoke"
```

该步骤会产生真实 API 请求，必须另行批准。分别检查 BFCL、ACE、HumanEval+/MBPP+、HotpotQA/2Wiki 的数据、解释器、official specs 和最小样例。基础设施失败、跳过和模型答错必须分开记录。

## 9. 完整实验（通用步骤；当前完成情况见顶部）

启动前硬门槛：环境、原始 frozen data、四卡 probe、Meta smoke 和六类评测 smoke 均有证据。使用全新 run dir，ACP 内前台运行，不能调用默认带 `--detach` 的 shell 启动器后让容器退出。

4B 的容器内命令形式：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
mkdir -p reproduction/logs
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python RSIFlow/launcher.py \
  --backbone 4b --prepare-data --wait-for-gpus \
  --config "$PWD/reproduction/configs/train_4b.json" \
  --run-dir "$PWD/Rollout_logs/runs/rsiflow-4b" \
  > "reproduction/logs/acp-4b-$(date -u +%Y%m%dT%H%M%SZ).log" 2>&1
```

把该命令作为合规四卡 ACP 作业的前台命令，并沿用 probe 的 workspace/AEC2/spec/image/volume。4B 完成并释放后，再以 `train_8b.json` 和 `rsiflow-8b` 提交第二个作业。多次提交至少间隔 5 秒，不使用 `sleep inf`。

监控：

```bash
bash RSIFlow/watch_meta.sh --run-dir "$PWD/Rollout_logs/runs/rsiflow-4b" --once
```

恢复时使用相同配置和 run dir，先检查 `active_task.json`、jobs、workflow events 和回执，不重复 bootstrap，不覆盖 attempts。`--wait-for-gpus` 会等待 GPU 0–3 连续两次检查空闲，不代表后续训练一定成功。

## 10. 最终评估与结果审计

第五轮接受并保存完整 Task/Meta snapshot 后，控制器才执行 `final_only` 独立评估。检查：

- 六类 benchmark 均有状态，冻结总数为 744；本次只有五类 694 可进入评分，ACE 50 必须保留 pending。
- `code_search_partial_complete.json` 的 state hash 与选中 Task 一致，指标文件 hash 未变化；不得复用完整 `complete.json` 缓存。
- `task_results.jsonl` 每行都有明确 execution/scoring 状态；缺失值不能当失败或零分。
- `task_metrics.json`/`task_metrics.csv` 在 skip-ACE 下 overall complete 应为假；只有 partial 回执完整时才报告 694 口径结果。
- `feedback_to_meta=false`，评估结果没有进入 SFT 或技能学习。
- 跳过 ACE 时由 `RSIFlow/validation_skip.py` 按 settings/manifest 生成 partial 回执：当前为 694/744，旧 300 协议仍为 250/300；缺失指标文件或已有完整缓存都应拒绝。
- 模型 checkpoint、Harness manifest、Artifacts、skills/context 和 snapshots 均保留。

若以后审阅批准 8B，对比必须使用同一 frozen release、同一评估预算和兼容 API/评测器。只比较完整配对结果，不用部分任务或历史均值补齐。

## 11. 常见阻断与处理

| 现象 | 处理 |
| --- | --- |
| source manifest 缺失 | 暂停精确复现；请求原始资产或另行批准同协议重构 |
| GPU 空闲不足 20GB/40GB | 不启动或等待合规资源，不杀其他任务 |
| 四卡服务健康但 checkpoint 不匹配 | 视为绑定错误，检查 registry 和 weights manifest |
| ACP 启动后立刻结束 | 检查是否误用 `--detach`，改为前台控制器 |
| API/Codex 失败 | 记录基础设施错误，保留 run/thread，修复后恢复，不重复 bootstrap |
| ACE 用户模拟不可用 | 标为阻断或显式 partial，不能写成 ACE 零分 |
| 既有 frozen dir 配置不同 | 使用新 release，不能覆盖 |
| 文档参数无法识别 | 以 argparse 为准；技能 seed 当前是 `--skill-seed` |

## 12. 当前已决定与仍需材料

已决定：同协议重构、主分区八卡仅用 GPU 0–3、4B 先行、`gpt-6-astra`、ACE 跳过、4B 审阅后才讨论 8B。

仍需用户提供 Meta 私有凭据到 `assets/private/credentials.sh`（权限 600）；agent 不读取、打印或复制 key。SCO Landlock/GPU 和五类 benchmark smoke 未全部通过前，不启动五轮长任务。
