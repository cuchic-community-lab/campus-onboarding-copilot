# XiaohaiGPT 线上部署指南（宝塔 / systemd / supervisor）

后端是一个零第三方核心依赖的 Python 服务（`http.server` + SQLite + FTS5），
部署到线上时**直接读取宝塔站点目录里的线上文件和知识库**，无需改代码、
无需写死绝对路径。所有路径通过 `config.py` 自动探测：启动时向上查找包含
`files.json` 的目录作为 `SITE_ROOT`（本地与线上目录结构一致）。

---

## 1. 目录结构约定

线上站点根（与本地 `D:\CUC_Files\26新生网站` 同构）：

```
/www/wwwroot/<站点>/            ← SITE_ROOT（自动探测）
├── files.json                  ← 文档目录真值源（files[] + links[]）
├── files/                      ← 官方 PDF/Excel/图片
├── qa/                         ← 问答库 markdown
├── qrcodes.json                ← 二维码清单
├── qrcodes/                    ← 二维码图片
└── copilot/                    ← 本仓库代码（部署位置，名称任意）
    ├── src/campus_copilot/
    ├── data/                   ← 索引库 knowledge.db（自动生成）
    ├── senior_replies/         ← 人工答复文件（可选，见第 5 节）
    ├── start.sh
    └── DEPLOY.md
```

> `SITE_ROOT` 探测规则：从 `copilot/` 向上最多 4 级找含 `files.json` 的目录。
> 也可用环境变量 `CAMPUS_SITE_ROOT` 显式指定（宝塔的"网站目录"不同名时推荐）。

---

## 2. 一键安装与启动（宝塔面板）

```bash
cd /www/wwwroot/<站点>
git clone <仓库> copilot   # 或直接上传 zip 解压到 copilot/
cd copilot
cp .env.example .env.local   # 配置 CAMPUS_LLM_API_KEY 等（可选）
bash start.sh                # 检查依赖 → 增量建索引 → 127.0.0.1:8000
```

建议用 **supervisor 或 systemd** 常驻（不要裸跑 start.sh，否则 SSH 断开服务就停）。

---

## 3. 常驻进程配置

### 3.1 supervisor（宝塔自带，推荐）

`/etc/supervisord.d/xiaohaigpt.ini`：

```ini
[program:xiaohaigpt]
directory=/www/wwwroot/<站点>/copilot
command=/usr/bin/python3 -m campus_copilot.cli serve --host 127.0.0.1 --port 8010
environment=PYTHONPATH="/www/wwwroot/<站点>/copilot/src"
autostart=true
autorestart=true
startsecs=3
stderr_logfile=/www/wwwroot/<站点>/copilot/logs/supervisor.err.log
stdout_logfile=/www/wwwroot/<站点>/copilot/logs/supervisor.out.log
```

宝塔操作路径：软件商店 → Supervisor 管理器 → 添加守护进程（进程名 `xiaohaigpt`、
启动命令填上面的 command、运行目录填 directory、环境变量填 `PYTHONPATH=...`）。

### 3.2 systemd（CentOS/OpenCloudOS 无宝塔 supervisor 时）

`/etc/systemd/system/xiaohaigpt.service`：

```ini
[Unit]
Description=XiaohaiGPT backend
After=network.target

[Service]
WorkingDirectory=/www/wwwroot/<站点>/copilot
Environment=PYTHONPATH=/www/wwwroot/<站点>/copilot/src
ExecStart=/usr/bin/python3 -m campus_copilot.cli serve --host 127.0.0.1 --port 8010
Restart=always
RestartSec=3
User=www

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now xiaohaigpt
systemctl status xiaohaigpt
```

---

## 4. Nginx 反代（宝塔站点配置）

在站点 Nginx 配置的 `server { ... }` 内添加：

```nginx
# XiaohaiGPT：/copilot/ 前缀反代到后端 8010
location /copilot/ {
    proxy_pass http://127.0.0.1:8010/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_read_timeout 60s;
    client_max_body_size 50m;
}
```

> `proxy_pass` 末尾的 `/` 会把 `/copilot/api/chat` 重写为 `/api/chat`，
> 后端无需感知前缀。前端页面用 `xiaohaigpt.html` 时，访问
> `https://<域名>/copilot/xiaohaigpt.html`。
> 若想不挂前缀直接 `https://<域名>/`，把 location 改成
> `location / { proxy_pass http://127.0.0.1:8010/; ... }`（注意与 PHP 站点共存时
> 需用 `location ^~ /copilot/` 限定，不要抢 PHP 的 `/`）。

**PHP 站触发即时索引**：在 `api.php` 上传/删除/二维码更新成功分支追加
（详见 contract.md 第 6 节）：

```php
@file_get_contents('http://127.0.0.1:8010/api/ingest', false, stream_context_create([
  'http' => ['method' => 'POST', 'header' => 'Content-Type: application/json', 'content' => '{}', 'timeout' => 3]
]));
```

---

## 5. 师哥师姐人工答复（可选，免接口）

后端每 60s 轮询 `senior_replies/` 目录（位于 SITE_ROOT 下），把
`<question_id>.md` 文件内容导入为答复。文件内容即答复正文：

```bash
mkdir -p /www/wwwroot/<站点>/senior_replies
echo "建议等开学后看课程老师通知，先别急着买。" > /www/wwwroot/<站点>/senior_replies/q_20260731_8f3a.md
```

> `question_id` 可在 `GET /api/senior/pending`（带 token）或
> `data/senior/questions.json` 里查到。

---

## 6. 环境变量清单

| 变量 | 默认 | 说明 |
|------|------|------|
| `CAMPUS_SITE_ROOT` | 自动探测 | 站点根目录（含 files.json） |
| `CAMPUS_DATA_DIR` | `<repo>/data` | 索引库/处理产物目录 |
| `CAMPUS_AUTO_POLL_SECONDS` | `60` | 自动轮询重建间隔（0=关） |
| `AI_ASSISTED_CHUNKING` | `0` | 1=启用 LLM 辅助切分标签增强 |
| `ANSWERABILITY_SCORE_FLOOR` | `0.06` | 未知判定：top1 分数阈值（v1.2 起默认，原 0.02） |
| `ANSWERABILITY_COVERAGE_FLOOR` | `0.22` | 未知判定：概念覆盖阈值（v1.2 起默认，原 0.15） |
| `ANSWERABILITY_MIN_EVIDENCE` | `2` | 未知判定：最小证据数（v1.2 起默认，原 1） |
| `SENIOR_ADMIN_TOKEN` | `dev-senior-token` | 师哥师姐管理 token（**生产必须改**） |
| `CAMPUS_LLM_PROVIDER` | `makers` | 模型 provider |
| `CAMPUS_LLM_API_KEY` | 空 | 模型 API Key（在 `.env.local` 配置） |
| `CAMPUS_LLM_BASE_URL` | Makers 预设 | 模型接口 |
| `CAMPUS_LLM_MODEL` | `@makers/deepseek-v4-flash` | 模型名 |

生产环境 `.env.local` 建议：

```ini
CAMPUS_SITE_ROOT=/www/wwwroot/<站点>
SENIOR_ADMIN_TOKEN=<改成随机长串>
CAMPUS_LLM_API_KEY=<EdgeOne Makers 的 API Key>
AI_ASSISTED_CHUNKING=0
```

> API Key 获取：登录腾讯云 EdgeOne → Makers → Models → API Key 管理 → 创建专用
> Key（不要提交到仓库，`.env.local` 已在 `.gitignore`）。

---

## 7. 验证

```bash
curl -s http://127.0.0.1:8010/api/health
curl -s -X POST http://127.0.0.1:8010/api/chat -H 'content-type: application/json' \
  -d '{"query":"宿舍是几人间？","profile":{"cohort":"2026"}}'
```

通过反代访问：

```bash
curl -s https://<域名>/copilot/api/health
```

---

## 8. 常见问题

- **索引显示 documents=0 / degraded**：`SITE_ROOT` 探测失败，检查
  `CAMPUS_SITE_ROOT` 或目录层级（向上 4 级内需有 files.json）。
- **PDF 无法解析**：服务器缺 `pypdf`，会自动降级 `metadata_only`（文档仍入库、
  只是正文不可检索）。`pip install pypdf python-docx openpyxl` 后重新
  `python3 -m campus_copilot.cli build`（或删掉 data/index 全量重建）。
- **端口被占**：改 `--port 8010`，同步改 Nginx 反代端口。
- **宝塔安全组**：8010 无需对外开放（仅 127.0.0.1 监听 + Nginx 反代）。
