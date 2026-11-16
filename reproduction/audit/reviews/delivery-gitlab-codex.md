CHANGES

# GitLab 备份只读审查（2026-11-16 UTC）

## 范围与结论

审查对象为 `reproduction/4b-bwrap` 的 `6c2049b002ad6c4214af34e8ecfb0331cb03adeb`，目标 `git@gitlab.acerobotics.com:wuxuecheng/rsiflow.git`。这是有条件的阻塞结论：`.gitignore` 未整体忽略 `Meta_logs/`，实际存在未跟踪的运行目录，普通 `git add` 可能意外收录；模型和 assets 也只按部分目录及扩展名覆盖。推送固定 HEAD 本身不会带入这些未跟踪文件或未提交的修改，但后续暂存前应明确处理忽略规则并检查暂存内容。由于任务禁止修改其他文件，本审查未修复。

本地 `main` 和 merge-base 均为 `496d3cfb9ff39997d107d0ff29e746b07e638180`；`main..HEAD` 有 58 个提交。现有唯一 remote 是 `origin`，指向 `https://github.com/1HUAN1/RSI-Flow.git`，未配置目标 GitLab。没有访问 GitLab，无法确认目标现有 refs、实际需要传输的对象、访问权限或服务端策略；新仓库推送该 HEAD 可携带其全部可达历史，而非仅 58 个提交。

## 发现

- HEAD tree 1,471 个文件，最大为 `assets/report-specs-v4/hotpotqa_dev.json`（684,938 bytes）；无超过 50 MiB 文件，无已跟踪 symlink。未看到大型模型或 checkpoint；两个已跟踪 report-spec JSON 为评测规格数据，应按备份目的确认其适合内部仓库。
- HEAD 全部常规 blob 进行了私钥头、常见 OpenAI/GitHub/GitLab/AWS token 形态和长凭据赋值的模式扫描，未命中；这不是所有凭据形式的保证。未输出 secret 内容，未读取认证文件。
- 对 HEAD 可达历史按 `git rev-list --objects HEAD` 的对象名去重扫描了 2,088 个 blob：没有超过 50 MiB 的 blob，私钥头和常见 token 形态无命中。历史扫描未检查所有可能的秘密格式，也没有逐个提交路径或人工审阅全部历史。按禁止读取认证文件的要求，跳过历史路径 `RSIFlow_8B_harness/upstream/HarnessForge_4B/.env.example`，因此不能对该历史文件的内容作结论；该路径不在 HEAD。历史不属于 HEAD 文件扫描的完整保证。
- `.gitignore` 覆盖 `Rollout_logs/`、常见缓存/日志/环境、特定 `assets/models/`、`assets/raw/`、`assets/isolation/`、checkpoint 目录和多种模型扩展名；`assets/`、根目录 `models/`、`Meta_logs/` 不是整体忽略。`git check-ignore` 对这些具体样本未匹配。`Meta_logs/` 中部分日志及 sqlite 因通用规则被忽略，但其他文件未被整体屏蔽。
- `Meta_logs/` 在状态中为未跟踪，HEAD 没有该目录；显式推送此 HEAD 不会携带它。`reproduction/audit/operations.jsonl` 已被跟踪，HEAD 中的已提交版本（blob `30b0dc10235b5d498cf161a4c5dc19545fba6193`）会随 HEAD 及历史推送；工作树中未提交的修改不会随该推送携带。其他未跟踪 review briefs 和本报告也不会随固定 HEAD 推送。

## 实际执行的检查

- `cat AGENTS.md`；`git status --short --branch --untracked-files=normal`；`git remote -v`；`git branch -vv`；`git log --oneline --decorate --max-count=25`。
- `git merge-base HEAD main`；`git log --oneline main..HEAD`；`git rev-list --count main..HEAD`；`git ls-tree -r HEAD | awk '$1 == "120000" {print}'`。
- `cat .gitignore`；`git ls-tree -r --name-only HEAD -- Meta_logs reproduction/operations.jsonl`；`git status --short --ignored -- Meta_logs reproduction/operations.jsonl`；`git check-ignore -v` 检查上述运行、缓存和 assets 样本。
- Python 通过 `git ls-tree -r -l -z HEAD` 和 `git cat-file --batch` 检查 HEAD 文件大小及上列秘密模式；通过 `git rev-list --objects HEAD` 和 `git cat-file --batch` 对历史对象执行大小及较窄秘密模式扫描，按名称跳过认证文件。`git ls-tree -r -l HEAD -- reproduction/audit/operations.jsonl` 核实已提交 blob。

## 推送前后核验清单

1. 固定 `git rev-parse HEAD` 和目标 ref，确认仍为本审查 commit；复核 `git status --short`，不得把本地日志、操作日志修改或未跟踪文件误暂存。对未来提交补充 `Meta_logs/` 和预期资产路径忽略策略，并审查 `git diff --cached --name-status`；改变 HEAD 后须重新审查。
2. 确认 GitLab 目标 URL、已有分支和保护策略；必要时用只读 `git ls-remote` 核验。先对可达历史进行适合组织策略的独立 secret 扫描，特别注意上文未读取的历史认证样例及模式扫描盲区。
3. 明确 refspec，将指定 commit 推送到约定分支；避免 `--all`、`--tags`、强推和顺带提交本地更改。推送权限由主 agent 按既有授权处理。
4. 推送后用 `git ls-remote <remote> refs/heads/<branch>` 核对远端 SHA 为指定 HEAD；检查 GitLab 分支与默认可见性、仓库权限和服务端结果。必要时使用隔离克隆检查已提交树；核实远端不含未跟踪 `Meta_logs/` 和工作树未提交改动，但包含已提交的 operations 版本。

未进行远程写入、commit、PR、SCO、删除、清理或认证文件读取。本报告为唯一写入文件。
