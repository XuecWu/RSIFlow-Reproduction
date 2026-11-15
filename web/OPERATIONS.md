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
