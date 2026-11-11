# RSI-Flow 调研展示站

本目录是 cpolar 的唯一公开根目录。网站为静态 HTML/CSS/JavaScript，无 npm、CDN、数据库或实验环境依赖。

## 文件职责

- `index.html`：核心中文展示，禁用 JavaScript 也可阅读。
- `styles.css`：响应式布局、键盘焦点和 reduced-motion 支持。
- `app.js`：导航当前位置与命令复制，不加载动态实验结果。
- `data/repository.json`：commit、模块、协议、GPU 和有效 SFT 等源码事实。
- `data/reproduction.json`：后续实验阶段状态；当前全部为 `not_run_pending_approval`。
- `REPRODUCTION.md`：后续完整复现命令与停止条件，命令未执行。
- `ENVIRONMENT.md`：本机观察和 SCO 资源建议。
- `ASSETS.md`：外部资产、许可证/hash 要求与缺口。
- `RESULTS.md`：本次实际交付和验证结论。
- `OPERATIONS.md`：追加式操作 ledger，包含失败与决策。
- `tests/test_web.py`：事实、结构、链接、状态和凭据安全测试。
- `logs/`：本地 HTTP/cpolar 运行日志与 PID，不包含认证配置。

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
python3 -m http.server 8000 --bind 127.0.0.1 --directory web
```

若 8000 被占用，使用 18000，并同步替换下方命令。服务只监听回环地址，公网入口由 cpolar 提供。

## cpolar 发布

```bash
cpolar http 8000 -log=stdout -log-level=INFO
```

通常从 `http://127.0.0.1:4040/api/tunnels` 获取 HTTPS `public_url`。本机既有配置可能改用其他 inspect 端口：本次实际 web interface 为 `127.0.0.1:4048`，其 API 返回空响应，因此以 `web-runtime/logs/cpolar.log` 中 `Tunnel established at https://...` 为准，并用真实公网请求验收。不要把 authtoken 写入命令、文件或网页。临时公网地址可能在隧道重启后变化，应以当前隧道日志和 `RESULTS.md` 为准。

## 停止与重启

先核对 PID 文件对应的命令行确实是本网站的 `http.server` 或 `cpolar http <port>`，再停止：

```bash
kill "$(cat web-runtime/logs/http-server.pid)"
kill "$(cat web-runtime/logs/cpolar.pid)"
```

重启时使用上面的启动命令，并重新记录 PID、日志和公网 URL。不要使用模糊进程匹配，以免影响其他服务。

## 内容更新规则

1. 修改源码事实时同时更新 `repository.json`、首页和 `AGENTS.md`。
2. 只有取得真实证据后，才修改 `reproduction.json` 的阶段状态和 evidence。
3. 区分 `not_run_pending_approval`、运行中、完成、失败和阻断，不能预填成功。
4. 更新后运行完整网站测试、JS 语法检查、本地与公网 HTTP 验证。
5. 公开前检查所有文本和日志，不加入凭据、私有轨迹、原始数据集或 checkpoint。
6. 重要操作追加到 `OPERATIONS.md`，最终结论同步到 `RESULTS.md`。
