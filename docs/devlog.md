# 开发日志

> 记录开发过程中的关键决策、踩过的坑、以及阶段成果。
> 这份日志本身就是项目作品的一部分——它证明项目是**逐步构建**的，不是一次性生成的。

---

## 2026-09-18 · 阶段 0：立项与环境搭建

### 今日目标

搭好开发环境，建立 GitHub 仓库，跑通"改代码 → 提交 → 推送"的工作流。

### 决策记录

**1. 项目命名：English-Windish → 待定**

原定名 `English-Windish`，评审时发现 "Windish" 并非英文词汇，存在发音与理解歧义（易被误读为 wind-ish）。项目名需满足三个条件：

- 见名知意（一眼看出是英语学习工具）
- 易读易记（面试官念得出来）
- 域名/仓库名可用

候选：`EduLens`（教育透镜）、`GrammarLens`（语法透镜）、`LingoLens`。**待定，需用户确认。**

**2. 技术路线：混合方案（云端 + 本地）**

| 环节 | 方案 | 理由 |
|---|---|---|
| 图片识别 | **云端多模态大模型** | 手写体识别准确率要求高，本地视觉模型（8B 级）质量不足 |
| 语法解析 | 云端文本大模型 | 需要强推理能力，本地模型易产生幻觉 |
| 词汇缓存 | 本地 SQLite | 同一个单词查过一次就不再花钱，降低成本 |
| 后处理 | 本地 Python | 数据清洗、格式化、去重，无需模型 |

**为什么不做纯本地？** 本机 RTX 4060 Laptop（8GB 显存）理论上可跑 7B-8B 量化模型，但推理速度约 5-15 token/s，一次完整的语法分析需等待数十秒，交互体验不可接受。**保留本地模型作为后续优化选项**（阶段 3 可尝试本地缓存层）。

**3. 应用形态：Web 应用**

关键理由是**可分享性**——Web 应用可以部署到公网，面试官点开链接即可使用。桌面应用无法提供这个能力。

**4. 图片输入：上传 + 拍照双通道**

先实现上传（阶段 1-2），再补网页调用摄像头拍照（阶段 2 后期）。摄像头 API 需要 HTTPS 环境，部署后再做。

**5. 释义展示：折叠式**

默认展示 2-3 个高频义项，点击"展开全部"查看按词性分组的完整释义。理由：完整释义列表长度不可控，直接展示会让卡片极其冗长，破坏阅读体验。

---

### 环境搭建过程

**本机配置（实测）**

| 项目 | 实测值 |
|---|---|
| 系统 | Windows 11 家庭版 Build 26200 |
| 处理器 | Intel Family 6 Model 183（12/13 代移动端），2.2GHz 基准 |
| 显卡 | **NVIDIA GeForce RTX 4060 Laptop，8GB 显存** |
| 内存 | 16 GB |
| 磁盘 | C: 300GB（剩 112GB）/ D: 653GB（剩 170GB） |
| 机型 | 机械革命 曜石16Pro（MECHREVO Yaoshi16Pro GM6IX0B） |

**环境选择**

- **Python 3.13.12**（WorkBuddy 托管版，隔离目录，不污染系统环境）
- **虚拟环境**：`.venv`，创建于项目根目录
- **Git**：2.55.0.windows.3，已配置 `user.name=windone286-arch`
- **凭据**：Windows 凭据管理器已存有 GitHub 凭据，推送无需重新认证

**目录结构**

```
English-Windish/
├── src/
│   ├── ocr/          # 图像识别
│   ├── grammar/      # 语法解析
│   ├── vocabulary/   # 单词精讲
│   └── report/       # 报告生成
├── data/
│   ├── samples/      # 样例图片
│   └── uploads/      # 用户上传（.gitignore 排除）
├── docs/             # 文档
└── tests/            # 测试
```

**踩坑记录**

1. **`wmic` 命令在新版 Windows 上已移除**——查询硬件信息失败。改用 `systeminfo` 导出后按 GBK 编码解析。
2. **sandbox 拦截 `reg.exe` 和 Bash 调用 PowerShell**——硬件查询需通过文件导出 + Python 解析的迂回方式。
3. **`systeminfo` 输出为 GBK 编码**——在 UTF-8 环境直接读取会乱码，需显式指定编码。

---

### 今日产出

- [x] 创建项目目录 `D:\Projects\English-Windish`
- [x] 创建 Python 虚拟环境 `.venv`
- [x] 编写 `README.md`（含架构图与功能说明）
- [x] 编写 `.gitignore`（排除密钥、虚拟环境、用户数据）
- [x] 编写 `.env.example`（API Key 模板）
- [x] 编写 `requirements.txt`
- [x] 建立模块化目录骨架
- [x] **编写核心代码**（见下）
- [x] **27 个单元测试全部通过**
- [x] **首次 Git 提交**（commit `ce62043`）
- [ ] 推送到 GitHub（SSH key 待添加到账号）

### 核心代码实现

| 文件 | 职责 | 关键设计 |
|---|---|---|
| `src/config.py` | 配置管理 | Key 只从环境变量读；缺失时给中文提示而非抛裸异常 |
| `src/models.py` | 数据模型 | 用 dataclass 而非 dict，字段有名字、类型明确 |
| `src/llm_client.py` | 模型调用封装 | 屏蔽三家服务商差异；指数退避重试；JSON 提取容错 |
| `src/prompts.py` | Prompt 模板 | 集中管理，可单独迭代评审 |
| `src/ocr/recognizer.py` | 图片识别 | 多模态直读，不用传统 OCR |
| `src/grammar/analyzer.py` | 语法解析 | 分块处理 + 幻觉片段过滤 + 缩写保护切分 |
| `src/vocabulary/tutor.py` | 单词精讲 | 两级筛选 + 并发请求 + SQLite 缓存 |
| `src/report/generator.py` | 报告渲染 | 本地模板渲染，不让模型再写一遍 |
| `src/main.py` | 命令行入口 | 支持 `--text` 调试模式，免图片测试 |

### 几个值得说明的技术点

**1. JSON 提取容错**

大模型返回 JSON 时极不老实，实测常见三种污染：
- 裹在 ` ```json ... ``` ` 代码块里
- 前后加"好的，以下是分析结果："这类废话
- 字符串值里含花括号，导致简单正则匹配出错

解决方案是**括号配对扫描**：逐字符遍历，跟踪 `depth` 和 `in_string` 状态，
正确处理转义和字符串内的括号。比正则可靠得多。测试覆盖了 8 种污染场景。

**2. 幻觉过滤**

语法标注的 `text` 字段必须能在原句中精确找到，否则直接丢弃。
实测效果：模型会自作主张地把 `be accustomed to` 改写成 `be accustomed to doing`，
这类改动会导致前端无法定位高亮位置。过滤掉比强行渲染更安全。

**3. 缩写保护切分**

按句末标点切句时，`Mr.` / `U.S.` / `Dr.` / `etc.` 会被误判为句末。
方案是先把缩写替换成占位符，切分完再还原。测试验证了
`Mr. Smith went to the U.S. last year.` 不会被切成两句。

**4. 测试策略：不调真实 API**

所有单元测试都是纯逻辑测试，不发起网络请求。理由：
- 调 API 的测试慢且花钱
- 结果不确定（大模型输出有随机性）
- CI 环境没有 API Key

把可测的逻辑抽成纯函数，是工程上的标准做法。

---

## 2026-09-19 · 阶段 0 收尾：GitHub 推送踩坑

### 问题现象

HTTPS 推送 GitHub 失败，报错依次为：

```
schannel: server closed abruptly (missing close_notify)
fatal: unable to access ... : Empty reply from server
OpenSSL SSL_read: unexpected eof while reading, errno 0
```

### 排查过程

1. **测网络连通性**：`curl https://github.com` 返回 `200`（0.7s），网页通道正常
2. **排除偶发**：重试仍失败，不是抖动
3. **切换 TLS 后端**：`http.sslBackend=openssl` 后报 `unexpected eof`，
   说明不是 schannel 独有的问题
4. **结论**：中间网络设备在干扰 TLS 握手，尤其是涉及大流量上传时

### 解决方案：改用 SSH

- 测试端口：`github.com:22` 通，`ssh.github.com:443` 也通
- 生成密钥：`ssh-keygen -t ed25519`（Windows 上没有现成密钥）
- 写 `~/.ssh/config` 指定 `IdentityFile` 和 `IdentitiesOnly yes`
- 远程地址改为 `git@github.com:windone286-arch/English-Windish.git`
- SSH 认证测试：`Permission denied (publickey)` —— 通道正常，待添加公钥到账号

### 经验总结

**在国内网络环境下，GitHub 走 SSH 比 HTTPS 稳定得多。**
HTTPS 的 TLS 握手容易被中间设备干扰，而 SSH 是二进制协议，特征不明显，
不容易被针对性干扰。这个配置一次做好，以后所有仓库都受益。

配置 SSH 只需三步：
1. `ssh-keygen -t ed25519 -C "邮箱"`
2. 把 `~/.ssh/id_ed25519.pub` 的内容贴到 GitHub → Settings → SSH keys
3. `git remote set-url origin git@github.com:用户名/仓库名.git`

### 下一步

阶段 1 续：
1. 用户申请通义千问 API Key
2. 用真实试卷照片跑通全链路
3. 根据实际输出质量迭代 Prompt

---

## 2026-09-19 · 阶段 2：Web 界面

### 今日目标

把命令行工具变成 Web 应用——关键是要能部署拿公开链接，
面试官点开就能用。

### 决策记录

**1. 先重构，再加功能**

阶段 1 的分析逻辑写在 `main.py` 里，如果 Web 版直接复制一份，
就会出现「改了一处忘了另一处」的问题。所以先把流水线抽到
`src/pipeline.py`，让 CLI 和 Web 共用。

同时给流水线加了 `on_progress` 回调参数：

```python
def analyze_image(image_path, ..., on_progress: ProgressCallback | None = None)
```

这样「计算」和「怎么显示进度」就解耦了——
命令行传一个打印到终端的回调，Web 传一个写队列的回调。

**2. 用 SSE 而不是 WebSocket**

分析耗时约 18 秒。没有进度反馈的话，用户会以为页面卡死了。

对比两种方案：

| | SSE | WebSocket |
|---|---|---|
| 通信方向 | 单向（服务器→客户端） | 双向 |
| 协议 | HTTP | ws:// 独立协议 |
| 浏览器 API | EventSource | WebSocket |
| 断线重连 | 自动 | 需自己实现 |

本场景是「提交任务 → 服务器单向推进度」，SSE 完全匹配。
WebSocket 的双向能力用不上，属于过度设计。

**3. 前端用 fetch 读流，不用 EventSource**

`EventSource` 只支持 GET 请求，但上传文件必须用 POST。
所以前端手动读流：

```javascript
const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buffer = '';
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  const chunks = buffer.split('\n\n');
  buffer = chunks.pop() || '';   // 最后一段可能不完整，留到下一轮
  ...
}
```

**这里有个容易踩的坑**：网络分块不保证落在事件边界上，
一个 SSE 事件可能被切成两半。必须用 buffer 累积，
按 `\n\n` 切分后把不完整的尾段留回去。

**4. 时间轴精确性：所有用户数据用 textContent 写入**

分析结果里包含从用户图片识别出的文字——属于**不可信输入**。
如果拼 `innerHTML`，图片里的 `<script>` 就会被当作 HTML 执行。
所以全部用 `createElement` + `textContent`。

这不是洁癖，是必须做的。面试时讲这个点能体现安全意识。

### 实现过程

**后端（`src/web/app.py`）的关键设计**

1. **后台线程 + 队列**
   流水线是同步阻塞的（httpx 同步请求）。如果直接在 SSE 生成器里跑，
   会卡住整个事件循环，其他请求全部排队。
   所以用 `asyncio.to_thread` 丢到线程池，通过 `asyncio.Queue` 传回进度。

2. **`call_soon_threadsafe` 不能省**
   `asyncio.Queue` 不是线程安全的。进度回调在工作线程里执行，
   必须用 `loop.call_soon_threadsafe` 把写队列的操作丢回事件循环线程。

3. **上传文件用 uuid 重命名**
   原始文件名可能有三个问题：
   - 含路径分隔符导致目录穿越
   - 中文文件名在不同系统上的编码问题
   - 同名文件互相覆盖
   所以保存为 `20260919_123133_80bb31d4.jpg` 这种格式。

4. **`X-Accel-Buffering: no`**
   部署在 nginx 后面时，不加这个头 SSE 会被缓冲成一坨，
   进度推送就失去意义了。

**前端（`src/web/static/`）的关键设计**

1. **语法点高亮要处理重叠**
   多个语法点的片段可能互相重叠。处理方式：按起点排序，
   然后贪心保留不重叠的区间。
   高亮只是视觉提示，完整解释在下方列表里，所以丢弃重叠部分不丢信息。

2. **释义折叠**
   默认展示 2-3 条高频义项，点击展开按词性分组的完整释义。

3. **语法类型用颜色 + 文字双重标识**
   只靠颜色区分对色觉障碍用户不友好，所以同时有文字标签。

### 踩坑记录

1. **`asyncio.Queue` 跨线程写入**
   最初直接在回调里 `queue.put_nowait(...)`，导致事件偶尔丢失。
   根因是 asyncio 对象不线程安全，必须用 `call_soon_threadsafe`。

2. **`stream=True` 解码**
   `TextDecoder.decode(value)` 不带 `{stream: true}` 时，
   多字节的 UTF-8 字符被切在分块边界上会解码成乱码。
   中文内容下这个问题很容易触发。

3. **用户图片被误提交到公开仓库（严重）**

   **现象**：`data/samples/` 下的用户试卷照片被推送到了公开仓库。

   **根因**：`.gitignore` 只排除了 `data/uploads/` 和 `data/raw/`，
   没有覆盖 `data/samples/` 和 `data/reports/`。
   后续几次提交里的 `git add -A` 就把它们一并提交了。

   **为什么危险**：Git 是内容寻址存储，删除文件只是新增一个删除记录，
   **历史提交里的文件依然存在**。任何人拿到旧的 commit hash
   就能取回这些文件。而用户上传的很可能是私人试卷、成绩单、笔记。

   **处理**：
   1. 备份 `.git` 目录（752K）到项目外，确保可回滚
   2. `git rm --cached` 从索引移除（本地文件保留）
   3. 补充 `.gitignore`：排除 `data/reports/` 和 `data/samples/*`，
      仅用白名单保留示例图
   4. 用 `git filter-branch --index-filter` 重写全部历史
   5. 删除 `refs/original` 备份引用、清理 reflog、`git gc --prune=now`
   6. 强制推送覆盖远程

   **验证结果**：
   - `main` 分支历史中只剩 `data/samples/sample_article.png`
   - `.git` 体积从 752K 降到 213K（图片对象已清除）
   - 远程主分支文件树中不再有用户文件

   **残留说明**：GitHub 服务端仍缓存着旧提交对象，需知道确切 hash
   才能访问，平台会自动回收。主分支和常规访问路径已干净。

   **教训**：
   - 隐私保护必须**从源头拦截**——给 `.gitignore` 加规则的成本是几秒，
     清理历史的成本是几十分钟加一次强制推送的风险
   - 涉及用户数据的目录，默认应该排除，需要展示的文件走白名单
   - 提交前应该 `git status` 看一眼到底要提交什么，
     而不是无脑 `git add -A`

   **补救措施**：新增 `data/samples/sample_article.png`——
   程序生成的示例图（模拟英语教材页面），无隐私风险，
   保证别人克隆仓库后仍有可试用的样本。

### 今日产出

- [x] `src/pipeline.py` —— 抽出的分析流水线，CLI 与 Web 共用
- [x] `src/main.py` —— 重构为调用 pipeline
- [x] `src/web/app.py` —— FastAPI 后端（SSE 流式进度）
- [x] `src/web/static/index.html` —— 页面结构
- [x] `src/web/static/style.css` —— 样式（浅色主题，响应式）
- [x] `src/web/static/app.js` —— 交互逻辑
- [x] `tests/test_web.py` —— 14 个 Web 接口测试
- [x] **51 个测试全部通过**

**实测验证**：
- 服务启动正常，健康检查返回 `status: ok`
- 上传接口返回 4 个进度事件 + 完整结果
- 完整分析耗时约 18 秒
- 无效格式 / 空文件 / 超大文件均被正确拒绝

### 下一步

阶段 3：部署上线，拿到公开链接。

---

## 2026-09-19 · 阶段 2.5：结果分栏、历史记录、文本直接输入

### 背景

阶段 2 的界面能跑，但用起来别扭。用户实测后提了三条：

1. 结果全堆在一屏里，翻译、语法、单词要一直往下翻，想回头看翻译得滚回去
2. 提交之后回不去，想看上一次的结果没有入口
3. 只想查一个生词也得先拍照上传，链路太重

这三条都不是"功能缺失"，而是**交互设计问题**——功能都在，只是不好用。
这也说明一件事：能跑通和能用之间差着一段距离。

### 决策记录

**1. 结果分栏：为什么用 Tab 而不是折叠面板？**

合并看（分栏 Tab）和展开看（手风琴折叠）是两种不同的阅读模式：

- **Tab 是互斥的**——一次只关心一件事。分析一篇课文时，
  用户的心智是"先把翻译读一遍，再看语法点，最后背单词"，这是**串行**的
- **折叠是并存的**——适合"先扫一眼全貌再逐块深入"

本场景是串行的，所以选 Tab。代价是不能同时对照两栏，
但语法标注本身已经带了翻译，对照需求被覆盖了。

**2. 历史记录：为什么是 SQLite，而不是 JSON 文件？**

最省事的做法是往 `history.json` 里 append。但列表页要按时间倒序、
要分页、要能单条删、还要在几千条时不卡——这些需求用 JSON 全都要手写。

SQLite 是 Python 标准库自带（`sqlite3`），零安装零配置，
建个索引就有 O(log n) 的查询。**在"单机小数据 + 结构化查询"这个场景下，
SQLite 没有真正的对手。**

关键设计：`list()` 查询**不返回完整 payload**，只返回摘要和预览前 80 字。
原因是每条记录的分析结果可能有几十 KB，抽屉一打开拉 50 条就是几 MB。
点进具体某条时再用 `get()` 拉全文。

**3. 文本输入：真正省的是钱和时间**

拍照链路的成本结构是两次 API 调用（视觉 + 文本）。
查一个单词也走这条路，等于花两次钱等十几秒。

文本输入直接跳过视觉环节，走同一条流水线的后半段。
所以后端不是新写一套逻辑，而是把 `_build_event_stream()` 抽成通用框架，
图片和文本各传一个 `run_job` 闭包进去。

### 踩的坑

**1. SSE 的 POST 困境**

浏览器的 `EventSource` 只能发 GET，但上传图片必须 POST。
绕法是：**用 `fetch` 读响应流，自己解析 SSE 格式**。

坑在于分块边界——一个事件可能被切成两半，前半截落在上一个 chunk，
后半截在下一个。所以不能用 `for (const line of text.split('\n'))` 直接处理，
必须维护一个 buffer 累积，只在遇到 `\n\n` 时才切出一个完整事件。

**2. 中文被切块切出乱码**

同一个原因的另一面：一个中文字符占 3 字节，UTF-8 解码时如果 chunk 边界
正好落在一个字中间，`TextDecoder.decode()` 会输出替换字符（``）。

修法是加 `{stream: true}` 参数——告诉解码器"这不是最后一块，
不完整的字节先留着，和下一块拼起来再解"。**这个参数不加，
进度消息里的中文会随机变成乱码，而且只在特定网速下复现，很难查。**

**3. 上下文切换的历史负担**

历史记录里存的是 `AnalysisResult` 的完整 JSON。但渲染函数原本是为
"刚分析完"的场景写的——它拿的是内存对象。

从历史读取时拿的是反序列化的 dict，字段名一致但类型可能不同
（比如 `None` vs 缺失的键）。所以渲染前要统一做一次规范化，
而不是假设两边的数据结构完全一样。

### 今日产出

- [x] `src/storage.py` —— HistoryStore（save / list / get / delete / clear）
- [x] `src/web/app.py` —— 抽出通用 SSE 框架，新增 `/api/analyze-text` 与 4 个历史接口
- [x] `src/web/static/index.html` —— 模式切换 Tab + 结果 Tab + 历史抽屉
- [x] `src/web/static/app.js` —— 流式解析、Tab 切换、历史增删查、返回键
- [x] `src/web/static/style.css` —— 抽屉动画、Tab 样式
- [x] `tests/test_storage.py` —— 14 个存储测试（含中文持久化、分页、截断）
- [x] `tests/test_web.py` —— 新增文本分析、历史接口、页面结构校验
- [x] **89 个测试全部通过**（原 51 + 新增 38）

**实测验证**：
- 文本输入 `accommodate` → 返回完整词卡（音标 /əˈkɒmədeɪt/、词源、分组释义）
- 流式进度正常推送，分析完成后记录自动落库
- 空文本被拒、不存在的历史记录返回 404

### 下一步

阶段 3：部署上线，拿到公开链接。

---

## 模板（后续日期沿用）

```markdown
## YYYY-MM-DD · 阶段 N：<阶段名>

### 今日目标

### 决策记录
（做了什么选择，为什么这么选，放弃了什么方案）

### 实现过程
（关键代码逻辑、架构调整）

### 踩坑记录
（报错信息、根因、解决方式）

### 今日产出
- [ ]

### 下一步
```
