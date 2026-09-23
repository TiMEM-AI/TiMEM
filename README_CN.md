<p align="center">
  <a href="https://github.com/TiMEM-AI/timem-ai">
    <img src="assets/timem.jpg" width="800px" alt="TiMem - 智能体预测式认知调控引擎">
  </a>
</p>

<p align="center">
  <strong>TiMem：让你的AI随时间进化</strong>
</p>

<p align="center">
  <em>智能体预测式认知调控引擎——让智能体更稳定、能进化、更主动</em>
</p>

<p align="center">
  TiMem 是一款基于<strong>时序分层记忆（TMT）</strong>的<strong>智能体预测式认知调控引擎</strong>。它让智能体能够<strong>稳定执行长程任务</strong>、<strong>持续学习自我进化</strong>、<strong>主动理解用户需求</strong>——将无尽的交互转化为结构化、可行动的认知。
</p>

<p align="center">
  <a href="#-快速开始"><strong>🚀 快速开始</strong></a>
  ·
  <a href="#-核心概念"><strong>🧠 核心概念</strong></a>
  ·
  <a href="#-示例代码"><strong>📖 示例代码</strong></a>
  ·
  <a href="#-云服务"><strong>☁️ 云服务</strong></a>
  ·
  <a href="docs/zh/README.md"><strong>📚 文档索引</strong></a>
  ·
  <a href="#-研究论文"><strong>📄 研究论文</strong></a>
</p>

<p align="center">
  <a href="https://timem.cloud">
    <img src="https://img.shields.io/badge/官网-timem.cloud-blue" alt="官网">
  </a>
  <a href="https://pypi.org/project/timem-ai">
    <img src="https://img.shields.io/pypi/v/timem-ai?color=%2334D058&label=pypi%20package" alt="PyPI 版本">
  </a>
  <a href="https://github.com/TiMEM-AI/timem-ai/blob/main/LICENSE">
    <img src="https://img.shields.io/badge/license-Apache%202.0-blue" alt="许可证: Apache 2.0">
  </a>
  <a href="https://github.com/TiMEM-AI/timem-ai/stargazers">
    <img src="https://img.shields.io/github/stars/TiMEM-AI/timem" alt="星标数">
  </a>
  <a href="https://aclanthology.org/">
    <img src="https://img.shields.io/badge/ACL%202026-Findings-orange" alt="ACL 2026 Findings">
  </a>
</p>

> **🎉 TiMem v1.1.0 - ACL 2026 Findings！** 本次发布包括开源仓库 bug 修复、完整的记忆模型定义，以及基于研究的记忆巩固。**论文已被 ACL 2026 Findings 接收。**

## 🔥 TiMem 亮点

- **预测式认知调控**：通过时序分层记忆主动预判智能体需求和用户意图
- **五级时序记忆树（TMT）**：从细粒度证据到稳定人格的显式时序排序
- **持续自我进化**：智能体从每次交互中学习进化，无需微调
- **长程任务稳定性**：在复杂工作流和长期会话中保持稳定表现
- **复杂度感知召回**：根据查询复杂度自适应检索，平衡精度与效率
- **领先性能**：在 LoCoMo、LongMemEval-S 基准测试中表现优异

# 简介

[TiMem](https://github.com/TiMEM-AI/timem-ai) 是一款基于<strong>时序分层记忆（TMT）</strong>的<strong>智能体预测式认知调控引擎</strong>。它为智能体提供基础设施，使其能够稳定执行长程任务、从交互中持续学习自我进化、主动理解用户需求——将原始对话转化为结构化、可行动的认知。

### 核心特性与使用场景

**核心能力：**
- **时序记忆树（TMT）**：五级层次结构，具有显式时序排序
- **语义引导整合**：无需微调，基于指令引导
- **复杂度感知召回**：根据查询复杂度自适应检索范围
- **多模型支持**：OpenAI、Claude、智谱AI、千问、本地模型

**应用场景：**
- **自主智能体**：复杂多步骤任务的稳定执行与持久上下文
- **AI 助手**：与用户共同进化的主动式、上下文丰富对话
- **企业工作流**：具备持续学习能力的长程业务流程
- **客户支持**：通过深度用户理解提供跨会话个性化服务
- **教育与培训**：追踪并预测学习者需求的自适应辅导
- **生产力工具**：预判用户意图、自我进化的个人助理

## 🚀 快速开始

### 本地部署安装

选择使用托管云服务或本地部署：

### 云服务（推荐）

无需管理基础设施，几分钟内即可上手：

```bash
# 1. 安装 SDK
pip install timem-ai

# 2. 配置凭据
export TIMEM_BASE_URL=https://api.timem.cloud
```

```python
import asyncio
from timem import AsyncMemory

async def main():
    # 初始化客户端
    memory = AsyncMemory(
        api_key="你的API-KEY",
        base_url="https://api.timem.cloud"
    )

    # 添加对话记忆
    result = await memory.add(
        messages=[
            {"role": "user", "content": "你好，我叫张明"},
            {"role": "assistant", "content": "你好张明！"}
        ],
        user_id="user_001",
        character_id="assistant",
        session_id="session_001"
    )
    print(f"添加记忆: {'成功' if result['success'] else '失败'}")

    # 搜索相关记忆
    results = await memory.search(
        query="用户的名字",
        user_id="user_001",
        limit=5
    )
    print(f"找到 {results.get('total', 0)} 条相关记忆")

    await memory.aclose()

asyncio.run(main())
```

### 本地部署（开源）

需要数据库设置，但提供完全控制。

**一行命令 CLI 安装（推荐）：**

```bash
# 克隆并安装
git clone https://github.com/TiMEM-AI/timem-ai.git
cd timem-ai
pip install -e .

# 交互式安装向导（自动完成所有步骤）
timem setup wizard
```

**快速一行命令安装（带 API key）：**

```bash
timem setup quick --provider openai --api-key sk-your-key
```

**手动安装（如需自定义）：**

```bash
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt
cd migration && docker-compose up -d
```

**CLI 常用命令：**

```bash
timem start       # 启动数据库容器
timem stop        # 停止数据库容器
timem status      # 查看服务状态
timem doctor      # 运行环境诊断
timem config init # 交互式配置 .env
```

## 自托管服务

克隆后可以在自己的电脑或一台服务器上跑同一个进程。产品接口给 Agent，页面给聊天，`/aml/add` 与 `/aml/search` 给比赛。记忆生成用星流 `glm-5.3-flash`，聊天回复用 `qwen3.7-flash`，向量用 `qwen3-embedding-8b` 且固定 2048 维。每次对话请求都会显式关闭思考。

贡献者本地要起两个进程：仓库根目录 `python -m server`，以及 `frontend` 里 `npm install` 后 `npm run dev`（http://127.0.0.1:5173，接口代理到 8000）。

使用者只跑 Compose。镜像会先构建 `frontend/dist`，再由服务在 `/` 提供页面。

```bash
pip install -e .
# 填写 .env：XINGLIU_API_KEY、TIMEM_API_KEY
python -m server
```

本机默认只监听 `127.0.0.1:8000`。未构建前端时，这个地址只提供接口；聊天页在开发服务器上。构建后（`cd frontend && npm run build`）再打开 http://127.0.0.1:8000 即可聊天。Agent 调用 `/v1/memories` 与 `/v1/memories/search` 时带上 `TIMEM_API_KEY`。

服务器使用 `deploy/docker-compose.yml`。数据库端口不映射出来，前面用自己的 HTTPS 反代转发到 8000。此时设置 `TIMEM_DEPLOY_MODE=server`，聊天页和 API 都要这把 Key。比赛提交的是 `/aml/add` 与 `/aml/search`。

相对论文实现，这一层是适配：L1 可搜后才返回，L2–L5 在后台补；检索不在 AML Search 里生成答案。模型名在环境变量里，之后可以换成比赛要求的 `gpt-4o-mini` 与 `text-embedding-v4`（2048 维该模型也支持）。

## 📖 示例代码

示例文件位于 [`cloud-service/examples/`](cloud-service/examples/) 目录：

| 文件 | 说明 |
|------|------|
| [01_quick_start.py](cloud-service/examples/01_quick_start.py) | 快速开始 - 5分钟上手 |
| [02_add_memory.py](cloud-service/examples/02_add_memory.py) | 添加记忆示例 |
| [03_search_memory.py](cloud-service/examples/03_search_memory.py) | 搜索记忆示例 |
| [04_chat_demo.py](cloud-service/examples/04_chat_demo.py) | 聊天演示 - 带记忆的 AI 助手 |

### 运行示例

```bash
cd cloud-service/examples

# 配置环境变量
export TIMEM_BASE_URL=https://api.timem.cloud
export TIMEM_API_KEY=你的API_KEY

# 运行示例
python 01_quick_start.py
python 02_add_memory.py
python 03_search_memory.py
python 04_chat_demo.py
```


## 🧠 核心概念

### 系统架构

<p align="center">
  <img src="assets/timem-framework.jpg" width="1000px" alt="TiMem 系统架构">
</p>

**TiMem 架构包含三个核心组件：**

1. **记忆巩固（左侧）**：通过语义引导的巩固，将原始对话转换为 5 级层次记忆（L1-L5）

2. **时序记忆树（中间）**：以显式时序顺序组织记忆，从细粒度片段（L1）到稳定人格画像（L5）

3. **复杂度感知召回（右侧）**：根据查询复杂度自适应检索范围，平衡精度和效率

### 工作原理

```
用户："我想学 Python"

L1：提取事实 → "用户想学 Python"
L2：总结会话 → "用户开始 Python 学习之旅"
L3：每日模式 → "用户这周在学 Python"
L4：每周趋势 → "用户学习时间是工作日晚上"
L5：稳定人格 → "用户 = 正在培训的 Python 开发者"
```

后续查询："用户的的技术背景是什么？"

→ **复杂度分析**：简单事实查询
→ **层次召回**：检查 L1 → L5
→ **结果**：用户在学 Python（来自 L5 人格）
→ **回复**："根据我们的对话，你正在学习 Python..."

## ☁️ 云服务

TiMem 云服务是托管版本，无需部署即可使用。

### 🌐 控制台入口

[**控制台**](https://console.timem.cloud) — 管理 TiMem 云服务（国内）

> **注**：全球版控制台（timem.ai）即将上线。

### 快速开始

详细指南请参考：[cloud-service/README.md](cloud-service/README.md)

### 云服务 vs 本地部署

| 特性 | 云服务 | 本地部署 |
|:------|:--------|:--------|
| **部署** | 无需部署 | 需要配置 |
| **维护** | 平台管理 | 自行管理 |
| **数据控制** | 云端存储 | 完全控制 |
| **成本** | 按量付费 | 固定成本 |
| **定制化** | 有限定制 | 完全定制 |

### 相关文档

| 文档 | 说明 |
|:------|:------|
| [cloud-service/README.md](cloud-service/README.md) | 云服务完整指南 |
| [cloud-service/api/authentication.md](cloud-service/api/authentication.md) | 认证指南 |
| [cloud-service/api/reference.md](cloud-service/api/reference.md) | REST API 参考 |

## 📄 研究论文

### 论文

**🎉 已被 ACL 2026 Findings 接收！**

**TiMem: Temporal-Hierarchical Memory Consolidation for Long-Horizon Conversational Agents**

长程对话智能体需要管理不断增长的交互历史，这些历史很快就会超过大语言模型（LLM）的有限上下文窗口。现有的记忆框架对跨层次的时序结构化信息支持有限，往往导致记忆碎片化和不稳定的长程个性化。

我们提出了 TiMem，一个时序分层记忆框架，通过时序记忆树（TMT）组织对话，实现从原始对话观察到逐步抽象的人格表征的系统化记忆巩固。

### 核心特性

1. **时序层次组织**：TMT 在 5 个层次上提供显式时序排序
2. **语义引导整合**：无需微调即可实现跨层次记忆巩固
3. **复杂度感知记忆召回**：在不同复杂度的查询间平衡精度和效率

### 基准测试结果

| 基准测试 | 指标 | TiMem 性能 |
|:----------|:-----|:-----------|
| **LoCoMo** | 准确率 | **75.30%** （最优） |
| **LongMemEval-S** | 准确率 | **76.88%** （最优） |
| **LoCoMo** | 记忆压缩 | **减少 52.20%** 召回 token |

**流形分析**：TiMem 在 LoCoMo 上展现出清晰的人格分离，在 LongMemEval-S 上降低了分散度，将时序连续性作为长程对话智能体记忆的一等组织原则。

**完整论文**：[arXiv:2601.02845](https://arxiv.org/abs/2601.02845)

## 🎉 📋 更新日志

持续维护升级记录：

- **2026.05.25** - **v1.1.0**: 开源仓库 bug 修复，恢复缺失的 `timem/models/` 和 `timem/schemas/`，论文被 ACL 2026 Findings 接收
- **2026.02.08** - **v1.0.0**: 开源仓库正式上线
- **2026.02.01** - 云服务上线内测预览版
- **2026.01.06** - TiMem 研究论文发布于 arXiv

## 🗺️ 路线图

向下一代智能体认知基础设施持续演进。

### 🧠 记忆架构进化

| 功能 | 描述 | 价值 |
|:--------|:------------|:-------|
| **L1→L2 跳层人格连接** | L1 片段向 L2 会话摘要巩固时，跳层连接将低频但高信号特征直接路由至用户画像模块，实现结构化持久化存储。 | 长期用户理解永不衰减——饮食禁忌、专业能力、情感模式等关键特征跨越会话边界持续保留，不受噪声干扰。 |
| **关系摘要模块** | 专用关系洞察层，跨会话提取并维护用户、实体、概念之间的推断关系图谱。 | 智能体发展真正的"社交智能"——不仅理解用户说了什么，更理解他们与人、组织、话题之间的关系网络。 |
| **增强 L3 事件图谱** | 将 L3 日度记忆升级为原生事件图谱拓扑——时间节点通过因果、时序、主题边连接，替代平面摘要。 | 多跳时序推理："因为你周二调整了会议，周三工作量增加，这解释了今天的压力。" |
| **多跳推理能力** | 检索管道扩展显式图谱遍历，支持在遥远但因果关联的记忆之间进行链式推理。 | 复杂问题（需要综合数周或数月交互历史）获得结构化推理链路，而非单纯的相似度匹配。 |
| **记忆标签与主题索引** | 为所有记忆记录增加 `tags` 字段，自动识别内容主题，构建时序层次之外的二级主题索引。 | 宽泛主题查询（"关于我健身目标的一切"）检索速度提升 3-5 倍，同时不牺牲具体事实查询的精度。 |

### ⚡ 推理与调控效率

| 功能 | 描述 | 价值 |
|:--------|:------------|:-------|
| **小模型 Plan & Gating** | 用针对 TiMem 决策边界微调的专用小模型（7B 参数以下）替代基于 LLM 的规划与门控决策。 | Plan 生成和路由决策延迟降低 10-50 倍，实现实时智能体响应，同时保持决策质量。 |

### 🤝 多智能体与运维

| 功能 | 描述 | 价值 |
|:--------|:------------|:-------|
| **多智能体协作** | 原生支持共享记忆空间、角色感知委派和协作智能体间的冲突消解协议。 | 专业化智能体团队（研究员、规划员、执行者）共享统一认知基底——交接零信息损耗。 |
| **本地优先面板** | 以隐私为中心的 Web 面板，用于记忆检视、调试与治理——完全本地运行，零云端依赖。 | 完全数据主权。用户和运维人员可在不暴露敏感历史的前提下，浏览、审计、管理智能体的记忆内容。 |

---

## 📚 文档与支持

### 📖 文档
- **[完整文档](docs/zh/README.md)** - 完整文档中心
- **[开发者指南](docs/zh/developer-guide/README.md)** - 30分钟开发者快速入门
- **[本地部署指南](skill/install.md)** - 本地部署安装步骤

### 🔧 API 与 SDK
- **[API 参考](docs/zh/api-reference/overview.md)** - REST API 文档
- **[Python SDK](docs/zh/sdk/python/quickstart.md)** - Python 集成
- **[认证指南](docs/zh/api-reference/authentication.md)** - 认证说明

### 🛠️ 支持
- **问题反馈**：[GitHub Issues](https://github.com/TiMEM-AI/timem/issues)
- **贡献指南**：[CONTRIBUTING.md](CONTRIBUTING.md)
- **故障排查**：[docs/zh/troubleshooting.md](docs/zh/troubleshooting.md)

### 💬 社区

TiMem 欢迎各种形式的贡献：代码、文档、bug 报告、功能建议和反馈。随时提交 Issue 或 Pull Request，每一份贡献都会让 TiMem 变得更好。

加入我们的中文社区获取技术支持和交流：

<p align="center">
  <img src="assets/wechat_group_cn.jpg" width="200px" alt="微信群">
  <img src="assets/feishu_group_cn.png" width="200px" alt="飞书群">
</p>

- **微信群**: 扫码加入 TiMem 技术交流群
- **飞书群**: 扫码加入 TiMem 开发者社区


## 📝 引用

如果使用 TiMem 进行研究，请引用：

```bibtex
@misc{li2026timemtemporalhierarchicalmemoryconsolidation,
      title={TiMem: Temporal-Hierarchical Memory Consolidation for Long-Horizon Conversational Agents},
      author={Kai Li and Xuanqing Yu and Ziyi Ni and Yi Zeng and Yao Xu and Zheqing Zhang and Xin Li and Jitao Sang and Xiaogang Duan and Xuelei Wang and Chengbao Liu and Jie Tan},
      year={2026},
      eprint={2601.02845},
      archivePrefix={arXiv},
      primaryClass={cs.CL},
      url={https://arxiv.org/abs/2601.02845},
}
```

## ⚖️ 许可证

TiMem 采用双协议模型，兼顾开放性与易用性：

- **核心引擎** — `timem/`、`storage/`、`services/`、`llm/`、`migration/`  
  [Apache License 2.0](LICENSE)：含专利授权保护，适用于生产基础设施
- **工具与集成** — `tools/`、`cloud-service/`、`docs/`  
  [MIT License](tools/LICENSE)：极度宽松，鼓励社区工具和商业化集成

详见各 LICENSE 文件完整条款。欢迎各种形式的贡献 — PR 和 Issue 让 TiMem 变得更好。


## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=TiMEM-AI/timem&type=Date)](https://star-history.com/#TiMEM-AI/timem&Date)

---

<p align="center">
  <strong>⭐ 如果 TiMem 对你有帮助，请在 GitHub 上给我们星标！</strong>
  <br><br>
  由 TiMem 团队打造
</p>
