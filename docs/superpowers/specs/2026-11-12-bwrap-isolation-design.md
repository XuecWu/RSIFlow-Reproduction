# RSI-Flow bubblewrap 隔离子系统设计

日期：2026-11-12  
状态：用户已批准设计；实施计划已编写，等待用户审阅计划并选择执行方式
范围：Qwen3-4B 主线的 Meta、Task candidate 与 native benchmark 隔离，不启动五轮实验

## 1. 目标与边界

在当前本机和 SCO 都不支持 Landlock 的条件下，提供一个显式、可审计、fail-closed 的 bubblewrap 隔离后端，使真实主控制流能够完成以下验证：

1. Task candidate 与 EvalPlus worker 不能读取宿主私有文件、访问外网或逃逸资源限制。
2. BFCL 等 native benchmark 能使用官方依赖环境、写指定输出并通过既有 RPC 调用受控模型服务。
3. 持久 Codex app-server 能执行 native dynamic tools，并在重启后恢复同一 thread；真实 `OPENAI_API_KEY` 不进入沙箱。
4. 任一隔离能力缺失都返回基础设施错误，不自动退化为无隔离执行，不把模型答错记为隔离成功。

本设计不改变 3000/744 数据、五轮演化策略、模型、endpoint、SFT 参数或 694 评分口径。bubblewrap 是已声明的工程差异，不能宣称与作者原始 Landlock 运行环境完全相同。

## 2. 已有证据与方案选择

已确认：

- `pt-4hdyar4r` 中，现有 Meta 运行时构建、namespaces、chroot 和 capability 前置阶段可执行，最终仅在 `_apply_landlock()` 返回 errno 38。
- `pt-vcq25ldi` 中，共享盘 bubblewrap 0.6.1 在无 `/proc` 条件下通过 `/data`/`/root` 隐藏、运行时只读、scratch 可写和外网不可达检查。
- 中转代理的非流式和流式 Responses 已成功；`ResponsesTransport` 能在宿主持有密钥，`bridge.py` 能在沙箱内提供无密钥 HTTP-to-Unix 转发。

考虑三条路线：

1. **选择：bubblewrap + 既有 seccomp/UID/capability/rlimits。** 可选择性绑定现有 Python 和官方评测环境，不必复制大型 conda 环境，适合 Task 和 benchmark；需要实现受信 inner launcher 与持久 Meta 会话适配。
2. 仅删除 Meta chroot 中的 Landlock。改动较小，但不能覆盖当前 `LinuxSandbox` 和 native benchmark，且大型评测环境复制成本高，不作为完整方案。
3. 更换支持 Landlock 的内核/环境。最接近上游，但当前机器不具备该条件，仍可作为未来对照验证路线。

## 3. 安全不变量

所有 profile 必须满足：

- 显式选择 `bwrap` 后端；不自动 fallback 到 Landlock、宿主机直接执行或 `danger-full-access`。
- 固定 bubblewrap 二进制路径、版本和 SHA256；诊断目录 `reproduction/preflight` 不作为生产依赖。
- 默认 `--unshare-all`、`--new-session`、`--die-with-parent`，不挂载真实 procfs，不共享宿主网络命名空间。
- 只挂载代码定义的受信只读路径和专用可写目录；用户配置不能授予 `/`、`/data`、`/home`、凭据目录或 checkpoint 根的宽泛访问。
- `OPENAI_API_KEY`、其他凭据和宿主环境默认不进入沙箱；子进程使用明确的最小环境变量。
- seccomp 在 bubblewrap 完成挂载和 namespace 设置后、执行不可信程序前加载。不能在宿主 `preexec_fn` 中提前禁止 bubblewrap 自身需要的 mount/unshare。
- 保留 CPU、虚拟内存、文件大小、进程数、文件描述符、输出字节和 wall timeout 限制；超时终止整个进程组并回收临时目录。
- 基础设施失败与候选失败分开记录。沙箱不可用、RPC 中断或官方 runner 未启动均为 infrastructure error，不得记成模型零分。

## 4. 组件设计

### 4.1 后端描述与资产清单

新增受版本控制的隔离配置，记录：

- backend ID：`linux_bwrap_seccomp_v1`；
- bubblewrap 版本、SHA256、来源和预期平台；
- inner launcher、seccomp profile 与资源限制的源码 hash；
- profile 名称和允许的功能，不开放任意宿主路径参数。

新增 Git 忽略的生产资产目录 `assets/isolation/`。真实二进制由受控命令安装并校验；当前 `reproduction/preflight/isolation/bin/bwrap` 只保留为诊断证据。

### 4.2 BubblewrapBackend

`BubblewrapBackend` 负责：

1. 验证二进制、inner launcher、运行时目录和 profile。
2. 创建专用 scratch/output，拒绝符号链接逃逸和非专用目录。
3. 生成只读/可写 mount 映射以及最小环境。
4. 通过受信 inner launcher 完成 UID/GID、capability、rlimits 和 seccomp 设置后 exec 目标。
5. 返回统一的 `SandboxResult`、会话和 negative-probe 证据；保留现有 `SandboxUnavailable` 分类。

受信 inner launcher 放在只读运行时中。它不接受任意 pre-exec Python 代码，只接受经过校验的 profile 和固定目标命令。

### 4.3 三种执行 profile

**Candidate profile**

- 用于 `LinuxSandbox`、TACO、EnvScaler 和 EvalPlus worker。
- 只读绑定干净的系统 Python、标准库和必要动态库；scratch 唯一可写。
- 禁止外网、新进程和非必要 syscall；会话模式保留有界 stdin/stdout JSON-lines RPC。
- `LinuxSandbox` 成为显式后端选择入口，现有调用者不需要改变评分逻辑。返回证据中的 backend 字段必须如实写 `linux_bwrap_seccomp_v1`。

**Native benchmark profile**

- 用于 `tool_sandbox.run_native()` 和 BFCL `check_native`。
- 只读绑定固定官方 Python 环境、复制的 native work 和受信入口；仅 output 可写。
- 保留两条 RPC 文件描述符，禁止外网；允许官方 runner 所需线程和原子 rename，但禁止创建不受控进程。
- 不默认挂载 procfs。若官方 runner 因缺少 `/proc` 失败，先记录具体基础设施错误；不得直接挂载宿主 proc。只有在独立探针证明 fresh procfs 可用且不会泄露宿主进程信息后，才可新增单独 profile 版本。

**Persistent Meta profile**

- 用于 `CodexAppServer` 和持久 thread，不改变 `NativeExperimentTools` 的控制权。
- Codex 进程在沙箱内，Python 控制器和 dynamic-tool executor 留在沙箱外，双方只通过既有 stdio JSON-RPC 交互。
- 只读绑定 pinned Codex、catalog、受信 bridge 和必要运行时；源代码、声明的 experiment workspace、run log 目录按主线需要写入。
- 复用现有运行时的受控 `/proc/self/exe -> /codex` 静态链接满足 Codex 自定位，不因此挂载真实 procfs。
- `.git`、私有凭据、模型权重、原始数据和无关宿主目录不挂载。可写路径采用明确清单，不直接绑定整个仓库根目录。
- `codex_home` 使用 run 专用的持久目录，重启时保留；静态 runtime 每次重新校验。不得复用每次调用都会清空 `codex_home` 的 disposable stage 逻辑。

### 4.4 无密钥 Meta provider 链路

复用现有组件：

```text
Codex in bwrap
  -> 127.0.0.1:18443/api/v1（沙箱内 bridge.py）
  -> 专用 transport.sock
  -> 宿主 ResponsesTransport
  -> 固定 OpenAI 中转代理
```

- 只有宿主 `ResponsesTransport` 读取私有凭据并附加 Authorization。
- 沙箱内 bridge 和 Codex 不接收 key，不能访问宿主网络。
- 保留模型身份、请求预算、SSE 完整性和响应证据检查；不新增模型或 endpoint fallback。
- `CodexAppServer` 接受受控启动器和运行时生命周期接口，不自行拼装未审计命令。
- thread resume 必须恢复原 `thread_id` 和持久 `codex_home`；恢复时先核对 running jobs，不能重复 bootstrap 或 rollout。

## 5. 生命周期与失败处理

- 启动前：校验所有资产、路径、namespace 和 negative probe；任一失败即停止。
- 运行中：记录 backend、mount 清单、namespace inode、seccomp/limits hash、退出码和资源使用；敏感请求头及 key 不进入日志。
- 重启：区分 Codex 进程退出、provider 连接失败和 Task job 仍在运行。持久 Meta 先恢复 transport、thread 和 job 状态，再决定是否继续。
- 清理：终止进程组、关闭 RPC/relay、回收 scratch；保留脱敏回执和必要的持久 `codex_home`。
- 不支持时：返回 `SandboxUnavailable` 或 `BackendUnavailable`，不自动尝试宿主机执行。

## 6. 测试与验收

### 6.1 先写失败测试

覆盖以下行为，并先观察在旧 Landlock-only 实现上失败：

- bwrap 缺失、hash 不符、namespace 不可用和非法 mount 都 fail closed。
- `/data`、私有凭据、checkpoint 和宿主 `/root` 不可读；运行时不可写，scratch 可写。
- 外网、mount、setns、ptrace、非授权进程创建被拒绝；stdin/stdout 和 RPC 正常。
- 超时、输出超限和进程组清理正确，不留下运行中的子进程。
- `codex_home` 在重启后保留，静态 runtime hash 变化会被拒绝。
- 沙箱环境及转交子进程均不含真实 provider key。

### 6.2 本机与 SCO 真实 smoke

按顺序执行，前项失败则不进入后项：

1. candidate 一次性执行和持久 session，包含正常结果、模型异常、超时和负向逃逸矩阵。
2. EvalPlus 固定官方最小样例，确认官方 comparator 和 candidate RPC 正常；模型答错与基础设施错误分别报告。
3. BFCL `check_native`，完成官方导入、注册和两次受控 CPU RPC。
4. 持久 Codex native dynamic tools：真实调用一个无副作用工具并收到回执。
5. 关闭并重启 app-server，恢复原 thread ID；确认没有重复 job，再继续一个短 turn。
6. 上游完整 pytest、网站测试、SCO 终态和资源释放检查。

真实 smoke 使用固定短输入，不启动 A0+B1–B5，不生成或宣称 benchmark 分数。

### 6.3 独立安全审阅

由未参与实现的 reviewer 检查：

- mount 和环境是否过宽，是否存在私有路径或 key 泄露；
- seccomp 是否在正确阶段生效，RPC 文件描述符是否形成逃逸通道；
- 持久目录恢复、进程组清理和失败分类是否正确；
- 测试是否真实执行，是否存在跳过、mock 代替真实证据或状态误判。

所有 Important/Critical 意见处理完成后，才允许把 Task 3 标为完成。

## 7. 审计与交付

每阶段记录本地 commit、测试命令、证据 SHA256、SCO job ID 和终态。公开网页只展示脱敏状态，不公开原始请求、私有路径内容或凭据。

完成本设计只代表解除隔离基础设施阻断。五轮启动仍需同时满足：持久 Meta 验证、四卡服务健康、五类 benchmark 最小 smoke、当前 694 配置 `--check` 和最终独立 preflight review。

## 8. 明确不做

- 不删除测试断言或把 Landlock 失败改名成通过。
- 不在所有执行器中统一放开网络或 `/proc`。
- 不把 bwrap 最小探针当成完整安全等价证明。
- 不在本设计实施过程中启动五轮、SFT 或最终 694 评估。
- 不更换模型、endpoint、数据 release 或评分协议。
