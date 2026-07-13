# 部署说明

China Doc TruthKeeper 是通过 SSE 提供 MCP 工具的 Python 服务。推荐部署在 AWS 中国区 EC2（`cn-north-1` 或 `cn-northwest-1`），并由 Nginx 或 ALB 终止 TLS。服务默认仅监听 `127.0.0.1:8000`，避免将未经认证的 MCP SSE 端点直接暴露到公网。

## 1. 前置条件

- Python 3.10+，或 Docker 24+。
- 一台中国区 EC2 实例；安全组只允许受信任客户端或反向代理访问 443。
- 用于只读验证的 EC2 IAM Role。最小权限应按实际验证服务收敛；不要在生产环境使用管理员权限。
- 可选的公开 Qwen3-235B-VL OpenAI 兼容接口密钥。未设置密钥时，文档审计工具仍会抓取文档并返回本地知识库对照结果，但不会执行 LLM 分析。

## 2. EC2 + systemd 部署

```bash
sudo useradd --system --create-home --shell /sbin/nologin truthkeeper
sudo mkdir -p /opt/china-doc-truthkeeper /var/lib/truthkeeper
sudo chown -R truthkeeper:truthkeeper /opt/china-doc-truthkeeper /var/lib/truthkeeper
git clone https://github.com/zjinsong/aws-china-doc-validation.git /opt/china-doc-truthkeeper
cd /opt/china-doc-truthkeeper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
```

创建 `/etc/china-doc-truthkeeper.env`，并设置权限为 `600`：

```ini
AWS_DEFAULT_REGION=cn-north-1
TRUTHKEEPER_DB_PATH=/var/lib/truthkeeper/truthkeeper.db
TRUTHKEEPER_HOST=127.0.0.1
TRUTHKEEPER_PORT=8000
QWEN_API_KEY=replace-with-your-public-qwen-api-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen3-235b-vl
```

`submit_feedback` 使用无头 Chromium 提交 AWS 中国区文档页面反馈。部署主机必须能访问 `docs.amazonaws.cn`，并且只应允许可信 MCP 客户端调用该工具；提交前请确保问题摘要和证据中不含凭证、账户 ID 或其他敏感信息。

创建 `/etc/systemd/system/china-doc-truthkeeper.service`：

```ini
[Unit]
Description=China Doc TruthKeeper MCP Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=truthkeeper
Group=truthkeeper
WorkingDirectory=/opt/china-doc-truthkeeper
EnvironmentFile=/etc/china-doc-truthkeeper.env
Environment=PYTHONPATH=/opt/china-doc-truthkeeper/src
ExecStart=/opt/china-doc-truthkeeper/.venv/bin/python -m china_doc_truthkeeper.server
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

启动并验证：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now china-doc-truthkeeper
sudo systemctl status china-doc-truthkeeper
sudo journalctl -u china-doc-truthkeeper -f
```

## 3. Nginx TLS 反向代理

将 `mcp.example.com`、证书路径和允许访问的网络替换为实际值。SSE 需要关闭代理缓冲并保留长连接。

```nginx
server {
    listen 443 ssl http2;
    server_name mcp.example.com;

    ssl_certificate     /etc/letsencrypt/live/mcp.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/mcp.example.com/privkey.pem;

    location / {
        allow 10.0.0.0/8;
        deny all;
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

MCP SSE URL 示例：`https://mcp.example.com/sse`。

## 4. Docker 部署

```bash
git clone https://github.com/zjinsong/aws-china-doc-validation.git
cd aws-china-doc-validation
docker build -t china-doc-truthkeeper:latest .
docker run -d --name china-doc-truthkeeper \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v truthkeeper-data:/var/lib/truthkeeper \
  -e AWS_DEFAULT_REGION=cn-north-1 \
  -e QWEN_API_KEY \
  -e QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
  -e QWEN_MODEL=qwen3-235b-vl \
  china-doc-truthkeeper:latest
```

在 EC2 上推荐使用 IAM Role 获取 AWS 临时凭证；不要把长期 `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` 写入镜像、仓库或 systemd 文件。

## 5. 上线检查清单

- 服务进程以非 root 用户运行，数据库目录可写。
- 安全组和 Nginx 仅允许可信来源访问；生产环境使用 HTTPS。
- IAM Role 遵循最小权限，仅允许所需的 Describe/List/Get 操作。
- Qwen API Key 存放在受限的环境文件或 Secrets Manager 中，不提交至 Git。
- 已执行 `playwright install --with-deps chromium`，并允许服务访问 `docs.amazonaws.cn`。
- 备份 `/var/lib/truthkeeper/truthkeeper.db`，并监控 systemd / Nginx 日志。
- 先用 `query_knowledge_base` 和 `verify_feature` 验证 MCP 客户端连通性，再开放文档审计功能。
