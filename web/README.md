# RSI-Flow 调研展示站

本目录是 cpolar 的唯一公开根目录。网站为静态 HTML/CSS/JavaScript，无 npm、CDN、数据库或实验环境依赖。

## 文件职责

- `index.html`：核心中文展示，禁用 JavaScript 也可阅读。
- `styles.css`：响应式布局、键盘焦点和 reduced-motion 支持。
- `app.js`：导航当前位置与命令复制，不加载动态实验结果。
- `process.html`、`process.js`：操作时间线、关键结果和真实 run 快照。
- `data/repository.json`：commit、模块、协议、GPU 和有效 SFT 等源码事实。
- `data/reproduction.json`：实验阶段状态；当前固定 16 条单域五轮已完成，正式五域和独立评估仍未运行。
- `data/process.json`：由主 checkout 审计账本生成的脱敏静态快照，更新后需重新生成。
- `REPRODUCTION.md`：完整复现命令、已接受证据与停止条件。
- `ENVIRONMENT.md`：本机观察和 SCO 资源建议。
- `ASSETS.md`：外部资产、许可证/hash 要求与缺口。
- `RESULTS.md`：实际交付和验证结论。
- `OPERATIONS.md`：追加式操作 ledger，包含失败与决策。
- `tests/test_web.py`：事实、结构、链接、状态、五轮快照和凭据安全测试。
- 仓库学习专题位于 `reproduction/STUDY_GUIDE.md`，不作为静态站的单独公开文件。
- 原始 HTTP/cpolar 日志与 PID 位于公开根目录外的 `web-runtime/logs/`。

## 验证

从仓库根目录运行：

```bash
python3 -m unittest discover -s web/tests -v
node --check web/app.js
```

测试只覆盖本次网站，不代表上游 pytest 或任何模型实验已运行。

## 本地启动

```bash
cd /data/wuxuecheng/Hierarchical_level/RSI-Flow
python3 -m http.server 18000 --bind 127.0.0.1 --directory web
```

服务只监听回环地址。当前公网入口不再依赖随机 cpolar 地址，而是通过共享 webshow 站点发布。

## webshow 发布

共享固定入口为 `https://webshow.vip.cpolar.top`，其既有静态服务监听 `127.0.0.1:9900`。本次通过 `/data/wuxuecheng/ace-wam/webshow/rsiflow` 指向主工作树 `web/` 的挂载发布，公开路径为 `https://webshow.vip.cpolar.top/rsiflow/`。

新增挂载前必须确认目标不存在且来源确为本仓库 `web/`；不要覆盖已有链接或目录，不要重启、停止或修改共享静态服务及其 cpolar 隧道。挂载后用真实公网请求验收首页、过程页、JS、CSS 和 JSON。不要把 authtoken、原始隧道日志或 client metadata 放入公开根目录。

## 停止与重启

只停止本任务拥有的本地 HTTP 服务。先核对 PID 文件对应的命令行确实是本网站的 `http.server 18000`，再停止：

```bash
kill "$(cat web-runtime/logs/http-server.pid)"
```

重启后重新记录 PID、日志和本地验证结果。共享 webshow 的静态服务与 cpolar 隧道不属于本网站专有进程，不使用这里的 PID 文件或模糊进程匹配停止它们。当前服务归属与历史入口见 `RESULTS.md`。

## 内容更新规则

1. 修改源码事实时同时更新 `repository.json`、首页和 `AGENTS.md`。
2. 只有取得真实证据后，才修改 `reproduction.json` 的阶段状态和 evidence。
3. 区分 `not_run_pending_approval`、运行中、完成、失败和阻断，不能预填成功。
4. 更新后运行完整网站测试、JS 语法检查、本地与公网 HTTP 验证。
5. 公开前检查所有文本和日志，不加入凭据、私有轨迹、原始数据集或 checkpoint。
6. 重要操作追加到 `OPERATIONS.md`，最终结论同步到 `RESULTS.md`。
7. 审计账本或 `runs.json` 更新后，用 `reproduction/scripts/build_status.py` 从主 checkout 审计根重建 `data/process.json`，并运行五轮快照契约测试。
