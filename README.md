# English-Windish

> 拍照 → 识别 → 语法解析 → 单词精讲。一个为英语学习者（尤其是初高中学生）打造的 AI 辅助学习工具。

## 这个项目解决什么问题

学生在做英语阅读理解、完形填空、试卷订正时，会遇到三类问题：

1. **看不懂句子结构**——固定搭配、复杂句型、特殊句式识别不出来，靠语感硬猜
2. **查单词效率低**——词典 App 只给中文释义，不给词根词缀、不给词源、不给搭配例句
3. **记录成本高**——错题、生词散落在试卷和笔记本上，无法系统复习

本项目把这三件事合成一个动作：**拍一张照片，得到一份结构化的学习报告**。

## 核心功能

### 功能一：拍照语法解析

上传/拍摄一张英文文本图片，系统输出：

- **原文识别**：多模态大模型直接读图，无需本地 OCR
- **中文翻译**：逐句对照翻译
- **语法标注**：自动标出
  - 固定搭配（如 `be accustomed to doing`）
  - 复杂句型（如定语从句、状语从句、名词性从句）
  - 特殊句式（如倒装、强调句、虚拟语气、独立主格）
- **同步呈现**：语法标注与翻译在同一栏中逐句对齐

### 功能二：单词精讲

对文本中的重点词，输出结构化词卡：

| 板块 | 内容 |
|---|---|
| **词根词缀** | 拆解构词，说明每个词素含义 |
| **词源 / 核心原意** | 追溯词源，点出核心义（帮助理解引申义） |
| **中文释义** | 默认展示高频义项，可展开查看全部（按词性分组） |
| **固定搭配** | 常见高频搭配，每条配**英文例句 + 中文翻译** |

### 功能三：文本直接输入

没有图片时（比如只想查一个生词、分析一个长难句），直接在输入框敲进去即可。
后端走的是同一条分析流水线，只是一个跳过图片识别、直接进入语法与词汇解析。
上限 20000 字符，够放一篇短文。

**为什么必须要有这个功能？** 拍照链路的成本结构是「视觉模型 + 文本模型」两次调用，
查一个单词也要付两次钱、等十几秒。文本输入把视觉那一步省掉，日常查词才变得可用。

### 功能四：历史记录

每次分析完成自动落库（本地 SQLite），抽屉式列表按时间倒序展示，
点任意一条可还原完整结果，也能单条删除或清空。

**为什么存本地而不存云端？** 学生的错题和课文原文属于个人数据，
放本地既省了服务器成本，也避开了「用户内容上云」的合规麻烦。
代价是换设备不同步——这在当前阶段是划算的取舍。

## 技术架构

```
用户上传图片
     │
     ▼
┌─────────────────────┐
│  多模态大模型（视觉） │  ← 直接读图，规避传统 OCR 对手写体的识别瓶颈
└─────────────────────┘
     │  结构化文本
     ▼
┌─────────────────────┐
│  语法解析 Pipeline   │  ← Prompt 工程：先给依据，再给结论，降低幻觉
└─────────────────────┘
     │
     ├──────────────┬──────────────┐
     ▼              ▼              ▼
  翻译对齐       语法标注        知识点抽取
     │              │              │
     └──────────────┴──────────────┘
                    │
                    ▼
┌─────────────────────┐
│   单词精讲引擎       │  ← 词根词缀 / 词源 / 释义分级 / 搭配例句
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│  Web 界面（分栏展示）│
└─────────────────────┘
```

## 技术栈

| 层次 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.13 | |
| 视觉模型 | 通义千问 `qwen-vl-max` | DeepSeek 无视觉模型，故图片识别走通义 |
| 文本模型 | DeepSeek `deepseek-chat` | 推理能力强，负责语法分析与单词精讲 |
| 后端框架 | FastAPI | 原生 async，适配「等待外部 API 数十秒」的场景 |
| 实时通信 | **SSE（Server-Sent Events）** | 分析耗时约 18 秒，需实时推送进度 |
| 前端 | 原生 HTML / CSS / JavaScript | 无框架依赖，零构建步骤 |
| 数据存储 | SQLite | 单词词卡缓存，避免重复查询 |
| 部署 | 待定 | |

### 为什么用 SSE 而不是 WebSocket

本场景是**单向推送**——客户端提交任务后，只有服务器往客户端推进度，
客户端不需要在分析过程中发消息。SSE 正好匹配：

- 基于 HTTP，无需额外的协议握手
- 浏览器原生 `EventSource` 支持
- 断线自动重连

WebSocket 是双向协议，用在这里属于过度设计。

### 为什么前端用 `fetch` 读流而不是 `EventSource`

`EventSource` 只支持 GET 请求，但上传文件必须用 POST。
所以前端用 `fetch` + `response.body.getReader()` 手动解析 SSE 流，
并自行处理分块边界（一个事件可能被切成两半）。

## 开发进度

- [x] 阶段 0：环境搭建、GitHub 仓库初始化
- [x] 阶段 1：核心功能（命令行版跑通全链路）
- [x] 阶段 2：Web 界面 + SSE 实时进度
- [x] 阶段 2.5：结果分栏 Tab、历史记录、文本直接输入
- [ ] 阶段 3：部署上线、性能优化、文档完善

## 项目结构

```
English-Windish/
├── README.md              # 项目说明（本文件）
├── requirements.txt       # Python 依赖
├── .gitignore             # Git 忽略规则
├── .env.example           # 环境变量模板（真实 key 不入库）
├── src/
│   ├── config.py          # 配置管理（双轨：视觉服务商 + 文本服务商）
│   ├── models.py          # 数据模型定义
│   ├── prompts.py         # Prompt 模板集中管理
│   ├── llm_client.py      # 大模型调用封装（多服务商 + JSON 容错 + 重试）
│   ├── pipeline.py        # 分析流水线（CLI 与 Web 共用）
│   ├── storage.py         # 历史记录存储（SQLite）
│   ├── main.py            # 命令行入口
│   ├── ocr/               # 图像识别
│   ├── grammar/           # 语法解析
│   ├── vocabulary/        # 单词精讲（含 SQLite 缓存）
│   ├── report/            # 报告渲染（Markdown / JSON）
│   └── web/               # Web 应用
│       ├── app.py         # FastAPI 后端（SSE 流式进度）
│       └── static/        # 前端（原生 HTML/CSS/JS）
├── data/
│   ├── samples/           # 示例图片
│   ├── uploads/           # 用户上传（.gitignore 排除）
│   └── reports/           # 生成的分析报告
├── docs/
│   └── devlog.md          # 开发日志（含决策记录与踩坑复盘）
└── tests/                 # 测试（51 个，不依赖真实 API）
```

## 快速开始

### 1. 环境准备

```bash
git clone git@github.com:windone286-arch/English-Windish.git
cd English-Windish

# 创建虚拟环境
python -m venv .venv

# 激活（Windows）
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置 API Key

```bash
copy .env.example .env
```

编辑 `.env`，填入你的密钥：

```ini
# 视觉：负责图片识别（DeepSeek 没有视觉模型，必须用通义或智谱）
VISION_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-你的通义key

# 文本：负责语法分析与单词精讲
TEXT_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的deepseek key
```

> `.env` 已被 `.gitignore` 排除，不会被提交。

### 3. 运行

**Web 界面（推荐）**

```bash
python -m src.web.app
```

打开 http://127.0.0.1:8000

**命令行**

```bash
# 分析图片
python -m src.main data/samples/test1.jpg

# 指定词卡数量
python -m src.main data/samples/test1.jpg --words 10

# 只识别文字（省 API 额度，用于快速验证图片质量）
python -m src.main data/samples/test1.jpg --text-only

# 直接分析文本，跳过图片识别（调试 Prompt 用）
python -m src.main --text "The book which I bought yesterday is interesting."

# 输出 JSON
python -m src.main data/samples/test1.jpg --json
```

### 4. 运行测试

```bash
pytest tests/ -v
```

## 开发日志

开发过程记录见 [docs/devlog.md](docs/devlog.md)。

## License

MIT
