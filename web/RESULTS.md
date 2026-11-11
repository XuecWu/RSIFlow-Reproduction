# 本次交付结果

## 状态与访问入口

- 源码调研：已完成，基线 commit `496d3cfb9ff39997d107d0ff29e746b07e638180`。
- 复现手册：已完成，全部为后续建议，未执行。
- 网站实现与发布：已完成，静态页面可通过公网访问。
- 实验复现：本次未运行实验，无模型输出、训练轮次、SCO job 或 benchmark 分数。

**当前公网地址：** https://4a63b603.r36.cpolar.top

**本地地址：** http://127.0.0.1:18000/

cpolar 临时地址在隧道重启后可能变化，应以当前隧道日志和本文件最新记录为准。原始运行日志与 PID 位于公开根目录之外的 `web-runtime/logs/`。

## 已生成内容

- 根级 `AGENTS.md`：架构、有效配置、数据协议、GPU/SCO、资产、恢复与维护规则。
- `web/REPRODUCTION.md`：Conda 到最终评估的分阶段复现手册。
- `web/ENVIRONMENT.md`：本机观察和集群资源建议。
- `web/ASSETS.md`：模型、数据、评测器、Codex 与原始 manifest 缺口。
- `web/index.html`、`styles.css`、`app.js`：中文响应式静态展示。
- `web/data/repository.json`、`reproduction.json`：源码事实和未执行阶段状态。
- `web/README.md`：测试、启动、发布、停止与更新说明。
- `web/OPERATIONS.md`：下载、调研、决策和验证日志。

## 关键调研结论

1. 4B/8B 共用控制和运行时，协议为 5×600=3000，最终独立评估 744。
2. 有效 SFT 是 LoRA rank 8、LR 2e-6，不是 base JSON 表面的 rank 16、LR 2e-5。
3. 推理要求四卡各至少 20,000 MiB 空闲，SFT 前各至少 40,000 MiB。
4. 原始 source manifests 是精确复现的关键缺口，不能用自行抽样替代。
5. 默认子进程会重写 CUDA_VISIBLE_DEVICES，主分区八卡双四卡并行需要先改映射和隔离并测试。
6. `META_SKILLS.md` 中的 `--meta-skill-seed` 与代码不一致，当前实际参数为 `--skill-seed`。

## 验证记录

- TDD RED：首次运行 9 项网站测试，5 项因页面、repository JSON、README/RESULTS 和主分区规格说明尚未实现而失败。
- 初版 GREEN：同一测试命令 9/9 通过；`node --check web/app.js` 通过。
- 独立复核：六项源码事实均核对无误，但发现后台任务超时停止服务和原始日志公开两个 Important 问题。
- 服务根因：两个后台任务均在 600 秒达到工具默认 timeout；本地连接拒绝、旧公网地址 404。改为 `disable_timeout=true` 的持久后台任务。
- 公开面修复：原始日志和 PID 从 `web/logs/` 移到 `web-runtime/logs/`；新增测试拒绝公开原始日志和 cpolar client ID。
- 最终测试：`python3 -m unittest discover -s web/tests -v`，10/10 通过；`node --check web/app.js` 再次通过。
- 本地 HTTP：首页、CSS、JS、两个 JSON、REPRODUCTION、RESULTS 共 7 个资源均返回 200。
- 公网 HTTP：当前 HTTPS 地址的相同 7 个资源均返回 200；首页包含 RSI-Flow、本次未运行实验、3000/744 和集群规格等关键内容。
- cpolar 配置差异：本地 web interface 实际为 4048，API 返回空响应；公网 URL 来自隧道建立日志，公网可用性由真实请求确认。
- 实验边界：未创建 Conda 环境、未下载实验资产、未运行上游 pytest/--check、未提交 SCO、未调用 GPU/API。

## 运行进程

| 服务 | PID | 地址 | 原始日志（不公开） |
| --- | ---: | --- | --- |
| 静态 HTTP | 2000430 | `127.0.0.1:18000` | `web-runtime/logs/http-server.log` |
| cpolar | 2000586 | `https://4a63b603.r36.cpolar.top` | `web-runtime/logs/cpolar.log` |

两个服务均以无超时后台任务运行。停止前应按 `web/README.md` 先核对 PID 对应的命令行，避免影响其他进程。

## 独立复核遗留的 Minor

- `ASSETS.md` 已列出所需资产类型、数量、Codex/EnvScaler 版本和 provenance 字段，但多数模型/数据集/评测器仍缺权威 URL、具体 revision、许可证和建议路径。该补充属于后续资产获取调研，本次未下载、未核验，不阻断当前源码展示。

## 后续待决策

- 原始 3000/744 source manifests 是否可提供，或是否接受同协议重构。
- 使用 dev 四卡顺序方案，还是先为主分区八卡并行开发隔离支持。
- Meta/ACE API 的安全注入与预算。
- 长任务、输出存储和实际运行审批。
