CHANGES

# 独立 Kimi 合并与交付只读复核

复核时间：2026-11-16T15:16Z 左右。复核范围是指定工作树、交付文档及两个工作树的只读 Git/账本元数据。本复核没有执行合并、暂存、提交、推送、测试、SCO、服务启动、删除或清理；以下测试与服务项目均是主 agent 后续验收门槛，不是本 reviewer 已执行的结果。

隔离分支已有独立安全审阅的 `Ready to merge Yes`，但当前交付状态仍为 CHANGES：分支可以快进合入不等于账本、GitLab 和公网交付已经完成。

## 1. 合并前必须核对的事实

### 1.1 分支和提交边界

- 主工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow`，分支 `reproduction/4b-main8`，HEAD `05f761a25e65e8531018da448fb0991d08ffd721`。
- bwrap 工作树：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/bwrap-isolation`，分支 `reproduction/4b-bwrap`，实际 HEAD `6c2049b002ad6c4214af34e8ecfb0331cb03adeb`。
- merge-base 是 `05f761a`；`reproduction/4b-main8` 是 `reproduction/4b-bwrap` 的祖先，bwrap 侧有 21 个提交，主侧无独有提交。因此当前是 fast-forward，不需要 `--no-ff`，也不需要制造合并提交。
- `reproduction/audit/reviews/bwrap-sdd-progress.md:217` 仍写最终 HEAD `68cb220`，但实际分支另有一个文档归档提交 `6c2049b`。合并对象必须是实时核对的 `6c2049b`，不能只照抄 ledger 中的旧 HEAD。
- `68cb220..6c2049b` 为六份 review/RESULTS 文档变化；最终完整测试结果仍须在实际合并 HEAD 上重新取得，不能把旧 commit 的计数自动当成本次交付复验。

证据：`reproduction/PROGRESS.md:25-45`、`web/RESULTS.md:17-31`、`reproduction/audit/reviews/bwrap-sdd-progress.md:196-217`，以及上述 Git rev-parse/merge-base/log 结果。

### 1.2 dirty 文件是动态集合，不止任务简报列出的项目

主工作树当前只显示两个未暂存修改：

- `reproduction/audit/operations.jsonl`
- `reproduction/audit/key_results.jsonl`

bwrap 工作树除 `operations.jsonl` 和受保护的 `Meta_logs/` 外，复核期间还出现：

- 未提交 `.gitignore` 修改，新增 `Meta_logs/` ignore；
- 未跟踪 `reproduction/audit/daily/2026-11-16.md`；
- 未跟踪四份 `delivery-*-brief.md` / `delivery-gitlab-codex.md`。

这说明工作树仍有其他交付活动。合并前应暂停并确认各文件 owner，重新读取 status；不能假设本报告的快照一直有效。`.gitignore` 和未跟踪 delivery 文件不会因为 fast-forward 自动成为交付提交的一部分，也不得被顺手 stage。

### 1.3 分支提交不修改主工作树的两本 dirty 账本

`git diff --name-only reproduction/4b-main8..reproduction/4b-bwrap` 共 48 个路径，其中没有：

- `reproduction/audit/operations.jsonl`
- `reproduction/audit/key_results.jsonl`

因此当前两本主工作树 dirty 账本与待合并路径不相交，本身不构成 merge 阻塞。无需 stash，也不能为了“让 Git 干净”而 restore、checkout、clean 或覆盖它们。

主工作树复核快照：

- operations：1151 行，SHA256 `ea9403380b77b5f1100c6bce5b66578cc3cf4e218ec9985bea8d2b7f0749858e`；sequence 1..1269，无重复 sequence；`sequence.state=1269`。
- key_results：101 行，SHA256 `82c10d582e9459d62d03c3b42c6ba9efaabdfd1254114519389259fd1ba92c0a`。

这些 hash 只用于说明复核时的状态；实际 merge 前后必须重新计算，且合并期间应暂停新的审计追加，以便逐字节比较。

### 1.4 两个工作树的 operations 账本已经分叉

- 主账本 1151 行，bwrap 账本 521 行；只有前 519 行完全相同。
- 两边分叉后都从 sequence 581 开始，bwrap 的 581/582 两条记录与主账本中任何完整 JSON 记录都不完全相同。
- bwrap `sequence.state=582`，主 `sequence.state=1269`。
- key_results 情形不同：bwrap 的 47 行是主账本前 47 行的共同前缀，主账本另有后续记录。

因此不能复制一个账本覆盖另一个，也不能直接 `cat` 拼接 operations，否则会重用 sequence 并破坏追加语义。建议以主账本为候选权威账本，原样保留 bwrap 文件及 hash；对 bwrap 分叉的 581/582 逐条做语义去重。确实缺失的历史事件只能在主账本当前最大 sequence 之后追加新记录，并记录原工作树、原 sequence、原时间/evidence；若已有等价事件，则追加对账说明而不是重复导入。不得改写既有行。保留 bwrap 工作树，直到其账本、Meta_logs 和证据路径完成保留核验。

### 1.5 GitLab 当前不是可直接快进推送的关系

- 两个本地分支都没有 upstream。
- 本地远端引用只有 `gitlab/main` 等，未见已配置的本地分支 upstream。
- `gitlab/main` 是 `0c47d98df09327ba09f4fad9aaa9914837ffa280`（Initial commit）。
- `gitlab/main...reproduction/4b-main8` 的左右独有提交数为 1/57，且 `git merge-base` 未找到共同祖先。

所以不能把本地 main8 直接推成 `gitlab/main` 并强推解决。实际推送前必须 fetch、重新确认目标 ref 和远端最新 SHA。默认低风险方案是推送同名新分支 `reproduction/4b-main8`；若目标必须是 GitLab `main`，则需要另行明确审批非快进/无共同祖先历史的处理方案，禁止 force push。

### 1.6 公开服务文档是历史状态，不是当前可用证据

任务说明已明确 HTTP 与 cpolar 停止；但 `web/RESULTS.md:37-42` 仍列 PID `2157843` / `2000586` 和 `https://5340b001.r36.cpolar.top`，`reproduction/PROGRESS.md:53` 还保留更旧的 `4a63b603` 过程页。这些只能视为历史记录。恢复前不得宣称当前公网可访问；恢复后应写入新的真实 URL、PID、工作树和验证证据，不能沿用旧值。

## 2. 不暂存、不提交、不覆盖账本时的 merge 处理

当前状态下推荐直接在原主工作树 fast-forward：

1. 暂停其他交付写入和账本追加，重新读取两个工作树 status；确认 index 没有 staged 文件。
2. 重新计算主 operations/key_results 的行数与 SHA256，并保存到本次合并操作证据中；同时记录 bwrap operations 和 `Meta_logs/` 仍保持未提交、未跟踪状态。
3. 再执行只读前置检查：
   - `git merge-base --is-ancestor reproduction/4b-main8 reproduction/4b-bwrap` 必须为 0；
   - `git diff --name-only reproduction/4b-main8..reproduction/4b-bwrap` 仍不得包含两本主 dirty 账本；
   - 主工作树不得新增会被分支覆盖的未跟踪路径。
4. 在主工作树执行 `git merge --ff-only reproduction/4b-bwrap`。不要使用 `--no-ff`、`--autostash`、`git stash`、`reset --hard`、`clean` 或针对账本的 `checkout/restore`。
5. 合并后立即确认 HEAD 为实时 bwrap tip（当前 `6c2049b`），并重新计算两本主账本 SHA256/行数；应与 merge 前快照完全一致。随后才恢复审计追加，并记录 merge 操作。
6. 单独确认 bwrap 工作树的 operations、`.gitignore`、Meta_logs 和未跟踪交付文件仍存在且未被 stage/删除。分支合并不等于可以删除该工作树或其证据。

如果实际 merge 仍报告 local changes/untracked files would be overwritten，说明状态已相对本复核发生变化。应立即停止并重新做路径级核对，不能改用 stash 或强制 checkout。若新分支版本确实触及受保护账本，则在干净临时 integration worktree/临时分支完成代码合并和测试，同时保持两个原工作树不动；在原主工作树的账本权威副本与分叉处理被明确前，不宣称已经完成“原主工作树合入”。这一步需要用户决定账本归并方式，不能静默缩小交付目标。

## 3. 合并后验收清单

### 3.1 代码与网站离线验收

在合并后的主工作树实际执行，而不是在旧 reviewer/bwrap 工作树：

```bash
cd RSIFlow
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$PWD/runtime:$PWD" \
  /data/wuxuecheng/miniforge3/envs/rsiflow/bin/python -m pytest -q tests
cd ..
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s web/tests -v
node --check web/app.js
git diff --check
```

- 参考结果分别为 `297 passed, 2 skipped`、website `11/11`；必须记录本次真实输出、耗时、HEAD 和证据 hash。若有变化，以新输出审查，不能为保持旧计数修改断言。
- 核对 `313b6bc..合并HEAD` 的生产代码差异；当前复核未见 `RSIFlow/` 后续生产代码变化，因此既有 A100 smoke 可作为其原 commit 的证据，但不能写成在合并 HEAD 重新运行。
- 运行既有 secret/asset/public-root 检查；`assets/isolation/`、凭据、Meta_logs、raw provider 数据、私有轨迹和大日志不得进入公开目录或提交。扫描失败只报告安全路径，不打印秘密内容。
- Ruff 仍有 18 个披露的 legacy findings，不能宣称 Ruff 全量 clean。
- 本合并验收不自动启动五轮、SFT、benchmark、GPU/Qwen 服务或新 SCO 作业。

### 3.2 公网恢复与实测

- 从合并后的主工作树启动静态 HTTP，绑定 `127.0.0.1:18000`；cpolar 只暴露该工作树的 `web/`，原始日志放在公开根之外的 `web-runtime/logs/`。
- 用进程 cwd 和命令行确认 HTTP 服务不是继续指向旧 bwrap/reviewer 工作树。使用持久任务，避免默认 600 秒后再次停止。
- 本地与公网分别验证首页、CSS、JS、两个数据 JSON、REPRODUCTION、RESULTS、README、OPERATIONS 和过程页的实际 HTTP 状态与关键内容；不能只检查进程存在。
- 核对公网 JSON/RESULTS 的 merged HEAD、297/2、11/11、最终 Meta retry `pt-x6b4ista`、combined `pt-v6m10c4t` 整体 FAILED 的表述一致。
- 更新 `web/RESULTS.md`、`web/OPERATIONS.md` 和必要的过程 JSON，写入本次新 URL/PID/工作树/时间。旧 URL 只保留为历史，不作为当前入口。
- 若服务未恢复或公网核验失败，最终状态必须保持 CHANGES，不得写“已交付公网”。

### 3.3 GitLab 验收

- 先 `git fetch gitlab`，再核对远端目标分支当前 SHA、是否 fast-forward、受保护分支规则和实际 merged HEAD；不得依赖本次复核时的本地 remote-tracking ref。
- 未获明确目标决定时，最低风险是显式推送同名分支：
  `git push -u gitlab reproduction/4b-main8:reproduction/4b-main8`。
- 不推送 bwrap dirty 文件、Meta_logs 或其他未提交内容；不使用 `--force` / `--force-with-lease` 覆盖 `gitlab/main`，不擅自建 PR。
- 推送后独立读取远端分支 SHA，确认等于本地 merged HEAD；记录 remote 名称、ref、local/remote SHA 和实际 push 输出。本地 merge 成功不等于 GitLab 已交付。
- 若 GitLab 有 CI，读取实际 pipeline 状态和覆盖范围；没有 CI 证据时明确写“未验证 GitLab CI”。

### 3.4 审计验收

- merge、完整测试、网站检查、HTTP/cpolar 启动、本地/公网请求、fetch/push/远端 SHA 核验都应及时追加 operations；失败、拒绝、超时同样记录。
- key_results 分行记录 merged HEAD、测试计数、公网结果和 GitLab remote SHA，并附真实 evidence path/SHA256；没有输出不得写 `main_verified`。
- merge 前后账本 hash 一致是独立必检项。bwrap operations 两条分叉记录与 Meta_logs 的保留/对账结果也必须有记录。
- 每日记录核对 sequence、证据链接、公开 URL 和远端 SHA；不覆盖旧审计行，不把 sidecar hash 当作原始日志仍存在的替代品。
- 最终 git status 应只含明确保留的 dirty/untracked 文件；本报告和四份 delivery 文档不应被误认为已提交交付物。

## 4. 最终汇报不能夸大的结论

即使 merge、测试、公网和 push 全部通过，也只能宣称“bwrap 隔离分支已合入并交付，列明的离线/真实 smoke 证据成立”，不能扩大为以下结论：

- 不是作者原始 3000/744 队列的精确复现；只是公开数据同协议重构。`clean_independence_audited=false`、EnvScaler 共享环境定义和上游 `failed_overlap` 限制仍保留。
- 尚未运行五轮演化、SFT、最终 694/744 评估，没有任何模型 benchmark 分数；ACE 50 仍 pending，8B 未运行。
- Candidate/EvalPlus/BFCL/Meta 都是最小隔离或评测链路 smoke，不是对应 benchmark 分数。EvalPlus 是历史单任务证据，未在 `313b6bc` 重跑。
- 最终真实 gates 是 A100/root_mapping=false，不是 A800 pass。combined `pt-v6m10c4t` 整体/worker FAILED；只有其中 candidate/Harness/BFCL exit 0，Meta 成功来自单独 retry `pt-x6b4ista`。
- 历史 Task 6 `pt-zj1l9q1t` 是外部 actor `huangsiyuan1` 的 8×A100/root_mapping=true 证据，不属于 main-kimi，也不是当前最终 Meta gate。
- Codex native-shell descendant hygiene 未验证；当前仅覆盖 `shell_tool=false` 的 construction-level hygiene，不得声称 native-shell 通过或为此放宽 seccomp。
- 非 UTF-8 installation_id 的 TypeError/ValueError 诊断 Minor 仍保留；五轮前所需 benchmark preflight、健康四卡 Qwen、当前 694 `--check` 和独立 experiment preflight 不因本次合并自动关闭。
- `Ready to merge Yes` 只代表既有隔离审阅范围；它不等于已 merge、已 push、当前公网在线、GitLab CI 通过或可启动五轮实验。

## 主要证据路径

- `reproduction/PROGRESS.md`
- `web/RESULTS.md`
- `web/OPERATIONS.md`
- `reproduction/audit/reviews/bwrap-sdd-progress.md`
- `reproduction/audit/reviews/bwrap-task7-report.md`
- 两个工作树的 `reproduction/audit/operations.jsonl`、`key_results.jsonl`、`sequence.state`
- 两个工作树的实时 `git status`、branch tips、merge-base 和 remote-tracking refs（本报告记录的是 2026-11-16 复核快照）
