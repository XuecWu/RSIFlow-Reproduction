# RSI-Flow 复现与 bubblewrap 隔离手册

> 当前已完成 4B 隔离基础设施验证、DeepCoder function 16 单域单轮 canary，以及固定同一 16 条的 A0+B1–B5 单域工程实验。后者 B1/B3/B4 ARTIFACTS 为 2/16→3/16、B5 为 4/16→5/16，B2 MODEL 为 4/16→3/16 并 retain_parent；B2 真实 positive-only SFT 未激活，最终 active 为 base Qwen3-4B + 四个累计 artifacts。正式五域五轮、最终 694/744、ACE 50、8B、独立 benchmark 和作者原始实验复现仍未运行；candidate/EvalPlus/BFCL/Codex 历史结果仍是最小基础设施 smoke，不是模型 benchmark 分数。通用数据、GPU 和评估协议见 `web/REPRODUCTION.md`。

## 1. 显式后端与禁止回退

真实隔离执行必须显式选择：

```bash
export RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1
```

未设置变量时仅保留上游 legacy Landlock 默认，用于兼容离线测试；本机/目标内核的 Landlock 返回 errno 38。bwrap 失败必须作为 infrastructure error 失败关闭，不能回退 host、Landlock 或 `danger-full-access`。基础设施失败、候选正常错误和模型答错必须分别记录，不能把 sandbox/RPC 失败记成模型零分。

完整回归使用仓库命令；需要显式后端的 real-isolation 测试在测试内逐项设置，不给整个套件全局注入，因为 legacy Codex catalog 测试覆盖未设置变量时的兼容路径：

```bash
cd RSIFlow
PYTHONPATH="$PWD/runtime:$PWD" /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
# DeepCoder canary 当前实测：522 passed, 2 skipped；早期 Task 4 为 489 passed, 2 skipped，最终隔离修复为 297 passed, 2 skipped
```

## 2. 安装固定 bubblewrap 资产

目标镜像不依赖系统 `bwrap`。将已核验的 0.6.1 二进制安装到 Git 忽略的 `assets/isolation/`；installer 拒绝符号链接、非普通文件和 hash 不符的输入：

```bash
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/install_bwrap.py \
  --source /absolute/path/to/verified-bwrap-0.6.1 \
  --root "$PWD/assets/isolation"
```

固定 binary SHA256 为 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`，manifest 为 `RSIFlow/configs/bwrap_isolation.json`。installer 还在 ignored `install-receipt.json` 中记录 manifest/launcher 的固定相对路径和 SHA256；candidate/native/Harness 默认 backend 每次 launch 都强制验证，tamper、receipt 缺失或 symlink 会在 spawn 前失败。Persistent Meta 继续使用 static trusted copies。不要用未核验的下载物替换资产，不要把二进制、凭据或私有输出纳入 Git/website。

## 3. 三类隔离 profile

| profile 家族 | 用途 | 关键约束 |
| --- | --- | --- |
| `candidate`（含 `candidate_session`） | 候选程序与持久 JSON-lines session | 单一专用 scratch 可写；无网络、无进程创建；保留 stdin/stdout 协议 |
| `native_benchmark` | BFCL 等官方 native runner | 只允许 thread clone 和 atomic rename；只保留两个声明的 RPC FD；不挂 host procfs |
| `persistent_meta` | 持久 Codex app-server | host-side dynamic tools/jobs；持久 `codex_home`；禁止 nested namespace 逃逸；只允许私有 loopback relay |

所有 profile 都使用 `--unshare-all --new-session --die-with-parent`，由受信任的 inner launcher 应用 rlimits/seccomp。mount 在打开时校验 device+inode，拒绝 symlink、宽泛宿主目录和未声明的可写根。

## 4. Keyless relay 与持久恢复

真实 `OPENAI_API_KEY` 只存在于 host `ResponsesTransport`。sandbox 只收到 `RSIFLOW_RELAY_KEY=pipe-only`，通过私有 loopback 访问 host relay；provider 固定 `OpenAI`，model 固定 `gpt-6-astra`，upstream 固定 `http://token.acerobotics.com/v1`，不允许 fallback。不得在命令行、tracked config、网页或公开日志中放置 key。

`codex_home`、thread ID 和 host-side durable jobs 在 app-server 重启后保留并采用；恢复时不得重复 bootstrap 或创建重复 job。生产 `prepare_codex_home()` 在 fresh path 原子创建 0644 `installation_id`，校验 UUID、no-follow regular file 和 mode，保留有效 inode/content；smoke 只调用该生产路径。`prepare_meta_storage()` 的 central `.run-owner.json` 绑定 resolved full run path：same-basename 不同 fresh run fail closed，same-path restart 不变，只有可证明 same-path 的 legacy links 可采用无 owner 旧目录。host dynamic-tool 子进程继续剥离 provider 凭据。

Codex native-shell descendant hygiene 未验证：legacy Landlock 不可用，nested vendor bwrap 被 anti-escape seccomp 拒绝。当前批准的 smoke 使用 `shell_tool=false` 和 construction-level hygiene；不得放宽 seccomp 或宣称 native-shell 通过。

## 5. 最小 smoke 命令

所有输出目录必须位于 Git 忽略的 `reproduction/logs/`，每次使用新目录，不公开 raw prompts/responses 或私有轨迹。

Candidate：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/candidate_bwrap_smoke.py \
  --output reproduction/logs/candidate_bwrap_smoke_new.json
```

EvalPlus（固定官方 source/config）：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow:$PWD/reproduction/scripts" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/evalplus_bwrap_smoke.py \
  --config reproduction/configs/report-evaluators-v4.json \
  --output reproduction/logs/evalplus-bwrap-smoke/new
```

BFCL `check_native`：

```bash
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python - <<'PY'
import json
from pathlib import Path
from tool_validation import check_native
config = json.loads(Path("reproduction/configs/validation_744.json").read_text())
check_native("bfcl_v3", config["tool_benchmarks"]["bfcl_v3"],
             Path("reproduction/logs/bfcl-bwrap-check-new"))
PY
```

Persistent Meta 离线检查与真实 smoke：

```bash
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/persistent_meta_bwrap_smoke.py --check

# 只在安全注入 OPENAI_API_KEY 的受控 shell 中执行；不要 echo/source 输出密钥。
RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python \
  reproduction/scripts/persistent_meta_bwrap_smoke.py \
  --config reproduction/configs/meta_bwrap_smoke.json \
  --run-dir reproduction/logs/meta-bwrap-new/run
```

`reproduction/configs/validation_744.json` 中 BFCL 与 ACEBench 的 `data_path` 指向冻结 allocated validation 输入 `assets/cohort/allocated/validation/{bfcl_v3,acebench}.jsonl`（分别 152/50 条），与 validation v4 pack 字节一致；这是来源快照输入，不替代官方 runner 自己使用的固定数据。`RSIFlow/validation_sources.py` 仍冻结并验证 official specs、native evaluator checkout 中相关 Python/JSON/JSONL 文件，以及配置的所有 benchmark data path 哈希；不得删除或缩窄该来源校验。BFCL manifest 中另有 19 个 ID 不在官方 runner 中，这是独立协议 blocker，本路径修复不改变 ID 或评分口径。

## 6. 已接受的真实 SCO 证据

- Candidate `pt-gyfupguq` SUCCEEDED/worker Succeeded；report `reproduction/logs/candidate_bwrap_smoke_sco_r3.json`，SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
- EvalPlus `pt-bfkp5k0d` SUCCEEDED/worker Succeeded；report `reproduction/logs/evalplus-bwrap-smoke/20261113T035651Z/smoke_report.json`，SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`。只覆盖一个 HumanEval+ 任务的正反 official comparator。
- BFCL `pt-fdvsxqax` SUCCEEDED/worker Succeeded；receipt `reproduction/logs/bfcl-bwrap-check-r2/check.json`，SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。只覆盖两次受控 native RPC。
- Meta `pt-zj1l9q1t` SUCCEEDED/worker Succeeded；report `reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`，SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。

Meta 证据由外部 actor `huangsiyuan1` 提交，实际为 8×A100、N3lS+N2lS candidate specs、`root_mapping=true`，经用户授权接受。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 在启动前失败；不得写成 A800 pass。该证据仅属历史。

当前 `313b6bc` 最终 gates（均 8xA100/root_mapping=false）：combined `pt-v6m10c4t` 整体/worker **FAILED** 仅因 Meta provider completion failure；candidate/Harness/BFCL exit 均 0。Candidate report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`；Harness `1 passed in 23.26s`，log SHA256 `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`；BFCL 两次 RPC，receipt SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`，key hygiene SHA256 `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`。combined Meta ordinal 0 HTTP 502、retry HTTP 200 incomplete SSE，failed report SHA256 `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`，分类为外部 relay/provider completion failure。

唯一 Meta-only retry `pt-x6b4ista` SUCCEEDED/worker Succeeded/exit 0；report `reproduction/logs/final-meta-a100-20261115T043755Z/run/report.json` SHA256 `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`。ok/completed、same thread、无重复 job/bootstrap、exact gpt-6-astra、3 dynamic calls、11 key hygiene + 7 cleanup、durable job 0、shell_tool=false；installation_id=0644 且 run-owner 精确匹配。EvalPlus 保留上述历史 one-task smoke，未在 313b6bc 重跑。最终独立 re-review 已确认三项生产修复和两项发布修复全部关闭，无新增 Critical/Important，结论为 Ready to merge Yes。

## 7. 下一道实验门

Task 7 的独立安全审阅及修复后 re-review 已由 controller 完成并通过。即使隔离审阅关闭，五轮前仍须完成五类 benchmark 最小 smoke、逻辑 GPU 0–3 的健康四卡 Qwen 服务、当前 694 配置预检和独立 experiment preflight review。不要从本手册的 smoke 命令直接跳入 A0+B1–B5、SFT 或最终评估。

当前 694 预检必须显式选择机器配置并使用 rsiflow 解释器（裸 `python RSIFlow_4B/launch.py --check` 会落到 `RSIFlow/configs/train_4b.json` 占位配置：`skip_acebench=false`、run name `eval744`、v1 data release，不能证明 694）：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python RSIFlow_4B/launch.py \
  --config /data/wuxuecheng/Hierarchical_level/RSI-Flow/reproduction/configs/train_4b.json --check
```

保存输出并确认 `config` 正是该机器文件，再独立核对有效 `PipelineConfig` 的 v4 frozen release、`skip_acebench=true`、694/744（五个 scored benchmark）、validation manifest 与输出目录。`--check` 本身只巡检路径存在性、rounds 与每轮任务数，不校验 skip-ACE、694 计数、manifest 内容、评测器可执行性、GPU 或 API，**返回 0 不等于该门通过**；该门当前仍未关闭。

正式运行同样必须显式选择同一机器配置，且 ACP 控制器保持**前台**：`RSIFlow_4B/start_5round_600.sh` 固定加入 `--prepare-data --detach` 又不带 `--config`，不能直接依赖。正式五域五轮、最终 694/744、ACE 50、8B 与持久正式 Qwen 服务均未运行；固定 16 条单域五轮及 B2 SFT 不替代这些正式目标。

## 8. 真实 Qwen 最小评分 smoke（已实现，待 controller 审阅后执行）

独立 runner `reproduction/scripts/model_scoring_smoke.py` 组合现有接口，**不调用完整 `validate()`**。它不是 694/744 评分、不是 A0 baseline、不是五轮、不是模型 benchmark，也不得用于 Meta 学习、训练、候选接受或 checkpoint 选择。

固定范围（选择规则在任何模型输出之前声明，禁止看结果重选）：

- HumanEval+ / MBPP+ / HotpotQA-dev / 2Wiki-dev：各取父 manifest 原顺序中**第 1 个完整 task**（共 4 个，每个官方分母 1）。
- BFCL-v3：取原顺序中**前 50 个**其 native IDs 全部存在于 pinned official `native_layout` 的完整 task；不足 50 或 ID/family 不明确即 fail closed。
- 父 manifest 中未被官方识别的 BFCL 条目记录为**父 manifest 协议缺口**，不静默修复、不宣称完整 694。
- ACE **不执行**，不触发用户模拟 API；父协议的 50 条保持 pending。

离线计划（默认模式，不启动服务、不调用模型、不需要 GPU）：

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/preflight
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/model_scoring_smoke.py \
  --output-root reproduction/logs/model-scoring-smoke-plan-new \
  --config "$PWD/reproduction/configs/train_4b.json" \
  --manifest /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/validation_744_v4/manifest.json \
  --settings "$PWD/reproduction/configs/validation_744.json"
```

真实执行（**必须显式 `--execute`，output root 必须不存在，必须在有 4 张可用 GPU 的授权作业中**；本开发机 NVML 不可用，不能在此执行）：

```bash
TMPDIR=/tmp PYTHONDONTWRITEBYTECODE=1 RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1 \
PYTHONPATH="$PWD/RSIFlow/runtime:$PWD/RSIFlow" \
/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python reproduction/scripts/model_scoring_smoke.py \
  --output-root reproduction/logs/model-scoring-smoke-execute-new --execute \
  --config "$PWD/reproduction/configs/train_4b.json" \
  --manifest /data/wuxuecheng/Hierarchical_level/RSI-Flow/RSIFlow/runtime/data/validation_744_v4/manifest.json \
  --settings "$PWD/reproduction/configs/validation_744.json"
```

执行语义与边界：

- 经 `TaskAdapter._config()` 使用机器配置与 8271–8274 四个 endpoint（GPU 0–3）；`start_services` → `verify_services` 校验 GPU/checkpoint/weights manifest；`try/finally` 只停止本 runner 拥有的 registry（`<output-root>/services/four_gpu_services.json`），并记录四卡释放（`stop_services` 强制每卡 ≥40,000 MiB）。cleanup 失败单独报告，不覆盖原异常、不虚报释放。
- Code/QA 复用 `generate_dynamic` + `evaluate_official` + `official_task_scores`；BFCL 复用 `evaluate_tool`（`selected_tasks`/`selected_task_ids`，严格 50 个不同完整 task）。
- 每任务必须有**真实非空预测**与**已完成的本地模型调用**；模型答错可成为官方 FAIL，但空预测、缺 ID、重复 ID、异常、分母不符、未完成 RPC、服务或隔离失败一律 incomplete/blocked，**绝不记模型零分**。
- **BFCL 逐任务证据（fix round 1 强化，fix round 2 再强化可信绑定）**：除官方 `completed`、ID 覆盖与分母对账外，还必须对每个 selected native ID 核验（a）官方 category 预测文件中**唯一且非空**的 native submission（结构化调用或文本均可，但只接受一个无歧义非空字段），以及（b）**可归属且可信的已完成本地 Qwen 调用回执**。归属产物 `<output>/task_call_attribution.json` 必须：声明 `generator=evaluate_tool_native_request_attribution_v1`、绑定 frozen `state_hash`、声明 `selected_task_ids` 及其 `selected_task_ids_sha256`（runner 用 `durable.value_hash` 重算核对）、`tasks` 精确覆盖全部 selected IDs；**并且官方 `evaluate_tool` 的 `result.json` 必须内含 `task_call_attribution = {generator, path, sha256}` 明确绑定**，runner 重新计算该文件 SHA256 并核对，路径不得逃出该 benchmark 输出目录。每个归属条目必须是 `{receipt_key, request_sha256}`，runner 按对应 `transport/<key>.json` 的 `identity.request` **重算请求内容 hash** 并核对，同时要求回执 `status=completed`、`user_simulator=false`、`identity.state_hash` 匹配、`calls` 全部 completed，且同一回执键不得归属两个任务。工具评估缓存（包括 `validate.py` 的 `complete.json` 快速返回路径）均回到 `evaluate_tool` 重验官方输出、state 和 BFCL attribution；`.call_NNN.json` sidecar 不作为主回执，必须按主回执键、连续 call index、完成状态及 call 数量独立对账。缺可信 generator、缺 result 绑定、文件被篡改（hash 不符）、请求 hash 不符、伪造或手写映射文件 → 该 benchmark **blocked**，不按顺序猜测、不伪造归属、不只信 JSON 中声称的 native ID。BFCL 的已完成调用数计入报告 `model_calls` 总数。
- **BFCL attribution 实现与剩余数据边界**：生产 `official_tool_entry` 只在 BFCL 官方 `BaseHandler.inference(test_entry)` 的 pinned 当前任务上下文中捕获 native ID/category；固定 official commit 与 `BaseHandler.py` SHA256，不匹配即 fail closed。审计元数据经 RPC frame 的独立 `attribution` 字段传递，不写进模型可见 `messages/tools`；`HarnessAPI` 将其绑定到 receipt identity，`evaluate_tool` 在官方 generate/evaluate 完成后重算请求 hash、检查 selected-ID 覆盖/回执状态/本地调用，并写 `task_call_attribution.json` 和 `result.json` 的 path/SHA256/generator。BFCL attribution 由此不依赖结果顺序或问题相似度。**当前 v4 frozen 152 队列仍有 19 个 native ID 不在 pinned official pool，`evaluate_tool` 会在执行前拒绝该队列；本修改不替换数据、不降低分母，因此正式 BFCL/694 仍 blocked，须先发布并冻结修复后的数据版本。**
- **输出根目录校验（plan 与 execute 均执行）**：必须是 **PROJECT 内、尚不存在、且 Git 忽略**的新目录；优先用 `git check-ignore` 权威判定，git 不可用时（如跨 uid 属主问题）退回到保守的 `.gitignore` 祖先目录规则（`**/logs/` 等），任何 negation 触及该子树即拒绝。可跟踪路径（如 `reproduction/configs/…`、`RSIFlow/tests/…`）一律拒绝；校验方式记录在回执 `output_root_validation`。原始正式数据只读；不写 `complete.json`，不产生跨 benchmark 汇总准确率，不把 54 项冒充 694/744。
- 消费正式 held-out 条目会记录为 exposure，必须与后续正式评估隔离；`clean_independence_audited=false`、`exposure_unknown` 的保留意见不变。
- 真实 `OPENAI_API_KEY` 与 ACE 用户模拟凭据均**不需要**；不得读取或打印凭据。

离线回归：`RSIFlow/tests/test_model_scoring_smoke.py`（temp dirs + monkeypatch 隔离 GPU/官方进程；合成回执仅为 test-only fixture，不是真实 smoke 证据）。

## 9. BFCL v5 数据协议迁移（已生成并冻结，未执行；切换由 controller 决定）

§8 末尾指出的阻塞——v4 frozen BFCL 152 队列中 19 个 native ID 不在 pinned official pool——已由**新建不可变 v5 release** 关闭数据侧缺口。v4 全程只读、逐字节保留；未降低 152 分母，未修改 official evaluator/data，未改生产 `validate.py` 或五轮运行配置 `train_4b.json`。

迁移工具与产物：

- 单用途脚本 `reproduction/scripts/migrate_bfcl_v5.py`；离线回归 `reproduction/tests/test_bfcl_v5_migration.py`（**33 项**，临时 fixture，无 GPU/网络/模型；含 4 组 mutation 验证，证明 official-ID/disjoint、quota、immutable、pinned-HEAD 守卫均为载荷性校验）。
- **强制 pinned checkout 身份**：`--expected-official-commit` 为必填；工具同时核验 spec 声明的 commit、`repository_root` 的**真实 HEAD** 与 `native_layout` 解析出的 package root HEAD，三者必须一致。优先 `git rev-parse HEAD`；git 因跨 uid 属主拒绝时，只读解析 `.git/HEAD`/loose ref/`packed-refs` 得到同一 commit，**不修改任何 git trust 或全局配置**。回执记录 `verified_checkout_head`、`head_verification`、`package_root_head`。
- 主工作树 Git 忽略的新版本目录 `assets/cohort-v5/bfcl-v5-migration-r1/`：`allocated/validation/bfcl_v3.jsonl`（152 行）、`validation_source_manifest.json`（744 条）、`bfcl_v5_migration_receipt.json`（机器可读迁移回执）。
- 新 release：`RSIFlow/runtime/data/rounds_3000_5x600_v5/` 与 `RSIFlow/runtime/data/validation_744_v5/`，由**现有** `prepare_600_744.py` 生成，标准 READY schema 未扩展。
- 新配置（仅指向 v5，未覆盖 v4）：`reproduction/configs/train_4b_v5.json`、`reproduction/configs/validation_744_v5.json`。相对**本工作树的 v4 配置**只改 `data_release`/`frozen_data_dir`/`validation_manifest`/`source_validation_manifest`/`validation_config`/`run_name`，以及 BFCL `data_path`；evaluator commit、`repository_root`、`official_specs`、ACE spec、各源 quota 全部不变。
  - **ACE `data_path` 的基线差异须如实区分**：主工作树的 `reproduction/configs/validation_744.json` 仍指向旧占位路径 `assets/cohort/acebench/test.jsonl`（与 BFCL 同属早前已修的 data_path 缺口），而本工作树的 v4 配置已指向 `assets/cohort/allocated/validation/acebench.jsonl`。v5 沿用**本工作树 v4** 的 ACE 路径，因此相对本工作树 v4 **未变**，相对主工作树 v4 则是路径差异。两种基线下 ACE 的 50 条 validation 记录身份与字节都与 v4 相同，未替换任何 ACE 样本。

选择规则（发布前固定、完全确定、不看模型结果）：

- 保留 133 个已在 pinned `ea13468e4423454d0c213704fb87cf7cb3990433` official universe（17 类、4441 IDs）中的 BFCL 项，其身份/内容/字节不变。
- 确定性替换 19 个无效项：17 个带 BFCL-v4 风格标签（`exec_simple`×6、`exec_parallel`×3、`exec_multiple`×1、`multi_turn_composite`×5、`rest`×1、`sql`×1），2 个原始行缺 native ID 而退化为 qhash。
- **这些标签无一是 pinned v3 类别，不存在权威 crosswalk；回执显式记录 `category_crosswalk=unavailable_no_authoritative_mapping`，不伪造逐条类别等价。** 两条 qhash 项记 `removal_reason=missing_native_id_qhash_fallback`、`removed_category_label=null`。
- 候选池 = pinned official 全集中排除现有 train/validation 的 native_id/task_id、content_hash 冲突、group_id 冲突与不完整记录后的 4270 条（排除 133 个既有 ID、38 个内容冲突）。
- 19 个名额按**保留项类别分布的最大余数比例分配**，类内按 `fingerprint([seed=42, category, native_id])` 排序取前 N；候选按 v4 manifest 顺序填入被移除槽位。候选不足即 fail closed，绝不降配额。

已验证的不变量（真实资产，离线）：

- v4 release 30 个文件哈希不变（READY `07f07c61…`、frozen validation manifest `884f59d9…`）；v4 BFCL pack `eb4b048a…`、v4 source validation `manifest_hash 927a8606…` 未变。
- v5 训练 20 个 pack 与 v4 **逐字节相同**；`source_training_hash` 仍为 `78863d14…`；5×600、每轮配额、Code 120 STDIO/80 function、跨轮 task/content disjoint 全部通过。
- v5 验证 744 条：ACE 50、HumanEval+ 164、MBPP+ 178、HotpotQA 100、2Wiki 100 的身份与记录字节与 v4 **完全一致**；BFCL 仍 152、ACE 仍 50 pending、744 frozen / 694 scored 口径不变。
- v5 BFCL 152 个 native ID **全部**属于 pinned official 集合且唯一；`assert_disjoint(train, validation)` 通过；`select_specs` 得 744/694。
- `validate_tool_selection("bfcl_v3", …, 152 tasks)` 在 `formal_report_evaluation=true`、`purpose=report_only` 且 `evaluation_scope` 绑定一致时**接受 152 形式化队列**；selected IDs 与 frozen 队列一致，覆盖 14 个官方类别。该检查使用合成 binding，**未运行模型、未产生任何分数**。
- `validation_sources.freeze/verify` 对 v5 settings 成功（284 个文件哈希，含新 BFCL pack）。
- v5 READY：`status=prepared_not_executed`、`config_hash 32000098…` 与 `protocol.json` fingerprint 一致、`source_validation_hash 90ffe339…`。

边界与待办：

- **未执行任何模型/评估/五轮/SFT/SCO**；v5 只是冻结数据版本。没有 BFCL 分数、没有 694 结果、没有 A0 baseline。
- `clean_independence_audited=false`、`exposure_unknown` 与公开来源重构限制**原样保留**；v5 是「经 pinned BFCL v3 native-ID 对齐的同协议重构」，不是作者原队列精确复现。替换改变了 held-out 样本，v5 状态必须重新标注，不得把 v4 的 BFCL 证据当作 v5 结果。
- 是否把五轮运行配置切换到 v5 由 controller 决定；`train_4b.json` 与 v4 目录本次未改。
- 迁移回执与逐条 old→new 清单见 `reproduction/audit/reviews/bfcl-v5-report.md`。

## 10. 训练侧最小真实 smoke（首轮真实失败已诊断，等待修复后重跑）

独立 runner `reproduction/scripts/training_side_smoke.py` 用**五条固定 mini-B1 任务**原样复用生产 `TaskAdapter.bootstrap/rollout/sft` 与四卡 GPU 相位，验证真实训练控制流；**不调用 `pipeline.run()`/Meta，不接受候选，不写正式 round complete 或 694 标记**。离线回归 `RSIFlow/tests/test_training_side_smoke.py` 覆盖生产合同、mutation、legacy-contract RED 与真实进程树信号/`/proc` 归属测试；临时 fixture + mock，无 GPU/模型/API/SCO。真实首轮 S1 `pt-ave60pvr` 因遗漏显式 bwrap 环境进入 legacy Landlock 而 incomplete，未产生 rollout/正例结论；fix 1 已在 bootstrap/service 前增加后端门，详见 `reproduction/audit/reviews/real-preflight-fix1-report.md`。

五个子命令与阶段门：

```text
plan -> execute-rollout -> execute-sft -> execute-recovery -> status
```

- **只有 `plan` 创建输出根**，且必须是 PROJECT 内、尚不存在、Git 忽略的新目录（优先 `git check-ignore`，git 不可用时保守 `.gitignore` 祖先规则）；plan **无任何模型/服务调用**。
- 真实 `execute-rollout` 必须显式设置 `RSIFLOW_SANDBOX_BACKEND=linux_bwrap_seccomp_v1`；缺失或错误时在 `TaskAdapter`、bootstrap 和服务副作用之前 fail closed，不得回退 legacy Landlock。
- 每阶段不可变 receipt + stage lock（`O_EXCL`，禁止并发）+ 代码/config/selection/manifest/base-checkpoint 哈希绑定；绑定不符或存在 `started` 而无 final receipt → `requires_audit`，**不静默重跑、不删除重来**；final receipt 已存在 → 只校验并返回 `already_completed`。
- 选择规则（看模型结果前固定）：按冻结 parent B1 原顺序取第一条 EnvScaler、DeepCoder/TACO STDIO、DeepCoder/TACO function、HotpotQA、2Wiki，恰好 5 个唯一 task；缺类/重复/空记录/错误 role/hash/offset 一律 fail closed；**禁止看结果换题**。
- mini release 用原 `repack`/`native_index` 复制真实 record bytes，回读校验偏移/长度/哈希，并确认原 B1 pack 与检索索引哈希未变；`tasks.sqlite` 只含 5 条 `evolve_train`。
- 派生 smoke-only launcher：`rounds=1`、`allocated_tasks=5`、`training_tasks_per_pass=5`、source quotas `1/2/1/1`（domain `1/2/2`）、独立 data/output/run roots；**保留真实 checkpoint、seed Harness、检索、seed 42、BF16、one-epoch LoRA 与固定 SFT preset**（`max_steps=-1`、lr 2e-6、rank 8/alpha 16/dropout 0.05、七个 target modules、max_length 16384），经原 `TaskAdapter._config()` 验证并冻结。
- rollout 验收：5 条唯一 task、覆盖五类各一、role/purpose/round/stage/manifest 绑定一致、每条 `model_call_count>=1` 且有持久 trajectory 与 `verification.status=completed`、success 为布尔、无 infrastructure error。**模型答错但评分完成 = `completed_with_model_failures`（有效 smoke 结果）；缺失评分/infra 错误/错误 manifest = incomplete**。
- **零正例**：用生产 `select_positive_rows(profile="multidomain")` 判定；无自然正例时 SFT = `blocked_no_positive_parent_trajectory`，总 smoke 不得 PASS，**不调用 trainer、不写 child、不换题/造标签/借 held-out**。
- SFT 验收：正例仅来自本次 parent；`world_size=4`、ranks `{0,1,2,3}`、one epoch、preset 未被覆盖、loss 有限、LoRA/merged weights 确实改变；child 位于 smoke 输出树且不同于 base、base manifest 前后一致、Harness/artifacts 不变；`gpu_phase_receipt.status=completed` 且 `restored_checkpoint=child`、四 replica GPU 0–3；`verify_services` 绑定 child 后做一次真实短生成。
- recovery：核对训练 receipt 哈希未变，`ensure_services`+`verify_services` 恢复同一 child、短生成、清理；**全程零次 SFT 调用**。
- 所有阶段 `finally` 只停**本输出根**的 owned registry（`stop_services` 强制 GPU 0–3 各 ≥40,000 MiB）；无 registry 时记录"未创建 owned 服务"而不停其他 owner；**cleanup 失败则总结果失败**，即使 rollout/SFT 已完成。
- 每个 receipt 均含 `smoke_only=true`、`selected_tasks=5`、`formal_round_completed=false`、`eligible_for_meta=false`、`eligible_for_candidate_acceptance=false`、原 B1 hash 与 mini manifest hash。

真实资产离线 plan（`reproduction/logs/training-side-smoke-plan-r1`，Git 忽略、写入前 `test ! -e`）已验证：5 条真实 v4 B1 任务（`envscaler:env_154_rl:env_154_rl-task_47`、`deepcoder_taco:deepcoder-taco-424`、`deepcoder_taco:deepcoder-taco-2865`、`hotpotqa:5abaf8be5542992ccd8e7e85`、`2wiki:f96fae0a0bae11ebab90acde48001122`），记录字节/哈希与原 B1 **5/5 一致**，原资产未变，preset 与四卡 replica 完整，`model_calls=0`。

**边界**：本任务实现阶段**未启动真实 GPU/SCO/模型/API/五轮/SFT**。S1 rollout、S2 SFT/merge/reload、S3 recovery 的真实执行需单独授权并在 SCO 前台顺序进行；`Scope.observed()` 的 `same_round_full_B_parent_child_v2` 在本 smoke 中只表示"完整五条 mini B1"，**绝不能据此宣称完成正式 600-task B1 或五轮协议**。五条 rollout 分数不是 benchmark，不产生模型提升结论。详见 `reproduction/audit/reviews/training-side-smoke-report.md`。

## 11. DeepCoder function 16 canary（真实单轮已完成，质量 DONE_WITH_CONCERNS）

Tasks 1–4 完成实现、冻结 release、离线测试和审查；Task 5 已在 dev4 完成真实单域单轮单候选 canary。冻结 release 位于 Git 忽略的 `reproduction/logs/deepcoder-canary-16-v1/release/`：16 条 DeepCoder/TACO function 按父 B1 原顺序选择。实际运行配置为 `reproduction/configs/train_4b_deepcoder_canary_workspace.json`，一轮、code-only、`0/16/0` quotas、单实验 attempt、`evaluation_schedule=none`。

真实结果为：Astra 选择 **HARNESS**，同一 candidate 在 **2/3 次检查**后 passed；父代 **51**、候选 **93** 次真实 Qwen calls，父 **7**、候选 **8** 个有效 submission 进入 full verifier，其余不伪称完整测试。完整配对为 **3/16=0.1875 → 4/16=0.25，delta +0.0625**，显式 accept/activate、skills/context/snapshot、state COMPLETE、jobs 调和和四卡 cleanup 均完成。

质量边界必须保留：**4 个新成功 + 3 个回归，原 3 个成功全部回归**；4 个新成功全来自父代零调用失败，3 个回归全为候选零调用失败，过程异常使因果归因受限。该接受只代表固定批净正增益，不是普遍 Harness 改进或模型质量结论。独立审阅为 **Spec PASS / Quality DONE_WITH_CONCERNS**；详见 `reproduction/audit/reviews/deepcoder-canary-real-results.md`。

supervision 0001 保留 24 个失败 turns、24 条 error events 和 24 条对应 turn/completed events 的 502 历史；0002 在同一 run/thread 从 candidate_built 恢复，无重复 bootstrap/run_parent，最终 exit 0。真实执行与恢复必须遵循 `reproduction/audit/reviews/deepcoder-canary-runbook.md` 的 detached supervisor、4 小时 launcher 监督、显式 bwrap、私有 Meta env、identity-checked cleanup 和同 run resume；active jobs 未调和前禁止 cleanup/resume。

该单轮 run 的 MODEL 未选择、SFT 未运行；522 passed/2 skipped 是此历史阶段计数，当前测试证据见 final-review fix report。后续固定 16 条单域五轮见下节；694/744、独立 validation/benchmark 和作者原始实验复现仍未运行，ACEBench 是 skipped 而非 scored。

## 12. DeepCoder function 16 单域五轮（真实完成，有界工程结果）

实际配置为 `reproduction/configs/train_4b_deepcoder_five_round_workspace.json`，与单轮 canary 是不同 run。五轮重复使用同一组 16 个唯一 function tasks，共 80 rollout positions；每轮一个 `attempt_1`，无独立 validation。接受汇总为 `reproduction/audit/evidence/five-round-final-summary-20261119.json`，完整脱敏结果见 `reproduction/audit/reviews/deepcoder-five-round-real-results.md`。

| 轮次 | 组件 | Calls 父→候选 | full verifier 父→候选 | 成功数 父→候选 | new success / regression | 结果 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| B1 | ARTIFACTS | 87→0 | 7→7 | 2/16→3/16 | 1 / 0 | accept + activate |
| B2 | MODEL | 40→118 | 6→7 | 4/16→3/16 | 2 / 3 | retain_parent |
| B3 | ARTIFACTS | 61→0 | 8→8 | 2/16→3/16 | 1 / 0 | accept_candidate + activate |
| B4 | ARTIFACTS | 59→0 | 8→8 | 2/16→3/16 | 1 / 0 | accept_candidate + activate |
| B5 | ARTIFACTS | 63→0 | 8→8 | 4/16→5/16 | 1 / 0 | accept_candidate + activate |

ARTIFACTS 均为 Meta-assisted direct-submission output repair，候选零新增 Qwen calls，每轮编辑一个 target、复用其余 15 个 baseline results，累计 artifacts 为 1/2/3/4。每侧只有 6–8 个有效 submissions 进入 full verifier，不声称全部 16 条收到完整测试。

B2 positive-only SFT 使用 20 input/unique/sampled examples、4 ranks（每 rank 5 samples）、1 epoch、每 rank及全局 3 optimizer steps；LoRA rank 8、LR `2e-6`。merged candidate 未激活，B2 选择父代。最终 active generation=4，checkpoint 仍是 base Qwen3-4B，累计四个 artifacts；各轮 selected snapshot、skills、context 和 active hash 链均通过重算。

真实 supervision 为 0001/0003–0008，0002 为 preflight rejected；早期受控停止和 missing-key failure 保留，最终 0008 exit 0，22 个 durable jobs 终态完整。`run_complete=true`、protocol complete、launcher exited、external cleanup ok，四卡最终 cleanup 时空闲。Occupier 自动恢复当时因 NVML PID ownership 未解决而 stopped/restore_pending；之后用户明确授权直接启动，主 agent 复核四卡空闲后通过 `tmux` 会话 `rsiflow-idle-occupier` 执行 `bash run.sh --mem-mb 2500 --log-interval 20`，四个 rank 均持续训练。该操作是 cleanup 后的独立资源恢复，不改写自动恢复失败历史或实验证据。

最终修复后的完整离线套件为 **1031 passed, 2 skipped in 723.43s**；21 个终审专项测试、真实最终汇总重算、独立 scoped re-review、网站 11/11 和合并前 10 个 HTTP 资源检查均通过，详见 `reproduction/audit/reviews/deepcoder-five-round-final-verification.md`。

边界：没有正式五域 3000、held-out 或 694/744，没有作者复现或通用能力结论。ENOENT/EAGAIN 和零调用重叠限制归因；ARTIFACTS 分数是 Meta-assisted output repair，不是 Task policy 改进，B2 SFT 也不是模型质量提升。
