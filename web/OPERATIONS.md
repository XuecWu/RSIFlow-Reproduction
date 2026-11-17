# RSI-Flow 调研与网站操作日志

本文件是本次工作的追加式记录。敏感凭据不得写入本日志。

## 任务边界

- 目标：源码调研、`AGENTS.md`、复现决策文档、静态展示网站与 cpolar 发布。
- 用户约束：本次不创建 Conda 环境、不安装实验依赖、不下载模型/数据、不提交 SCO 作业、不运行 GPU/API 实验或 smoke。
- 允许的运行：网站自身测试、JavaScript 语法检查、静态 HTTP 服务与 cpolar 验证。
- 不执行 git commit 或 git push。
- Ruling: 不另建 git worktree 或 SDD 私有工作区 — 用户已指定克隆目录与交付位置，且明确不提交 commit；以本文件作为持久化执行 ledger — 若判断有误，只会增加少量流程差异，不影响交付内容。
- Pre-flight: Task 1 的 commit 和日志供 Task 2–5 使用；Task 2 的源码事实供 Task 3–4 使用；Task 3 的复现状态供 Task 4 网站测试使用；Task 4 的网站供 Task 5 发布，未发现接口冲突。

## 2026-11-11

### 11:04–11:06 UTC — 获取源码

- 直连执行 `git clone https://github.com/1HUAN1/RSI-Flow.git`，失败：`curl 16 Error in the HTTP2 framing layer`；目标目录未创建。
- 检查现有 mihomo：控制 API 返回 v1.19.13，经 `127.0.0.1:7890` 访问 GitHub 返回 HTTP 200。未启动新代理、未切换节点、未更新订阅。
- 仅对第二次克隆设置 `http_proxy`/`https_proxy`，克隆成功，共检出 1354 个文件。
- 固定版本：`496d3cfb9ff39997d107d0ff29e746b07e638180`，分支 `main`。
- 克隆后 `git status --short` 无输出，上游工作区干净。
- 根目录原本不存在 `AGENTS.md`，本次将新建。

### 源码分析与 AGENTS

- 阅读根 README、SOURCE_RELEASE、launcher、launch_meta、persistent_meta、controller_tools、task_adapter、meta_loop、skill_memory/META_SKILLS、数据准备/协议、4B/8B 配置与入口、runtime GPU/SFT/评估代码和测试。
- 核对出 17 个离线测试文件；按用户要求，本次未执行上游 pytest 或 `--check`。
- 新建根级 `AGENTS.md`，覆盖实际调用链、Task/Meta 主线、三类更新、技能记忆、3000/744 协议、有效 SFT 参数、GPU/SCO 约束、资产、恢复和网站维护。
- Ruling: 有效 SFT 参数以 `TaskAdapter._config()` 最后应用的 `SFT_PRESET` 为准 — base JSON 的 rank 16/LR `2e-5` 会被覆盖为 rank 8/LR `2e-6` — 若误写会改变复现超参数，因此文档同时保留覆盖关系。
- Ruling: 未修改源码时推荐 dev 四卡顺序运行，主分区八卡双流水线需先改 GPU 映射并测试 — 服务/训练子进程会重写 `CUDA_VISIBLE_DEVICES`，且默认 ports/output registry 会冲突 — 若忽略会导致两套实验争用 GPU、端口或状态文件。
- Ruling: 技能 seed 命令以 argparse 的 `--skill-seed` 为准，记录 META_SKILLS 文档中 `--meta-skill-seed` 的差异 — 若直接使用文档参数会启动失败。

### 复现文档与网站 TDD

- 新建 `REPRODUCTION.md`、`ENVIRONMENT.md`、`ASSETS.md` 和 `data/reproduction.json`；13 个实验阶段均为 `not_run_pending_approval`，未执行其中命令。
- RED：`python3 -m unittest discover -s web/tests -v` 首次运行 9 项，5 项失败；缺失首页、repository JSON、样式、JS、README/RESULTS，以及手册中的 `N3lS.Ii.I60.8` 说明。
- 实现静态首页、响应式 CSS、原生 JS、两个 JSON、README 和初步 RESULTS，并补全主分区规格。
- GREEN：同一测试命令 9/9 通过；`node --check web/app.js` 通过。

### 本地与公网发布

- 检查 8000 端口时返回 `Errno 98 Address already in use`，按预案改用 18000；未检查或干扰占用者。
- 静态 HTTP 服务 PID `1982863`，监听 `127.0.0.1:18000`。首页、CSS、JS、两个 JSON、REPRODUCTION、RESULTS 均返回 200，首页关键内容检查通过。
- cpolar PID `1983400`，建立 `https://49a63412.r36.cpolar.top`。
- 默认 4040 API 返回空内容；cpolar 日志显示 web interface 实际为 4048。4048 API 同样返回空内容，因此改从隧道日志取得 URL，不将其误判为隧道失败。
- 公网首页及相同 7 个资源均返回 200，关键内容检查通过。
- 更新 `README.md` 的端口/dashboard 说明和 `RESULTS.md` 的真实 URL、PID、测试与发布证据。
- 未创建 Conda 环境、未下载实验资产、未运行上游测试/预检、未提交 SCO 作业、未调用模型或 API。

### 独立复核与修复

- 独立 reviewer 核对六项源码事实无误，并实测当时 9/9 网站测试和 JS 检查通过。
- Important 1： reviewer 发现本地连接拒绝、旧公网 404。通知与 TaskList 证实两个后台任务均在 600 秒达到默认 `timeout_ms=600000`，旧 PID 已不存在；根因不是网页内容。
- Important 2：`web/logs/cpolar.log` 位于公开根目录，包含 client ID 和网络端点元数据。
- 为公开面问题新增 `test_raw_runtime_logs_and_client_ids_are_not_public`，先观察到 FAIL（`web/logs` 存在）。
- 将原始日志/PID 移到公开根目录外的 `web-runtime/logs/`，更新 README 和 RESULTS；测试转为 PASS，完整套件 10/10。
- 使用 `disable_timeout=true` 重启持久 HTTP 与 cpolar。当前 HTTP PID `2000430`，cpolar PID `2000586`，当前公网 `https://4a63b603.r36.cpolar.top`。
- 本地和新公网地址的首页、CSS、JS、两个 JSON、REPRODUCTION、RESULTS 均重新返回 200；新公网首页内容检查 PASS。
- Final: fixed 服务生命周期 — 本地/旧公网 7 资源由 HTTP 000/404 转为新本地/公网全部 200，持久任务无默认 600 秒 timeout。
- Final: fixed 原始日志公开 — 新安全测试 RED→GREEN，完整网站套件 10/10。
- Final: minor (deferred): ASSETS 多数外部模型/数据集/评测器仍可补权威 URL、revision、许可证和建议路径；当前已明确未下载/未核验，留待后续资产调研。
- 未进行二次 reviewer 复核；关键修复均由失败复现、自动化测试和真实本地/公网请求验证。

### 最终验证

- `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s web/tests -v`：10/10 通过。
- `node --check web/app.js`：通过。
- 本地 `http://127.0.0.1:18000` 与公网 `https://4a63b603.r36.cpolar.top`：首页、CSS、JS、两个 JSON、REPRODUCTION、RESULTS、README、OPERATIONS 共 9 个资源均返回 200。
- 公网首页关键内容 PASS；公网 RESULTS 的当前 URL、10/10 和新 PID 状态检查 PASS。
- `ps` 确认 HTTP PID `2000430` 与 cpolar PID `2000586` 仍在运行，两个持久后台任务均为 running。
- `git status --short --untracked-files=all` 仅显示本次 AGENTS 与网站源文件；`web/data` 和 `web-runtime/logs` 按上游 gitignore 规则被忽略，但文件实际存在并用于网站/运维。未发现测试生成的 `__pycache__`。
- 没有提交或推送 git，没有任何 SCO/GPU/API 实验作业需要清理。


### 4B 复现准备与 preflight 修复（当前）

- 已获批准的范围：4B、主分区 8×A800 仅用 GPU 0–3、公开数据同协议重构、`gpt-6-astra`、ACE 跳过；未启动五轮。
- v4 READY 记录 744 frozen / 694 scored / 50 pending；真实 release 测试与四类 official spec 已验证。
- Qwen Code 独立审阅为 CHANGES，主 agent 已处理证据错位、公开口径、真实测试、操作 SHA256 和过程快照问题。完整操作见 `../reproduction/audit/operations.jsonl`，关键结果见 `key_results.jsonl`，日常审计见 `daily/2026-11-11.md`。
- SCO probe `pt-r6np9yvd` 仍为 STARTING；Meta 私有凭据未提供；没有模型 benchmark 分数。


## 2026-11-12

### OpenAI 中转代理与隔离替代诊断（进行中）

- 用户提供并授权使用固定 `OpenAI/gpt-6-astra` 与 `http://token.acerobotics.com/v1`；私有凭据文件权限为 600，目录被 Git 忽略，密钥未写入日志或公开页。
- 首次 SDK `/models` 在 90 秒审计上限超时。分段计时定位到冷缓存 `import openai` 用时 67.18 秒；延长后 SDK 成功返回模型列表并包含 `gpt-6-astra`，不是中转代理无响应。
- 原始 Responses 非流式请求返回 HTTP 200、`completed` 和正确模型名；流式请求返回 15 个 SSE 事件并以 `response.completed` 结束。Codex native dynamic tools 与 thread resume 仍未验证。
- 发现 `persistent_meta.py` 原生线程仍写死旧 `autodl/DeepSeek`，通过 RED→GREEN 测试改为继承有效 base 配置；`test_persistent_meta.py` 8/8 通过。
- 本机 Landlock 返回 ENOSYS；bwrap 的 proc 挂载被拒绝，但无 proc 挂载的最小沙箱与 namespaces 可运行。这些只用于路线筛选，不能替代 SCO 证据。
- dev CPU 规格提交返回 `work spec not found`，未创建作业。改用主分区已知规格的分钟级探针，不加载模型、不启动实验。
- `pt-vqm5sa3h` 在 Landlock 前因 `/usr/lib/python3.12` 不存在失败。新增标准库路径发现测试并修复；单元测试通过，`pt-03lf4y6x` 复测进行中。
- 修改后上游全量回归为 167 passed、2 skipped、1 failed，唯一失败仍是已知的本机 Landlock 用例；没有五轮或 benchmark 分数。

### 隔离候选后端 SCO 结果

- `pt-03lf4y6x` 暴露 `/etc/python3.10` 版本化符号链接白名单问题；新增真实符号链接测试并仅允许所选解释器对应的 `/etc/pythonX.Y`，未放宽到其他目录。
- `pt-4hdyar4r` 终态 SUCCEEDED；探针输出 ready=false，唯一错误为 `_apply_landlock()` errno 38，已越过运行时构建和其余隔离前置阶段。
- `pt-vcq25ldi` 终态 SUCCEEDED；共享盘 bwrap 0.6.1 在无 `/proc` 条件下通过 `/data`/`/root` 隐藏、运行时只读、工作区可写、外网不可达六项最小检查。
- 最新全量回归为 169 passed、2 skipped、1 failed，唯一失败仍为既有 Landlock 环境用例；网站 11/11 通过。
- 上述结果只支持进入替代后端集成设计与独立安全审阅，不代表 Codex native tools、thread resume、真实 benchmark 或五轮已可运行。

## 2026-11-13 至 2026-11-15

### Bubblewrap 集成与真实最小 smoke

- 固定 bwrap 0.6.1、SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`，显式后端为 `linux_bwrap_seccomp_v1`；无 host/Landlock fallback 或 `danger-full-access`。
- Candidate `pt-gyfupguq` SUCCEEDED/worker Succeeded，7 functional + 18 negative + 5 timeout + 3 session 全通过；report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
- EvalPlus `pt-bfkp5k0d` SUCCEEDED/worker Succeeded，单任务 canonical Base/Plus pass、wrong Base/Plus fail；smoke SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`，official canonical/wrong SHA256 `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2` / `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514`。不是 benchmark 分数。
- BFCL `pt-fdvsxqax` SUCCEEDED/worker Succeeded，恰好两次受控 native RPC；`check.json` SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。不是 benchmark 分数。
- Meta `pt-zj1l9q1t` SUCCEEDED/worker Succeeded，same thread、无重复 job/bootstrap、3 dynamic calls、11 key hygiene + 7 cleanup true、durable job exit 0；report SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。
- Meta 证据由外部 actor `huangsiyuan1` 提交，实际 8×A100、N3lS+N2lS candidate specs、`root_mapping=true`，经用户授权接受。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 启动前失败，不宣称 A800 pass。
- Codex native-shell descendant hygiene 未执行；仅采用批准的 `shell_tool=false` construction-level gate，未放宽 seccomp。

### Task 7 回归与发布检查

- Base 完整套件重现 `276 passed, 2 skipped, 1 failed`；旧 Landlock Harness 测试保持名称和断言，显式切换 bwrap 后仍 RED，定位为 validation worker 自装 Landlock。最小接入 pinned backend 并移除非必要且被 backend 拒绝的 token 命名环境变量后，真实测试 GREEN：`1 passed in 32.98s`。
- 聚焦回归 `79 passed`；brief 原命令最终完整套件 **277 passed, 2 skipped**；website **11/11**，`node --check`、`git diff --check` 均通过。
- 现有 secret scan 通过；`assets/isolation/`、凭据、raw provider requests、私有轨迹和大输出保持 ignored/unlinked，`web/` 无 symlink。公开内容仅含脱敏证据，不包含 raw prompts/responses。
- 本任务未提交 SCO job，未启动五轮、SFT、最终 694/744、ACE 50、8B、Qwen 服务或正式演化训练，未 push。
- Task 7 最终独立安全审阅由 controller 在 commit 后执行；五轮前的五类 benchmark smoke、四卡 Qwen 健康、当前 694 `--check` 和 experiment preflight 仍是独立门槛。

### Consolidated final-review fix wave

- 生产 fresh home 的 installation-ID 测试先 `5 failed`，再以原子 no-follow 0644 生产 helper 转为 `5 passed`；fresh smoke production-path startup `1 passed`。有效 inode/content 保留，symlink/非普通文件/非法 UUID fail closed。
- same-basename/legacy/ownership/symlink 测试先 `5 failed`；central `.run-owner.json` 绑定 resolved full run identity 后，组合 storage/installation `14 passed`。不同 fresh run 不再共享，same-path 和可证明 legacy resume 保留。
- manifest/launcher tamper 与 missing receipt 先 `3 failed`；candidate/native/Harness no-spawn 联合 `9 failed`。ignored installer receipt 强制 pin 两类 security assets 后 `10 passed`；persistent Meta static-copy pinning 未改。
- 综合 focused **156 passed in 50.41s**；完整 suite **297 passed, 2 skipped in 90.22s**。Ruff 修复两个新增 import-order 后仍有 18 个既有 legacy findings，不宣称 clean。
- 当前公开计数已同步为 297/156。既有 SCO IDs/hash 不是新代码证据；受影响 candidate/native/Harness/Meta SCO rerun 和独立 re-review 均待 controller。本 wave 未提交 SCO job、未运行真实 provider smoke、未启动训练/评估、未 push。

### 最终 A100 真实 gates 与 evidence publication

- `313b6bc` combined `pt-v6m10c4t`（8xA100/root_mapping=false）整体/worker **FAILED** 仅因 Meta 外部 provider completion failure；candidate/Harness/BFCL exit 均 0。candidate SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`，7/18/5/3 checks 全过且 infrastructure_error=null；Harness `1 passed in 23.26s`，log SHA256 `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`；BFCL 两次 RPC，receipt SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`，key hygiene 全 true，SHA256 `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`。
- combined Meta ordinal 0 HTTP 502、retry HTTP 200 incomplete SSE，failed report SHA256 `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`；分类为外部 relay/provider completion failure，未改代码隐藏。
- 唯一 Meta-only retry `pt-x6b4ista`（8xA100/root_mapping=false）SUCCEEDED/worker Succeeded/exit 0；report SHA256 `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`。ok/completed、same thread、无重复 job/bootstrap、exact gpt-6-astra、3 dynamic calls、11 key hygiene + 7 cleanup、durable job 0、shell_tool=false；installation_id=0644 且 run-owner 精确匹配。
- Task 6 外部 actor `pt-zj1l9q1t` 仅保留为历史证据；当前最终 Meta gate 为 `pt-x6b4ista`。EvalPlus 仍为历史 one-task smoke，未在 313b6bc 重跑。native-shell descendant validation 未执行，五轮/SFT/final eval/Qwen 服务未运行。
- 本 publication step 未改生产代码/测试、未提交新 job、未启动训练、未 push；仅更新证据、audit 和公开状态。独立 re-review 仍为 controller gate。

### 2026-11-15 最终独立审阅关闭

- 未参与实现的 reviewer 对 consolidated fix wave 复核：生产 installation_id、run ownership、全 profile 受信资产，以及测试计数和凭据措辞五项均为 ADDRESSED；无新增 Critical/Important，Ready to merge Yes。
- 保留一个 Minor：非 UTF-8 installation_id 的异常类型可能为 TypeError 而非预期 ValueError；仍在 Codex 启动前拒绝且不改写文件，不影响隔离或状态安全。
- controller 在最终 HEAD 再次运行完整测试：297 passed、2 skipped；网站 11/11、Node、diff 检查通过。cpolar 本地源切换到最终审阅工作树，公网内容另行核验。

### 2026-11-15 公网服务核验（历史记录，服务其后已停止）

- 最终 HTTP 持久任务为 `bash-38l47xmz` / PID `2157843`，工作目录为最终审阅工作树；cpolar 保持原持久任务 `bash-09cysqyj` / PID `2000586`。
- 当时公网入口为 `https://5340b001.r36.cpolar.top`。本地 JSON 已断言独立审阅通过、Meta job `pt-x6b4ista` 和 297/2 测试计数；公网 `RESULTS.md` 已读取到相同审阅结论。
- **后续状态**：上述两个持久任务在上次会话关闭时被终止，本地 18000 连接拒绝、旧公网 URL 返回 404。该 URL 与 PID 自此只作历史留档，当前入口见下方 2026-11-16 节。

## 2026-11-16

### 授权与范围

- 用户授权：本地把 `reproduction/4b-bwrap` fast-forward 合入 `reproduction/4b-main8`，推送两个复现分支到内部 GitLab 备份，整理多 agent 交付复盘，并从主工作树恢复公开展示。
- 本阶段只做代码/文档交付、备份与网站恢复；**未启动新 SCO 作业、五轮演化、SFT、最终 694/744 评估或模型服务**。
- 使用当前 Herdr tab 的 Qwen4-Max、Codex、Kimi 三方通道；主 agent 位于 `physical-rsi-selector`（client/server 0.9.5、protocol 22）。真实探针分别返回 `RSIFLOW_DELIVERY_KIMI_OK`、`RSIFLOW_QWEN4_DELIVERY_OK`、`RSIFLOW_DELIVERY_CODEX_OK`；Qwen 由 `qwen3.8-max` 切换为 `qwen4-max`，Codex 固定 `/data/wuxuecheng/npm-global/bin/codex`（`codex update` 后仍 0.169.0，按更新器要求在原 pane 重启，未信任 hooks）。

### 合并前：账本保护与预检

- 主工作树两份未提交追加账本 `reproduction/audit/operations.jsonl`、`key_results.jsonl` 与功能工作树的追加记录、`Meta_logs/` 全部保持原样：不 reset、不 clean、不 stage、不 commit、不删除。
- 合并前主工作树 `operations.jsonl` SHA256 `ea9403380b77b5f1100c6bce5b66578cc3cf4e218ec9985bea8d2b7f0749858e`、`key_results.jsonl` SHA256 `82c10d582e9459d62d03c3b42c6ba9efaabdfd1254114519389259fd1ba92c0a`，两份均可逐行解析；**merge 后重算两值完全不变**。
- 独立 Kimi 交付报告 `reproduction/audit/reviews/delivery-integration-kimi.md` 指出：两个工作树的 `operations.jsonl` 在 **sequence 581 之后分叉**（同号记录语义不同）。处置：两份原件均保留，**禁止覆盖或直接拼接**，跨工作树引用需逐条语义对账。该报告的 CHANGES 是"合并/GitLab/公网尚未执行"的过程门槛，不是代码审阅失败。
- 功能工作树预合并验证：完整 Python `297 passed, 2 skipped in 92.22s`；website `11/11`；`node --check web/app.js`、`git diff --check` 通过。生产代码相对最终审阅 HEAD 未变。
- `Meta_logs/` ignore 修复：**由 Codex 按主 agent 指令执行，且只修改 `.gitignore`**（增加 `Meta_logs/`）；主 agent 回源核验 `git check-ignore -v Meta_logs/example.json` 命中、功能工作树 status 不再显示该未跟踪目录；既有 `Rollout_logs/`、`local_baseline/` 规则不变。经独立 Kimi scoped re-review 为 PASS。

### GitLab 备份推送与远端核对

- `origin` 保持 GitHub 上游 `https://github.com/1HUAN1/RSI-Flow.git`，**未向其推送**；新增 `gitlab` 指向 `git@gitlab.acerobotics.com:wuxuecheng/rsiflow.git`，SSH 实测欢迎身份 `@wuxuecheng`。
- 推送前 Codex 对 HEAD 及可达历史做体积与密钥形态扫描：无超过 50 MiB 的 blob、无已跟踪 symlink、常见私钥/token 形态未命中；报告 `reproduction/audit/reviews/delivery-gitlab-codex.md`。
- GitLab 既有 `main` 为 `0c47d98df09327ba09f4fad9aaa9914837ffa280`，与本地历史无共同提交：**保留该分支，不强推、不覆盖、不建 MR**。
- 初次备份仅新建并推送两个同名远端分支 `reproduction/4b-main8`、`reproduction/4b-bwrap`；**推送当时** `ls-remote` 两者均为 `3a66b655b764224acb21a108e59e7066f8503867`（即合并与初次备份点），与该时点本地 HEAD 一致。其后的发布文档提交与再次推送会推进两端 SHA，**当前分支与远端 SHA 以 `git log` / `git ls-remote` 实测为准**，本文不写死后续 SHA。**GitLab CI 未验证**，不宣称流水线通过。

### 合并后主工作树：真实失败与恢复（不只记成功）

- fast-forward 合并完成，交付分支为 `reproduction/4b-main8`；**合并与初次备份点 HEAD 为 `3a66b655b764224acb21a108e59e7066f8503867`**（该点的复盘/审查提交为 `3a66b65`）。承载本次发布文档更新的提交在该点之后形成，当前 HEAD 以 `git log` 实测为准。
- **失败**：合并后主工作树首次完整 pytest 为 `13 failed, 284 passed, 2 skipped`。逐项定位后确认全部失败源于主工作树缺少 Git-ignored 的 `assets/isolation/`（pinned bwrap 二进制与 installer receipt 不随 git 分发），**不是代码或测试回归**；未修改任何生产代码或测试断言来"修绿"。
- **恢复**：用既有 `reproduction/scripts/install_bwrap.py`，以功能工作树的同一 pinned binary 为 `--source` 安装到主工作树 `assets/isolation/`。binary SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`；receipt 的 `manifest_path`/`manifest_sha256`（`RSIFlow/configs/bwrap_isolation.json`）与 `launcher_path`/`launcher_sha256`（`RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py`）均匹配。
- **复验**：受影响测试 `51 passed in 48.77s`；随后完整 pytest **`297 passed, 2 skipped in 89.15s`**；website `11/11`、`node --check web/app.js`、`git diff --check` 全部通过。
- 经验：Git-ignored 安全资产是**每个工作树的本地前置条件**，合并/克隆后必须先按 installer 重建并校验 receipt，再跑真实隔离测试；否则会把环境缺失误读成代码回归。

### 公网服务恢复与实测

- 旧服务状态如实记录：`bash-38l47xmz`（HTTP）与 `bash-09cysqyj`（cpolar）已因会话关闭终止，本地 18000 连接拒绝、旧公网 URL 404；不宣称旧服务仍运行。
- 从**主工作树** `web/` 重新拉起持久服务：HTTP 任务 `bash-8s79760l` / PID `3697240`，`python3 -m http.server 18000 --bind 127.0.0.1 --directory <主工作树>/web`；cpolar 任务 `bash-be8pblb5` / PID `3697621`。
- 从隧道日志读取**实际新 URL** `https://7b96aab1.r61.cpolar.top`（不沿用旧地址）。本地 `http://127.0.0.1:18000/` 返回 200。
- 公网实测 10 个资源全部 200：`/`、`/styles.css`、`/app.js`、`/data/repository.json`、`/data/reproduction.json`、`/REPRODUCTION.md`、`/RESULTS.md`、`/README.md`、`/OPERATIONS.md`、`/process.html`；两个 JSON 按 UTF-8 解析成功，公网 `RESULTS.md` 关键事实（已合并/已备份、新入口、297/2、combined FAILED 与 Meta retry SUCCEEDED）检查通过。
- 原始日志仍位于公开根目录之外的 `web-runtime/logs/`；既有 secret scan 与 raw-log/client-metadata 检查随 website 套件通过。

### 多 agent 复盘与交叉审阅

- Qwen4-Max 新建 `reproduction/RETROSPECTIVE.md`，覆盖阶段历程与 ruling、真实证据、失败根因（区分已确认根因与外部 provider/控制面故障）、可复用经验、五轮前置门与追踪方式。
- 独立 Kimi 首轮任务审阅为 **Spec FAIL / CHANGES**：3 项 Important（旧 URL/PID 被写成当前入口、operations 分叉误写为"子集"、完整 pytest 的 commit 归属不清）+ 1 项 Minor（docs-only 提交列序把 HEAD 自身算作前序且漏 `861debd`）。
- Qwen 仅修改复盘文档完成四项修复；Kimi scoped re-review 四项全部 **ADDRESSED**，无新增 Critical/Important，**Spec PASS、质量 PASS**。
- 归属口径同步纠正：297/2 的完整套件执行分别归属功能工作树预检与合并后主工作树复验；2026-11-15 那次完整 pytest 对应 `861debd`，其后的 docs-only 提交只重检 website，不构成完整套件重跑；写作 agent 未运行的测试只能转引，不冒充自己执行。

### 本次文档更新与边界

- 本节与 `web/RESULTS.md`、`reproduction/PROGRESS.md`、`AGENTS.md`、`reproduction/audit/daily/2026-11-16.md` 的更新在上述实测**之后**形成；这些实测后的文档更新由主 agent 纳入后续文档提交，**具体 SHA 以 git 历史为准**，本文不预写或虚构任何后续 commit SHA。文中出现的 `3a66b655…` 一律指合并与初次备份点，不代表当前 HEAD。
- 未改生产代码、测试、JSONL 账本或其他文件；未提交 SCO，未启动实验，未删除或清理任何文件，未停止仍在运行的服务。
- 所有既有结论边界不变：combined `pt-v6m10c4t` 整体 FAILED（仅 Candidate/Harness/BFCL exit 0）、当前 Meta gate 为 `pt-x6b4ista`、无 A800 Meta pass、native-shell descendant hygiene 未验证、EvalPlus 为历史单任务 smoke、无五轮/SFT/最终评估/模型分数。


## 2026-11-17

### DeepCoder function 16 真实 canary 执行、恢复与独立审阅

- 真实 run 使用固定 16 条 DeepCoder/TACO function、code 单域、A0+B1 单轮、一个实验 attempt 和一个候选；没有换题，不是五轮或独立 benchmark。
- Astra 实际选择 HARNESS。同一 candidate 第一次检查 failed_build，修复后在第二次检查 passed，共使用 2/3 次检查；candidate state 只改变 Harness，model/checkpoint/weights 未变。
- 父代 51、候选 93 次真实 Qwen calls。父代 7、候选 8 个有效 submission 进入 full official verifier，其余 9/8 个无有效答案任务未进入 full verifier，不伪称 16 条均完成完整测试。
- 完整配对为父代 3/16=0.1875、候选 4/16=0.25，delta +0.0625；compare_scores 与 compare_task_differences 均完成，selection 为 accept_candidate，activate 后 candidate state 与 active state SHA256 一致。
- 配对变化为 4 个新成功和 3 个回归，原 3 个成功全部回归。4 个新成功全来自父代零调用失败，3 个回归全为候选零调用失败；过程异常使因果归因受限，接受只代表固定批净正增益。
- skills/context/snapshot、state COMPLETE、finish、9 个 durable jobs 的最终回执和四卡 cleanup 均完成，最终 registry 为 services_stopped。
- supervision 0001 的精确 502 口径为 24 个 distinct failed turns、24 条 error events，另有 24 条对应 turn/completed events；不是 48 个失败 turns。attempt 0002 在同一 run/config/backend/thread 从 candidate_built 恢复，无重复 bootstrap/run_parent，未新增 502，最终 exit 0。
- 独立审阅直接读取原始 receipts、trajectories、model calls、performance、selection、active state、skills/context、snapshots、jobs、events 和 registry，结论为 Spec PASS / Quality DONE_WITH_CONCERNS。
- MODEL 未选择，SFT 未运行；五轮、最终 694/744、独立 validation/benchmark 和作者原始实验复现均未运行，ACEBench 为 skipped 而非 scored。

### 永久文档与网页发布

- 新建 `reproduction/audit/reviews/deepcoder-canary-real-results.md`，发布真实协议、调用、full verifier 覆盖、配对分数、接受与恢复证据、关键哈希和质量限制；不公开 raw prompts/responses、私有轨迹、凭据或大日志。
- 同步 `reproduction/PROGRESS.md`、`web/RESULTS.md`、本节、`web/data/reproduction.json`、`AGENTS.md`、`reproduction/LEARNING.md` 和 `reproduction/REPRODUCTION.md`。当前完整离线套件计数为 **517 passed, 2 skipped**；旧 297/2 保留为明确标注的历史 smoke 阶段计数。
- `web/data/reproduction.json` 新增独立 canary 对象和 experiment stage，记录 16/1/1、HARNESS、2/3、51/93、7/8 full verifier、3/16→4/16、4/3 transitions、accept/activate、COMPLETE、cleanup、0001/0002 恢复和未运行边界；未修改其他 JSON 或 JSONL 账本。
- 发布检查：`python3 -m unittest discover -s web/tests -v` **11/11 OK**；`node --check web/app.js` 通过；`python -m json.tool web/data/reproduction.json` 通过；`git diff --check` 通过；修改文件常见私钥、API key 和 authtoken 形态扫描无命中，website 凭据/raw-log 公开面测试同时通过。
- 本发布只修改上述文档和公开状态 JSON，不改生产代码、测试或其他 JSONL；只创建一个本地 docs commit，不 push，不启动新实验或 SCO。
