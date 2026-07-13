# China Doc TruthKeeper — AWS 中国区文档真相守护者

## 作品标题

**China Doc TruthKeeper** — 基于 MCP 的 AWS 中国区服务可用性实时验证与文档准确性守护平台

---

## 场景与痛点

### 面向谁

AWS 中国区客户、TAM、SA 等需要准确了解中国区服务可用性的人员。

### 解决什么问题

AWS 中国区与 Global Region 之间存在大量 service/feature gap，客户面临以下痛点：

- **文档不准确**：中国区文档往往从 Global 直接复制，未反映实际可用性。客户按文档操作却发现功能不存在
- **粒度不够**：Regional Services List 只到服务级别，无法告诉你某个具体 feature（如 DynamoDB Global Tables）是否可用
- **AI 被误导**：越来越多客户使用 AI 读文档获取信息，不准确的文档导致 AI 给出错误答案
- **验证成本高**：客户需要自己去 console/CLI 尝试才能确认某个 feature 是否可用，浪费时间
- **信息分散**：可用性信息散落在 What's New、文档、论坛等多个地方，没有统一入口

### 为什么值得解决

- 这是中国区客户**每天都会遇到**的问题，影响面广
- 不准确的文档导致客户做出错误的架构决策，后果严重
- TAM 每周花大量时间回答"这个功能中国区有没有"类问题，自动化可以大幅提效
- 通过文档审计和反馈机制，可以推动文档质量的持续改善

---

## 技术实现

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│        任意支持 MCP 的客户端 / 应用                       │
│    ┌─────────────────────────────────────────┐          │
│    │  公开 Qwen3-235B-VL 模型服务            │          │
│    │  - OpenAI 兼容 API                       │          │
│    │  - MCP: 通过 SSE URL 连接                │          │
│    └──────────────────┬──────────────────────┘          │
└───────────────────────┼─────────────────────────────────┘
                        │ MCP Protocol (SSE over HTTPS)
┌───────────────────────┼─────────────────────────────────┐
│          MCP Server (Python + FastMCP)                   │
│          部署: EC2 cn-north-1                            │
│                       │                                  │
│  ┌────────────────────┼──────────────────────────┐      │
│  │              Tool Layer                       │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ query_knowledge_base                  │   │      │
│  │  │ 查询本地知识库中的可用性记录           │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ verify_feature                        │   │      │
│  │  │ 查询 Botocore 中国区服务端点目录       │   │      │
│  │  │ 结果自动回写知识库                     │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ verify_document_feature               │   │      │
│  │  │ 文档差异 + 安全 List/Describe API 探针 │   │      │
│  │  │ 数据驱动探针注册表（30+ 服务）         │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ verify_feature_comprehensive          │   │      │
│  │  │ 多源证据链：文档 → 发布公告 → API 探针 │   │      │
│  │  │ 综合分级结论                           │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ audit_documentation                   │   │      │
│  │  │ 抓取中国区文档 → 对比验证结果          │   │      │
│  │  │ 输出不准确点 + 修改建议               │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  └───────────────────────────────────────┘   │      │
│  │  ┌───────────────────────────────────────┐   │      │
│  │  │ submit_feedback                       │   │      │
│  │  │ 自动提交中国区文档页面的反馈表单       │   │      │
│  │  └───────────────────────────────────────┘   │      │
│  └───────────────────────────────────────────────┘      │
│                                                          │
│  ┌───────────────────────────────────────────────┐      │
│  │  Knowledge Base (SQLite) + AWS Test Account   │      │
│  └───────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────┘
```

### AI 能力使用

| AI 能力 | 具体应用 | 实现方式 |
|---------|---------|---------|
| **MCP (Model Context Protocol)** | 暴露 6 个 tools 供智能体调用 | FastMCP + SSE transport |
| **LLM 推理** | 理解用户问题、决定验证策略、生成文档审计报告 | 公开 Qwen3-235B-VL OpenAI 兼容接口 |
| **Tool Use** | 自动调用 AWS API 验证 feature 可用性 | boto3 + 中国区测试账号 |
| **知识积累** | 验证结果自动回写知识库，形成数据飞轮 | SQLite 持久化 |

### 关键技术点

- **MCP 远程服务**：通过 SSE transport 暴露 MCP Server，可由任意支持 MCP 的客户端通过 URL 连接
- **服务端点验证**：`verify_feature` 检查 Botocore 的 `aws-cn` 服务区域目录；该目录为 SDK 静态数据、滞后于新功能上线，因此"未收录"仅作为不确定信号，不等于功能不可用
- **文档驱动验证**：`verify_document_feature` 从中国区文档提取 List/Describe API，并按数据驱动的探针注册表（`probes.json`，覆盖 30+ 服务）执行探针，只调用无需账户资源标识的安全 API
- **多源证据链**：`verify_feature_comprehensive` 按可靠性递进综合三个来源——中国区文档对比 → What's New 发布公告（amazonaws.cn/new）→ 安全 API 探针，输出分级结论（`available`/`likely_available`/`unavailable`/`unknown`）并附证据与理由
- **证据分级**：区分 `available`、`api_available`、`unavailable`、`conflict` 和 `unknown`，权限不足不会误判为功能不可用
- **知识库飞轮**：每次验证结果自动入库，后续查询直接命中缓存，越用越准确
- **文档审计**：抓取中国区文档内容，与知识库交叉对比，自动发现不准确描述

---

## 如何运行

### 环境依赖

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | 3.10+ | 运行环境 |
| FastMCP | latest | MCP Server 框架 |
| boto3 | latest | AWS API 调用 |
| requests + BeautifulSoup4 | latest | 文档抓取解析 |
| Playwright + Chromium | latest | 自动填写并提交 AWS 中国区文档反馈 |
| 公开 Qwen3-235B-VL 接口 | - | 可选的文档审计推理 |

### 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

`submit_feedback` 会直接打开传入的 `https://docs.amazonaws.cn/...` 页面，在其反馈组件中填写问题摘要和验证证据并提交。它不会创建 Support case、发送邮件或在本地保存草稿；请仅提交已审核且不含敏感信息的内容。

`verify_document_feature(documentation_url, service, feature, region, resource_name=None)` 会读取指定中国区文档，发现相关的 List/Describe API，并使用 AWS 中国区凭证调用目标区域端点。探针由数据驱动的注册表（`probes.json`）驱动，支持三类探针：`instance_family`（如 EC2 实例族通过 `DescribeInstanceTypeOfferings` 验证实际供应）、`api_reachable`（零参数 List/Describe 确认 API 可达）、以及 `resource_probe`（针对需要单一资源标识的功能，仅在调用方显式提供 `resource_name` 时才发起只读调用）。工具不会调用 Create、Update、Put 或 Delete API，也不会保存 API 返回的账户资源列表。

`verify_feature_comprehensive(service, feature, region, documentation_url=None, resource_name=None)` 按可靠性递进综合中国区文档、What's New 发布公告与安全 API 探针，给出分级结论。静态 SDK 目录仅作弱信号，永远不会盖过发布公告或实时探针，从根本上避免新功能因 SDK 数据滞后被误判为不可用。

### 配置

1. **AWS 测试账号 credentials**：

```bash
export AWS_ACCESS_KEY_ID=YOUR_KEY_HERE
export AWS_SECRET_ACCESS_KEY=YOUR_SECRET_HERE
export AWS_DEFAULT_REGION=cn-north-1
```

2. **可选：配置公开 Qwen3-235B-VL 接口**：

```bash
export QWEN_API_KEY=YOUR_PUBLIC_QWEN_API_KEY
export QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export QWEN_MODEL=qwen3-235b-vl
```

3. **启动 MCP Server**：

```bash
PYTHONPATH=src python -m china_doc_truthkeeper.server
```

4. **MCP 客户端配置**：
   - 在支持 MCP 的客户端中添加 MCP 服务器
   - 填入 Server URL（如 `https://<your-ec2-ip>:8000/sse`）
   - Transport 选择 SSE

### 使用

在支持 MCP 的客户端中与智能体对话：

```
用户：DynamoDB Global Tables 在中国区能用吗？
用户：帮我检查一下这个文档是否准确：https://docs.amazonaws.cn/...
用户：S3 Intelligent-Tiering 在宁夏区域支持吗？
```

---

## 开源 / 第三方声明

| 库/框架 | 用途 | License |
|---------|------|---------|
| [FastMCP](https://github.com/jlowin/fastmcp) | MCP Server 框架 | MIT |
| [boto3](https://github.com/boto/boto3) | AWS SDK for Python | Apache-2.0 |
| [BeautifulSoup4](https://www.crummy.com/software/BeautifulSoup/) | HTML 文档解析 | MIT |
| 公开 Qwen3-235B-VL 接口 | 可选的文档审计推理 | 以服务提供商条款为准 |

所有代码为原创开发。

## 部署

参见 [部署说明](docs/DEPLOYMENT.md)，包括 EC2 + systemd、Nginx TLS、Docker、安全组、IAM Role 与 Qwen API Key 的安全配置。
