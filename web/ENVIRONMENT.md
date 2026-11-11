# 环境调研记录

## 当前状态

本次只进行只读环境观察和网站验证，没有创建 Conda 环境、安装实验依赖或提交 SCO 作业。

## 本机观察（2026-11-11）

| 项目 | 观察结果 |
| --- | --- |
| OS | Linux |
| Conda | 26.7.3，base `/data/wuxuecheng/miniforge3` |
| 系统 Python | 3.13.13；项目后续建议单独使用 Python 3.11 |
| Node / npm | 16.20.2 / 8.19.4，仅用于网站 JS 语法检查 |
| cpolar | 3.3.18 |
| SCO / ACP | 2.0.1 / 2.1.11，区域入口 `/root/.sco/bin/cnsh01/sco` |
| 内存 | 总量约 1.0 TiB；观察时约 893 GiB available |
| 共享盘 | `/data` 约 90 TiB，总可用约 3.6 TiB，已用约 97% |
| NVIDIA 设备节点 | 可见 8 个 `/dev/nvidia0`–`/dev/nvidia7`，但这不是 GPU 可用证明 |
| NVML | `nvidia-smi` 返回 `Failed to initialize NVML: Unknown Error` |
| CUDA compiler | `nvcc` 不在本机 PATH |

结论：本机不能用于当前四卡实验；后续按用户要求使用 SCO。设备节点存在但没有被解释为“本机有八张可用 GPU”。未进行驱动修改、重启或管理员操作。

## 源码的计算门槛

- Linux，Python 3.11+。
- 四个逻辑 CUDA GPU 0–3，BF16 inference，无 CPU fallback。
- 每个推理服务启动前对应 GPU 至少 20,000 MiB 空闲。
- MODEL SFT 前四卡各至少 40,000 MiB 空闲，之后运行四个 DDP ranks。
- 默认 launcher ports 都是 8271/8272/8273/8274，按顺序对应 GPU 0/1/2/3。
- 子进程会设置自己的 `CUDA_VISIBLE_DEVICES`，不能假设继承父进程物理卡映射。

## 后续 SCO 目标环境

### 低风险建议：dev 四卡顺序运行

| 字段 | 值 |
| --- | --- |
| Workspace | `sensebot-a100-a800-liujianbo` |
| AEC2 | `sensebot-a800-dev-liujianbo` |
| Worker spec | `N3lS.Ii.I60.4` |
| GPU / CPU / 内存 | 4×A800-80G-SXM4 / 48 vCPU / 480 GiB |
| 运行方式 | 4B、8B 顺序，各自独立 ACP job 和 run dir |
| 优点 | 不改源码，GPU/端口/registry 隔离简单 |
| 代价 | 总 wall time 较长，dev 可用性需实际 probe |

### 条件性方案：主分区八卡双流水线

主分区 `sensebot-a100-a800-liujianbo` 可提供 8×A800 80GB 单节点，例如 `N3lS.Ii.I60.8`（96 vCPU、960 GiB）。用户允许后续在主分区提交任务，但本次不提交。

直接在八卡容器运行两套默认代码并不安全：默认 ports、socket 和 output registry 会冲突，子进程还会重写 `CUDA_VISIBLE_DEVICES`。采用该方案前必须：

1. 实现并测试物理 GPU 0–3 与 4–7 的可配置映射。
2. 为 4B/8B 配置不同 ports、meta sockets、output_root 和 run dirs。
3. 隔离 four_gpu services registry、resource leases 和日志。
4. 验证双四卡 DDP、checkpoint binding、停止/恢复和异常清理。
5. 先提交短时 probe，再决定是否运行完整实验。

因此主分区八卡是后续开发/验证方案，不是本次已验证结论。

## 软件版本建议

- Python 3.11 Conda 环境。
- PyTorch 2.8.0+cu128：来自 SCO 文档中已有 A800 环境的验证记录，本项目尚未验证。
- `runtime[dev,pipeline]`：pytest/ruff/httpx + Transformers/PEFT/Accelerate。
- Task requirements：OpenAI、Hugging Face、sentence-transformers、faiss、评测辅助包等。
- Codex：`rust-v0.153.4`，具体 commit/hash 见 `ASSETS.md`。
- 评测器依赖可能冲突，建议使用独立 evaluation 环境。

## 网站运行环境

网站不需要 Conda、Node 包或实验依赖。使用系统 Python 标准库和 cpolar 即可；Node 仅执行 `node --check web/app.js`。公开服务根目录只能是 `web/`。
