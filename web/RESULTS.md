# 当前交付与真实结果

## 状态与访问入口

- 源码调研：已完成，上游调研基线 `496d3cfb9ff39997d107d0ff29e746b07e638180`（GitHub 上游 `main`）。
- **已合并、已备份**：隔离分支 `reproduction/4b-bwrap` 已 fast-forward 合入 `reproduction/4b-main8`。**合并与初次备份点为 `3a66b655b764224acb21a108e59e7066f8503867`**（该点的复盘/审查提交为 `3a66b65`），推送当时 `ls-remote` 两个远端复现分支同为该 SHA。承载本次发布文档更新的提交在该点**之后**由主 agent 纳入后续文档提交，因此**当前分支 HEAD 与远端 SHA 一律以 `git log` / `git ls-remote` 实测为准**，本文不写死后续 SHA。**当前不再是"只本地 commit、未 push"状态。**
- 远程与备份边界：`origin` 仍是 GitHub 上游且未向其推送；`gitlab` 指向内部项目 https://gitlab.acerobotics.com/wuxuecheng/rsiflow 。GitLab 既有 `main` 仍为 `0c47d98df09327ba09f4fad9aaa9914837ffa280`，与本地历史无共同提交，**未强推、未覆盖、未建 MR**；GitLab CI 未验证。
- 当前范围：Qwen3-4B、公开数据同协议重构、Meta `OpenAI/gpt-6-astra`。正式实验规划仍为主分区 8×A800 仅用 GPU 0–3。
- **尚未运行五轮演化、SFT 或最终 694/744 评估，没有任何模型 benchmark 分数。ACE 50、8B 和正式 Qwen 服务也未在本轮启动。**
- **公网首页：** https://7b96aab1.r61.cpolar.top
- **过程审计：** https://7b96aab1.r61.cpolar.top/process.html
- **本地地址：** http://127.0.0.1:18000/ （由主工作树 `web/` 提供）
- 交付复盘：`reproduction/RETROSPECTIVE.md`（历程、ruling、真实证据、失败根因、经验与五轮前置门；已通过独立复审）。

## 已验证的准备结果

1. 环境与资产：生产/离线 Python 3.11、依赖 lock、Qwen3-4B 13 文件和 3 个权重分片、Codex 0.153.4 provenance 已核验；pinned bubblewrap 0.6.1 安装于 Git 忽略目录。
2. 数据：v4 冻结 3000=5×600，独立验证 744；每轮代码为 120 STDIO + 80 function。它是同协议重构，不是作者原始队列；744 中只计划评分 694，ACE 50 为 pending。
3. 隔离回归：显式 `linux_bwrap_seccomp_v1` 下旧 Landlock Harness 回归已真实 RED→GREEN；commit `313b6bc` 的完整上游测试 **297 passed, 2 skipped**，综合聚焦回归 **156 passed**，website **11/11**，`node --check` 和 `git diff --check` 通过。生产 fresh installation-ID、same-basename ownership 和全 profile trusted-asset tamper/no-spawn 均有 offline 与下述最终 A100 证据。合并后主工作树的独立复验（含一次真实失败与恢复）见「合并、备份与本次复验」节。
4. Candidate：`pt-gyfupguq` SUCCEEDED/worker Succeeded，7/18/5/3 项 functional/negative/timeout/session 检查通过；report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
5. EvalPlus：`pt-bfkp5k0d` SUCCEEDED/worker Succeeded，一个 HumanEval+ 任务 canonical Base/Plus pass、wrong Base/Plus fail；smoke SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`，official canonical/wrong SHA256 `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2` / `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514`。**这不是 HumanEval+ 分数。**
6. BFCL：`pt-fdvsxqax` SUCCEEDED/worker Succeeded，pinned source 上完成恰好两次受控 CPU RPC；`check.json` SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。**这不是 BFCL 分数。**
7. Persistent Meta：`pt-zj1l9q1t` SUCCEEDED/worker Succeeded，同 thread、无重复 job/bootstrap、三次 dynamic tools、11 项 key hygiene、7 项 cleanup、durable job exit 0；report SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。这是隔离/恢复 smoke，不是模型质量结果。
8. 真实模型诊断：`pt-ox3o5he2` 曾完成四卡 BF16 服务、checkpoint/weights 绑定和短生成，随后停止服务并释放显存；这不是 benchmark 分数，也不表示当前服务仍运行。
9. 最终 A100 gates（`313b6bc`、root_mapping=false）：combined `pt-v6m10c4t` 整体/worker **FAILED** 仅因 Meta 外部 provider completion failure；candidate/Harness/BFCL exit 均 0。candidate SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`，Harness `1 passed in 23.26s`（log SHA256 `808285dc5c05709785e4d669e28882e5c496644bb1191a6301866325b2735f31`），BFCL 两次 RPC 且 key hygiene 通过（receipt/key SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4` / `fbe9b6386dd64f78e5eb72b7bbad9f88f2c39e218b93bd6ab049a0b97b20749d`）。combined Meta ordinal 0 HTTP 502、retry HTTP 200 incomplete SSE，failed report `4f123a27dc415e2b99d63569fee30af2fce68bf3fda68da3653ee7e9e1db3d9c`。唯一 Meta retry `pt-x6b4ista` SUCCEEDED/worker Succeeded/exit 0，report `a177657dbf1e744f2d2be64d16c33bcda08102052a7ecd7486aebdd48cf64b66`，全部恢复/key/cleanup 检查通过，installation_id=0644 且 run-owner 精确。EvalPlus 保留历史 one-task 证据，未在本 commit 重跑。

## 合并、备份与本次复验（2026-11-16）

1. **合并前预检（功能工作树）**：完整 Python `297 passed, 2 skipped in 92.22s`；website `11/11`；`node --check web/app.js` 与 `git diff --check` 通过。生产代码相对最终审阅 HEAD 未变，新增改动仅复盘/审计文档与 `Meta_logs/` ignore 规则。
2. **账本保护**：主工作树两份未提交追加 JSONL 在 merge 前后 SHA256 **完全不变**——`operations.jsonl` = `ea9403380b77b5f1100c6bce5b66578cc3cf4e218ec9985bea8d2b7f0749858e`，`key_results.jsonl` = `82c10d582e9459d62d03c3b42c6ba9efaabdfd1254114519389259fd1ba92c0a`。两个工作树的 `operations.jsonl` 在 **sequence 581 之后分叉**（同号不同义），两份原件均保留，**禁止覆盖或直接拼接**；bwrap 功能工作树与 `Meta_logs/` 一并保留，未 reset/clean/删除。
3. **合并后首次完整 pytest 失败（如实记录）**：主工作树 `13 failed, 284 passed, 2 skipped`。根因是主工作树缺少 Git-ignored 的 `assets/isolation/`（pinned bwrap 与 installer receipt 不随 git 分发），**不是代码或测试回归**。
4. **恢复方式（未改代码/测试）**：使用既有 `reproduction/scripts/install_bwrap.py`，从功能工作树的**同一 pinned binary** 安装到主工作树 `assets/isolation/`；binary SHA256 `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`，receipt 中 manifest/launcher 路径与哈希均匹配（`RSIFlow/configs/bwrap_isolation.json`、`RSIFlow/runtime/rsiflow/task_meta/bwrap_launcher.py`）。
5. **恢复后复验**：受影响测试 `51 passed in 48.77s`；随后完整 pytest **`297 passed, 2 skipped in 89.15s`**；website **11/11**、`node --check`、`git diff --check` 全部通过。因此 297/2 这一计数在**功能工作树预检**与**合并后主工作树**两处分别独立取得。
6. **GitLab 备份**：初次备份新建远端同名分支 `reproduction/4b-main8` 与 `reproduction/4b-bwrap`，**推送当时** `ls-remote` 两者均为 `3a66b655b764224acb21a108e59e7066f8503867`（即合并与初次备份点）；既有 `main` 保持 `0c47d98df09327ba09f4fad9aaa9914837ffa280` 不变。其后的发布文档提交与再次推送会推进两端 SHA，**当前远端值以 `git ls-remote` 实测为准**。推送前由 Codex 对 HEAD 及可达历史做体积与密钥形态扫描（无 >50 MiB blob、无已跟踪 symlink、常见私钥/token 形态未命中），报告 `reproduction/audit/reviews/delivery-gitlab-codex.md`。**未向 GitHub `origin` 推送，未强推，未建 MR，GitLab CI 未验证。**
7. **公网恢复与实测**：HTTP 与 cpolar 从主工作树 `web/` 重新拉起（见下方运行服务表）。公网 `/`、`styles.css`、`app.js`、`data/repository.json`、`data/reproduction.json`、`REPRODUCTION.md`、`RESULTS.md`、`README.md`、`OPERATIONS.md`、`process.html` **共 10 个资源全部 200**；UTF-8 JSON 与 `RESULTS.md` 关键事实检查通过。旧 URL/PID 仅作历史记录。
8. **多 agent 复盘与交叉审阅**：`reproduction/RETROSPECTIVE.md` 由 Qwen4-Max 撰写；独立 Kimi 首轮为 Spec FAIL / CHANGES（3 项 Important + 1 项 Minor：旧 URL/PID 当前性、operations 分叉误写为子集、完整 pytest 的 commit 归属、docs-only 提交列序），Qwen 仅改复盘文档完成修复，Kimi scoped re-review 四项全部 ADDRESSED、Spec/质量 PASS。Codex 的 `Meta_logs/` ignore 修复亦经 Kimi scoped re-review PASS。
9. **边界不变**：本次未启动新 SCO 作业、五轮演化、SFT、最终 694/744 评估或模型服务；所有既有证据口径与剩余门槛保持不变。

## 证据边界与剩余门槛

- 历史 Task 6 Meta 接受证据由外部 actor `huangsiyuan1` 提交，实际为 **8×A100**、N3lS+N2lS candidate specs、`root_mapping=true`，经用户授权接受；不是 `main-kimi` 提交。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 在启动前失败，**没有 A800 Meta pass**。当前最终 Meta gate 仅指上述 `pt-x6b4ista`。
- 真实 `OPENAI_API_KEY` 仅在 host `ResponsesTransport`；sandbox 只有 dummy relay key。公开页不包含 raw prompts/responses、私有轨迹、凭据或大输出。
- Codex native-shell descendant hygiene 未执行；legacy Landlock 不可用且 nested vendor bwrap 与 anti-escape seccomp 冲突。当前仅验证 `shell_tool=false` 的 construction-level hygiene，未放宽 seccomp。
- 最终独立 re-review 已完成：三项生产修复与两项发布修复均为 ADDRESSED，无新增 Critical/Important，Ready to merge Yes。combined `pt-v6m10c4t` 仍不得写成 SUCCEEDED。
- 五轮前仍需五类 benchmark 最小 smoke、逻辑 GPU 0–3 的健康四卡 Qwen 服务、当前 694 `python RSIFlow_4B/launch.py --check` 和独立 experiment preflight review。

## 独立审阅与公开原则

此前各任务的独立审阅及修复证据归档于 `../reproduction/audit/reviews/bwrap-sdd-progress.md`、`bwrap-task6-report.md`、`bwrap-task7-report.md` 和 audit operations；Task 7 的最终全分支安全审阅由未参与实现的 reviewer 执行。本次交付阶段的复盘与交叉审阅另见 `../reproduction/RETROSPECTIVE.md`、`../reproduction/audit/reviews/delivery-integration-kimi.md`、`delivery-gitlab-codex.md` 与当日审计 `../reproduction/audit/daily/2026-11-16.md`。公开状态只引用脱敏 report 路径、SHA256、job/terminal/resource 证据，不把 smoke 转换成模型分数。

## 运行服务

当前服务（2026-11-16 恢复，源目录为主工作树 `web/`）：

| 服务 | 后台任务 | PID | 地址 | 原始日志（不公开） |
| --- | --- | ---: | --- | --- |
| 静态 HTTP | `bash-8s79760l` | 3697240 | `127.0.0.1:18000` | `web-runtime/logs/http-server.log` |
| cpolar | `bash-be8pblb5` | 3697621 | `https://7b96aab1.r61.cpolar.top` | `web-runtime/logs/cpolar.log` |

历史服务（**已停止，仅留档，不可作为当前入口**）：

| 服务 | 历史任务 | 历史 PID | 历史地址 | 状态 |
| --- | --- | ---: | --- | --- |
| 静态 HTTP | `bash-38l47xmz` | 2157843 | `127.0.0.1:18000`（源为最终审阅工作树） | 会话关闭后终止 |
| cpolar | `bash-09cysqyj` | 2000586 | `https://5340b001.r36.cpolar.top` | 会话关闭后终止，旧 URL 返回 404 |

更早的 `https://4a63b603.r36.cpolar.top`、`https://49a63412.r36.cpolar.top` 及对应 PID 同样只是历史入口。cpolar 临时地址会变化，进程重启后必须重新读取实际 URL 并重新实测，不能沿用旧地址。公开目录仅包含脱敏静态页面；原始操作证据和私有轨迹不放入 web 根目录。
