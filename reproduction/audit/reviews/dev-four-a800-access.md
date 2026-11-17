# dev 独占四卡 A800 访问与资源核验

- 核验日期：2026-11-17
- 当前入口：`tcp://36.tcp.cpolar.top:10935`，SSH 用户 `root`
- 入口性质：cpolar 动态 TCP 地址，可能过期；过期后需要用户提供新地址。本报告不保存 cpolar authtoken、私钥内容或密码。
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
