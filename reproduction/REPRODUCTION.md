# RSI-Flow 复现与 bubblewrap 隔离手册

> 当前只验证 4B 隔离基础设施。五轮、SFT、最终 694/744、ACE 50、8B 和正式 Qwen 服务未在本任务中运行；所有 candidate/EvalPlus/BFCL/Codex 结果都是最小基础设施 smoke，不是模型 benchmark 分数。通用数据、GPU 和评估协议见 `web/REPRODUCTION.md`。

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
# 当前最终修复：297 passed, 2 skipped；综合聚焦：156 passed
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

## 6. 已接受的真实 SCO 证据

- Candidate `pt-gyfupguq` SUCCEEDED/worker Succeeded；report `reproduction/logs/candidate_bwrap_smoke_sco_r3.json`，SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
- EvalPlus `pt-bfkp5k0d` SUCCEEDED/worker Succeeded；report `reproduction/logs/evalplus-bwrap-smoke/20261113T035651Z/smoke_report.json`，SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`。只覆盖一个 HumanEval+ 任务的正反 official comparator。
- BFCL `pt-fdvsxqax` SUCCEEDED/worker Succeeded；receipt `reproduction/logs/bfcl-bwrap-check-r2/check.json`，SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。只覆盖两次受控 native RPC。
- Meta `pt-zj1l9q1t` SUCCEEDED/worker Succeeded；report `reproduction/logs/meta-bwrap-task6-r4-20261114T173158Z/run/report.json`，SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。

Meta 证据由外部 actor `huangsiyuan1` 提交，实际为 8×A100、N3lS+N2lS candidate specs、`root_mapping=true`，经用户授权接受。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 在启动前失败；不得写成 A800 pass。该证据仅属历史。

当前 `313b6bc` 最终 gates（均 8xA100/root_mapping=false）：combined `pt-v6m10c4t` 整体/worker **FAILED** 仅因 Meta provider completion failure；candidate/Harness/BFCL exit 均 0。Candidate report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`；Harness `1 passed in 23.26s`，log SHA256 `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`；BFCL 两次 RPC，receipt SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`，key hygiene SHA256 `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`。combined Meta ordinal 0 HTTP 502、retry HTTP 200 incomplete SSE，failed report SHA256 `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`，分类为外部 relay/provider completion failure。

唯一 Meta-only retry `pt-x6b4ista` SUCCEEDED/worker Succeeded/exit 0；report `reproduction/logs/final-meta-a100-20261115T043755Z/run/report.json` SHA256 `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`。ok/completed、same thread、无重复 job/bootstrap、exact gpt-6-astra、3 dynamic calls、11 key hygiene + 7 cleanup、durable job 0、shell_tool=false；installation_id=0644 且 run-owner 精确匹配。EvalPlus 保留上述历史 one-task smoke，未在 313b6bc 重跑。最终独立 re-review 已确认三项生产修复和两项发布修复全部关闭，无新增 Critical/Important，结论为 Ready to merge Yes。

## 7. 下一道实验门

Task 7 的独立安全审阅及修复后 re-review 已由 controller 完成并通过。即使隔离审阅关闭，五轮前仍须完成五类 benchmark 最小 smoke、逻辑 GPU 0–3 的健康四卡 Qwen 服务、当前 694 `python RSIFlow_4B/launch.py --check` 和独立 experiment preflight review。不要从本手册的 smoke 命令直接跳入 A0+B1–B5、SFT 或最终评估。
