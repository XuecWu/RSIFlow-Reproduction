# dev 独占四卡 A800 访问与资源核验

- 最近核验日期：2026-11-18（初次核验：2026-11-17）
- 当前固定入口：`tcp://1.tcp.vip.cpolar.top:11356`，SSH 用户 `root`；`2.tcp.cpolar.cn:10032` 为过渡地址，旧 `36.tcp.cpolar.top:10935` 为历史地址。
- 入口性质：China VIP 固定 TCP；仍须保持目标机 cpolar daemon 在线。通用操作见 `/data/wuxuecheng/CPOLAR_TCP_SOP.md`。本报告不保存 cpolar authtoken、私钥内容或密码。
- 认证：既有 `/root/.ssh/id_ed25519_gpu4090`，公钥指纹 `SHA256:7bJcV19C79WwlEruxU8zOc5mCABgE1RI5YNFj0R1fkw`，注释 `rmbench-gpu4090-debug`。
- 初次默认 SSH 身份对 root/localadmin 均未通过；使用用户指定密钥后 root 登录成功。没有把第一次认证失败误判为隧道不可用。

## 实测结果

- 主机：`app-91ba3954af664589900a5228c33cf6f9-695d6797bc-2lk76`。
- GPU：4×`NVIDIA A800-SXM4-80GB`，每卡 81920 MiB；核验时 0/1/2/3 各约 1 MiB 占用，`nvidia-smi --query-compute-apps` 无计算进程。
- 内存：约 1.0 TiB，可用约 973 GiB。
- 共享盘：`/data/wuxuecheng` 可见，quarkfs 90T，总可用约 6.4T（93% 已用）。
- preflight worktree 可见，HEAD=`15e45348528aabeeb1c2a8fa7013c6fa7c57d550`。
- Python：`/data/wuxuecheng/miniforge3/envs/rsiflow/bin/python`，Python 3.11.18。
- bwrap：`assets/isolation/bin/bwrap`，SHA256=`d78807229d616606e339c5988392b9e0ab4a6a6998fa51e4590837f426a12fca`，与 pinned 资产一致。
- 端口：目标机没有 `ss`，因此未把该命令错误输出当作空闲证据；改用 Python 对 127.0.0.1:8271–8274 逐一 bind/close，四端口均成功绑定，核验时可用。

## 操作边界

- 本轮仅执行 SSH 认证、只读资源/代码/资产检查和临时端口 bind/close。
- 未安装软件、未修改远端配置、未启动模型、未运行 rollout/SFT、未提交新的 SCO 作业。
- 当前修复后的评分 `pt-bvyk7gi1` 与 S1 `pt-x7apfiza` 已在主分区运行，不在该四卡机器上重复执行。
- 该机器可作为后续有界 preflight 重试或经用户批准后的单套正式四卡流水线候选；正式五轮仍需独立 GO 决策。

## 2026-11-18 当前动态入口更新

- 用户提供的最新入口：`tcp://2.tcp.cpolar.cn:10032`，SSH 用户 `root`；旧 `36.tcp.cpolar.top:10935` 仅保留为历史地址。
- 认证仍使用本报告所列既有 `/root/.ssh/id_ed25519_gpu4090`，未新增或替换私钥。
- 首次连接的新地址 host key 通过 `StrictHostKeyChecking=accept-new` 登记；随后实测主机仍为 `app-91ba3954af664589900a5228c33cf6f9-695d6797bc-2lk76`。
- 实测 4×A800 80GB，每卡约 3085 MiB、49–52% util；occupier identity 仍为 PID=PGID `24825`、start_ticks `2827137154`，精确 torchrun/train.py argv 匹配。
- 本次只执行 SSH 认证、主机/GPU/owner 只读核验，未停止 occupier、未启动模型或五轮。后续启动前仍须重新核验动态入口与实时资源。

## 2026-11-18 固定 China VIP TCP 启用

- 用户提供的固定入口：`tcp://1.tcp.vip.cpolar.top:11356`；后续默认使用该地址，`2.tcp.cpolar.cn:10032` 转为过渡入口，更早地址保留为历史。
- 初次固定地址 SSH 为 `Connection refused`。通过仍可用的过渡入口，在 dev4 使用既有认证配置启动 `cpolar tcp -region=cn_vip -remote-addr=1.tcp.vip.cpolar.top:11356 ... 22`。
- 日志明确记录 `Tunnel established at tcp://1.tcp.vip.cpolar.top:11356`；随后使用既有私钥和 `StrictHostKeyChecking=accept-new` 实测固定地址 SSH 成功，主机与 4×A800 均匹配。
- 固定隧道日志：`/data/wuxuecheng/cpolar-runtime/dev4-fixed-tcp.log`；daemon identity：`/data/wuxuecheng/cpolar-runtime/dev4-fixed-tcp-identity.json`，PID=PGID `25533`、start_ticks `2828734258`。
- cpolar 按既有配置自我 daemon 化，初次 `$!` identity 断言失败；没有据此重复启动，而是核对真实日志/进程后重新绑定唯一 daemon。该事件和通用操作已写入 `/data/wuxuecheng/CPOLAR_TCP_SOP.md`。
- 本次未停止 occupier、未启动模型或五轮；固定地址仅是访问通道证据。
