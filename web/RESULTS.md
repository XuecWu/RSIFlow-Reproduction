# 当前交付与真实结果

## 状态与访问入口

- 源码调研：已完成，上游基线 `496d3cfb9ff39997d107d0ff29e746b07e638180`。
- 当前隔离分支：`reproduction/4b-bwrap`；只进行本地 commit，未 push。
- 当前范围：Qwen3-4B、公开数据同协议重构、Meta `OpenAI/gpt-6-astra`。正式实验规划仍为主分区 8×A800 仅用 GPU 0–3。
- **尚未运行五轮演化、SFT 或最终 694/744 评估，没有任何模型 benchmark 分数。ACE 50、8B 和正式 Qwen 服务也未在本轮启动。**
- **公网首页：** https://4a63b603.r36.cpolar.top
- **过程审计：** https://4a63b603.r36.cpolar.top/process.html
- **本地地址：** http://127.0.0.1:18000/

## 已验证的准备结果

1. 环境与资产：生产/离线 Python 3.11、依赖 lock、Qwen3-4B 13 文件和 3 个权重分片、Codex 0.153.4 provenance 已核验；pinned bubblewrap 0.6.1 安装于 Git 忽略目录。
2. 数据：v4 冻结 3000=5×600，独立验证 744；每轮代码为 120 STDIO + 80 function。它是同协议重构，不是作者原始队列；744 中只计划评分 694，ACE 50 为 pending。
3. 隔离回归：显式 `linux_bwrap_seccomp_v1` 下旧 Landlock Harness 回归已真实 RED→GREEN；最终修复后的完整上游测试 **297 passed, 2 skipped**，综合聚焦回归 **156 passed**，website **11/11**，`node --check` 和 `git diff --check` 通过。生产 fresh installation-ID、same-basename ownership 和全 profile trusted-asset tamper/no-spawn 回归均为 offline 新证据；受影响 SCO rerun 尚待 controller。
4. Candidate：`pt-gyfupguq` SUCCEEDED/worker Succeeded，7/18/5/3 项 functional/negative/timeout/session 检查通过；report SHA256 `56b3fc6a2d5d3cdaf66c84947ed35c607c2db2b883ad9e6c1b3e5c32e00abd63`。
5. EvalPlus：`pt-bfkp5k0d` SUCCEEDED/worker Succeeded，一个 HumanEval+ 任务 canonical Base/Plus pass、wrong Base/Plus fail；smoke SHA256 `537ed60c8ed385ecab81197fb218a6e8117bfc3a0871d5e2445779daf524bfc0`，official canonical/wrong SHA256 `3125b42c1d1f8ef7ba4a6a4ee263ab653485c11a0b8e087327a9987ea8a2ffa2` / `f41bea3178f193a2228b196fe3709fe8f1aa6321302ff04c0ef29a3bf2427514`。**这不是 HumanEval+ 分数。**
6. BFCL：`pt-fdvsxqax` SUCCEEDED/worker Succeeded，pinned source 上完成恰好两次受控 CPU RPC；`check.json` SHA256 `ac659bf1898af26c0b8ef68b3b6fe3dd4beb07fa9931cbab051378056b5e0fe4`。**这不是 BFCL 分数。**
7. Persistent Meta：`pt-zj1l9q1t` SUCCEEDED/worker Succeeded，同 thread、无重复 job/bootstrap、三次 dynamic tools、11 项 key hygiene、7 项 cleanup、durable job exit 0；report SHA256 `b1f1dbf35c09ee192c2199028ee984ba7068889bdf3f126cd52965732220f920`。这是隔离/恢复 smoke，不是模型质量结果。
8. 真实模型诊断：`pt-ox3o5he2` 曾完成四卡 BF16 服务、checkpoint/weights 绑定和短生成，随后停止服务并释放显存；这不是 benchmark 分数，也不表示当前服务仍运行。

## 证据边界与剩余门槛

- Meta 接受证据由外部 actor `huangsiyuan1` 提交，实际为 **8×A100**、N3lS+N2lS candidate specs、`root_mapping=true`，经用户授权接受；不是 `main-kimi` 提交。A800 r5/r6 `pt-7i4qertj` / `pt-44m6zkmx` 在启动前失败，**没有 A800 Meta pass**。
- 真实 `OPENAI_API_KEY` 仅在 host `ResponsesTransport`；sandbox 只有 dummy relay key。公开页不包含 raw prompts/responses、私有轨迹、凭据或大输出。
- Codex native-shell descendant hygiene 未执行；legacy Landlock 不可用且 nested vendor bwrap 与 anti-escape seccomp 冲突。当前仅验证 `shell_tool=false` 的 construction-level hygiene，未放宽 seccomp。
- 最终生产修复的独立 re-review 和受影响 candidate/native/Harness/Meta SCO rerun 均由 controller 执行，当前仍为 pending；本页既有 SCO IDs/hash 只对应修复前已接受证据，作者自审不能替代新验证。
- 五轮前仍需五类 benchmark 最小 smoke、逻辑 GPU 0–3 的健康四卡 Qwen 服务、当前 694 `python RSIFlow_4B/launch.py --check` 和独立 experiment preflight review。

## 独立审阅与公开原则

此前各任务的独立审阅及修复证据保存在 SDD ledger 和 audit operations；Task 7 的最终全分支安全审阅不在本实现任务内自行分派。公开状态只引用脱敏 report 路径、SHA256、job/terminal/resource 证据，不把 smoke 转换成模型分数。

## 运行服务

| 服务 | PID | 地址 | 原始日志（不公开） |
| --- | ---: | --- | --- |
| 静态 HTTP | 2000430 | `127.0.0.1:18000` | `web-runtime/logs/http-server.log` |
| cpolar | 2000586 | `https://4a63b603.r36.cpolar.top` | `web-runtime/logs/cpolar.log` |

cpolar 临时地址可能变化。公开目录仅包含脱敏静态页面；原始操作证据和私有轨迹不放入 web 根目录。
