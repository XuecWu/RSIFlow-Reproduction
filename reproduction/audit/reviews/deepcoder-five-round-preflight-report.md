# DeepCoder 五轮：Task 5 完整离线门与 dev4 只读预检

- 日期：2026-11-18（UTC）
- reviewed/execution HEAD：`88bdc288fee690344d021f787736ee385ac78a6d`
- worktree：`/data/wuxuecheng/Hierarchical_level/RSI-Flow/.worktrees/five-round`
- 结论：**离线门 PASS；dev4 只读资源/API 门 PASS。** 这不是五轮启动许可；真实 MODEL plan 的独立 blocker 见 `deepcoder-model-smoke-report.md`。

## 1. 完整离线门

实际使用 `/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python`，不是系统 Python。

| 命令类别 | 实际结果 | 主审计证据 |
|---|---|---|
| `PYTHONPATH=<runtime>:<RSIFlow> python -m pytest -q tests` | `598 passed, 2 skipped in 154.30s`；skip 未计为 pass，无 xfail | `reproduction/audit/evidence/00001291-stdout.log`，SHA256 `92f0163acada8668477ca089217eb667156c25df32b470fbda243673f2c18e33` |
| `python3 -m unittest discover -s web/tests -v` | 11/11，`OK` | `00001292-stderr.log`，SHA256 `7af176e23b8762db48bd6678d597e50998e0b9cc312e7f4fb19c316284d2fc3c` |
| `node --check web/app.js` | exit 0 | operation `00001293` |
| `node --check web/process.js` | exit 0 | operation `00001294` |
| `deepcoder_five_round_preflight.py --config ...five_round_workspace.json --release ...five-round-16-v1/release --output ...preflight-offline.json` | `status=passed`、`rounds=5`、`real_evolution_started=false` | `00001295-stdout.log`，SHA256 `1e40fc5fd0e044e5cf7f1d59c05e3bb68c9c0aa26535f37fcdf23b6f0149b388` |

离线 receipt：`reproduction/logs/deepcoder-five-round-preflight-offline.json`，SHA256 `b419c6a90f20007b4a9c85ff6cd5b7254ccfa537e64d3d7facce011af79458b7`。其中固定 5×16 slots、16 unique IDs、52 个离线 turns、无独立 validation，API/model/GPU calls 均为 0；release hashes 在写 receipt 前后保持不变。关键结果：`00001296-task5_offline_gate`。

## 2. dev4 只读门

只使用指定入口 `36.tcp.cpolar.top:10935`、root、既有私钥、BatchMode/IdentitiesOnly。未猜测其他入口。

operation `00001297`（stdout SHA256 `7f21decf5d50220723f32a57dc0e0bda9878ea275e28f8d5092a1f98fc05cca9`）实测：

- dev4 HEAD 精确等于 reviewed HEAD，tracked/cached diff 为空；
- source root 是实际目录且根不是 symlink；MODEL output root、五轮 output root 和五轮 run dir 均不存在；
- 四张 `NVIDIA A800-SXM4-80GB`、每张 81920 MiB；当时每卡约 3085 MiB、43–52% util，compute apps 属于已登记 occupier；
- 127.0.0.1:8271–8274 均可临时 bind/close，两个 Meta socket 路径不存在；
- bwrap 0.6.1 binary、manifest、launcher 和 install receipt 全部 hash 相符；binary SHA256 为 pinned `d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`；
- `/data` 可用约 4.6 TiB；
- 五轮配置 `launch.py --check` exit 0，自然派生 run path 正确，rounds=5、16/round、空 skill seed、资产存在。该命令没有创建 run。

固定 provider/model Responses 探针只 source `/data/wuxuecheng/.config/rsiflow/openai.env` 并检查 key 非空；未读取、打印、复制秘密，未使用 `env` 或 `set -x`。OpenAI provider、`http://token.acerobotics.com/v1`、`gpt-6-astra` 实测 HTTP 200、`response.status=completed`、response model 精确匹配。证据：`00001298-stdout.log`，SHA256 `ee2554997dbf36ee8b1237703dce63d3c9201770a446ba59d93f1dabb459db75`。关键结果：`00001304-task5_dev4_readonly_preflight`。

## 3. 边界

- 本报告只证明离线和只读 preflight；没有证明真实 SFT、merge、reload、短生成或 recovery。
- 没有启动五轮、独立 validation、694/744 或任何正式 rollout。
- Task 4 的 M1/M2 仍为 deferred；新发现的真实 source `meta` symlink blocker 必须另行修复和复审。


## 4. Retry 1 新 HEAD 事实（2026-11-18）

当前 reviewed HEAD 为 `e849324c348ca357abd065fc5e8e80b4c9b44238`。Task 4 最后两轮 scoped re-review 已关闭 central `meta` link 及其 output-isolation 阻断；实现者报告该 HEAD 完整 `608 passed, 2 skipped`、scoped `168 passed`，Task 5 retry 未重跑这些测试。

dev4 只读复核（主审计 operation `00001318`）确认 HEAD/clean diff、生产 central meta owner、MODEL/五轮 output absent 和 occupier 原状态。真实 plan 重试随后被 parent 零调用 negative rows 的 source 资格门阻断；详见 `deepcoder-model-smoke-report.md` 的 Retry 1。没有启动 execute 或五轮，上述初始离线/API 证据不被表述为新 HEAD 的真实 MODEL pass。
