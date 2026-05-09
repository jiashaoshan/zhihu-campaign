# 📢 知乎获客技能 (zhihu-campaign)

> AI 驱动的知乎全自动获客解决方案 — 文章发布 + 评论区获客  
> 基于 BrowserWing 浏览器自动化 + DeepSeek API 驱动内容生成

---

## 目录

- [功能总览](#功能总览)
- [系统架构](#系统架构)
- [模块详解](#模块详解)
  - [模块一：文章发布](#模块一文章发布)
  - [模块二：评论区获客](#模块二评论区获客)
- [AI 内容生成](#ai-内容生成)
  - [文章生成策略](#文章生成策略)
  - [评论生成策略](#评论生成策略)
  - [关键词生成策略](#关键词生成策略)
- [反爬与安全策略](#反爬与安全策略)
- [封面图片系统](#封面图片系统)
- [环境要求](#环境要求)
- [安装与部署](#安装与部署)
- [快速使用](#快速使用)
- [配置参考](#配置参考)
- [BrowserWing 脚本](#browserwing-脚本)
- [文件结构](#文件结构)
- [故障排查](#故障排查)

---

## 功能总览

| 模块 | 功能 | 技术栈 | 自动化程度 |
|------|------|--------|-----------|
| **📝 文章发布** | LLM 根据产品链接自动生成知乎长文 → 自动发布 | DeepSeek API + 提示词工程 + BrowserWing | ✅ 全自动（可开启人工确认） |
| **🎯 评论区获客** | 关键词搜索 → AI 四维评分筛选 → LLM 生成自然评论 → 自动评论 | DeepSeek API + 评分算法 + BrowserWing | ✅ 全自动（含反爬保护） |

---

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                  zhihu-campaign.py                   │
│               (统一编排入口，处理CLI参数)               │
└──────────┬──────────────┬──────────────────────────┘
           │              │
           ▼              ▼
┌─────────────────┐  ┌──────────────────────────┐
│  文章发布模块    │  │    评论区获客模块          │
│                 │  │                          │
│ publisher.py    │  │ comment-acquisition.py   │
│                 │  │                          │
│ ┌─── 步骤 ──┐   │  │ ┌─── 流程 ──────────┐    │
│ │ ① LLM文章   │  │  │ │ ① LLM生成关键词    │    │
│ │ ② 保存草稿  │  │  │ │ ② BW搜索文章       │    │
│ │ ③ Pexels封面│  │  │ │ ③ AI四维评分筛选   │    │
│ │ ④ BW发布    │  │  │ │ ④ LLM生成评论      │    │
│ └────────────┘   │  │ │ ⑤ BW发表评论+反爬  │    │
│                  │  │ └────────────────────┘    │
└────────┬─────────┘  └───────────┬──────────────┘
         │                        │
         └────────┬───────────────┘
                  ▼
         ┌──────────────────┐
         │   zhihu_llm.py    │ ← DeepSeek API 封装
         └──────────────────┘
                  │
                  ▼
         ┌──────────────────┐
         │  BrowserWing API  │ ← 浏览器自动化（三个注册脚本）
         │  http://127.0.0.1 │    - dd8a7911 (搜索)
         │       :8080       │    - f3ac1d6a (评论)
         └──────────────────┘    - 8478f76d (发布文章)
```

### 技术栈

```
┌──────────┐    ┌───────────┐    ┌────────────┐
│ Python   │───▶│ DeepSeek  │───▶│   Pexels   │
│ 3.8+     │    │ API       │    │ 图片API    │
└──────────┘    └───────────┘    └────────────┘
     │
     ├──▶ BrowserWing (Chrome自动化)
     │      ├── 关键词搜索  → 提取文章列表
     │      ├── 发表评论    → 自动填写提交
     │      └── 发布文章    → 填写标题/正文/封面
     │
     └──▶ 本地存储
            ├── 文章草稿    → data/article_draft_*.md
            ├── 发布历史    → data/published-articles.json
            ├── 评论历史    → data/commented-history.json
            └── 执行日志    → data/campaign_*.log
```

---

## 模块详解

### 模块一：文章发布

**完整流程：**

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  LLM生成  │──▶│ 保存草稿  │──▶│ 获取封面  │──▶│ BW发布   │──▶│ 记录归档  │
│  文章正文  │   │ .md      │   │ Pexels   │   │ 到知乎   │   │ history  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
     │              │               │              │
 模板驱动       本地存储       下载到本地      BW脚本8478f76d
 article-       data/          ~/zhihu_       标题≤30字
 prompt.md     article_       cover_images/   正文≤4000字
               draft_*.md
```

**文章生成提示词策略 (`templates/article-prompt.md`)：**

文章采用"七段式"结构，每段有明确的营销心理学目标：

| 段落 | 作用 | 关键元素 |
|------|------|---------|
| ① 痛点钩子 | 制造共鸣 | 真实感危机故事 + 具体数字 |
| ② 认知颠覆 | 打破刻板印象 | 对比表格 + 反常识定义 |
| ③ 实操演示 | 证明可行性 | 代码/操作前后对比 |
| ④ 场景画像 | 对号入座 | 3-4类用户画像 |
| ⑤ 风险兜底 | 消除顾虑 | ✅ 清单体呈现 |
| ⑥ 行动号召 | 低门槛转化 | 免费额度 + 链接植入 |
| ⑦ 利益声明 | 社区信任 | "利益相关"声明 |

**封面图片获取：**

文章发布时自动从 Pexels （免费图库）搜索与主题相关的图片，下载到本地 `~/zhihu_cover_images/` 目录，作为封面参数传给 BrowserWing 脚本。无需人工干预。

> **注意**：Pexels API Key 已内嵌在代码中（`scripts/zhihu-article-publisher.py` 中的 `PEXELS_API_KEY`），一般无需额外配置。如需使用自己的 Key，可修改该文件。

### 模块二：评论区获客

**完整流程：**

```
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│  LLM生成  │──▶│ BW搜索   │──▶│ AI 四维  │──▶│ LLM生成  │──▶│ BW发表   │
│  关键词   │   │ 文章列表  │   │ 评分筛选  │   │ 自然评论  │   │ +反爬策略  │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
     │              │               │              │
  10个关键词     BW脚本         筛选Top 10      四种评论类型   60-180s间隔
  + 种子池      dd8a7911                        随机选择      每日15条上限
  备选降级                                        每小时5条上限
```

#### AI 四维评分算法

| 维度 | 权重 | 计算方式 | 说明 |
|------|------|---------|------|
| 🔥 热度 | 40% | `min(热度 / 10000 × 40, 40)` | 基于浏览量/关注数 |
| 💬 互动 | 30% | `min((赞同数 + 评论数×2) / 100 × 30 / 30, 30)` | 文章讨论热度 |
| ⏰ 时效 | 20% | `max(0, 20 − max(天数−30, 0) × 20 / 180)` | 30天内满分，逐日衰减 |
| ⭐ 质量 | 10% | `min(内容长度 / 200, 10)` | 内容完整度 |

#### 关键词管理策略

```
LLM动态生成 (10个/次)
    │
    ├── 选3个 → 本次使用
    │         └── 记录到 last_used (降权机制)
    │
    └── LLM不可用时 → 种子池 keywords.json (30个备选)
```

#### 评论生成策略

四种评论类型随机选择，模拟真实用户的多样化发言方式：

1. **赞同补充型** — 先赞同核心观点，再补充自己的经验/数据
2. **提问讨论型** — 提出问题，分享自己的解决方案
3. **实战分享型** — 分享用过的工具/方法，对比优劣
4. **案例分析型** — 用项目案例说明，引出实际效果

评论风格要求：
- 字数：60-200字（代码限制最多200字）
- 口语化，带语气词（"说实话"、"有一说一"）
- 含具体数字、时间节点、场景
- 避免营销腔和排比句

---

## 反爬与安全策略

### 速率控制

| 限制项 | 默认值 | 配置位置 |
|--------|--------|---------|
| 工作时段 | 8:00-23:00 | `config/anti-crawl.json` |
| 每日评论上限 | 15条 | `config/anti-crawl.json` |
| 每小时评论上限 | 5条 | `config/anti-crawl.json` |
| 评论间隔 | 60-180秒随机 | `config/anti-crawl.json` |
| 搜索间隔 | 5-15秒随机 | `config/anti-crawl.json` |
| 关键词切换间隔 | 10-30秒随机 | `config/anti-crawl.json` |

### 去重策略

- **URL 去重**：同一篇文章只评论一次
- **作者去重**：同一作者最多评论 1 篇文章
- 已评论记录持久化到 `data/commented-history.json`

### 失败重试

- 最大重试次数：3次
- 基础延迟：30秒
- 所有 BrowserWing API 调用有超时保护

---

## 封面图片系统

文章发布时，系统自动从 Pexels 获取与文章主题匹配的封面图片。

### 流程

```
文章标题/正文
    │
    ├── 提取主题关键词（AI/科技/大数据等）
    │
    ├── Pexels API 搜索（landscape, large尺寸）
    │   Header: Authorization: {PEXELS_API_KEY}
    │
    ├── 下载到本地 ~/zhihu_cover_images/
    │
    └── 本地路径传给 BW 脚本 → 知乎发布时设置为封面
```

### 容错

- Pexels API 不可用时，返回空封面（不影响文章发布）
- 会自动更换搜索关键词重试（使用兜底词 "technology"）
- 图片下载失败时优雅降级

---

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.8+ | 运行时环境 |
| BrowserWing | 最新 | 浏览器自动化服务，运行在 `http://127.0.0.1:8080` |
| DeepSeek API | - | 内容生成 API（默认模型：`deepseek-v4-flash`） |
| Pexels API | - | 免费封面图片源（Key 内嵌） |

### Python 依赖

```bash
requests    # HTTP 请求（LLM API + BrowserWing API + Pexels API）
```

---

## 安装与部署

### 1. 克隆/放置项目

```bash
# 从项目目录复制或克隆
git clone https://github.com/jiashaoshan/zhihu-campaign.git
cd zhihu-campaign
```

### 2. 配置环境变量

```bash
# DeepSeek API Key（必需）
export DEEPSEEK_API_KEY=sk-your-key-here

# BrowserWing 地址（可选，默认 http://127.0.0.1:8080）
export BROWSERWING_EXECUTOR_URL=http://127.0.0.1:8080
```

或在 `~/.openclaw/openclaw.json` 中配置：

```json
{
  "env": {
    "DEEPSEEK_API_KEY": "sk-your-key-here"
  }
}
```

### 3. 注册 BrowserWing 脚本

在本项目 `bw-scripts/` 目录下包含三个 BrowserWing 脚本的 JSON 定义文件。在 BrowserWing 管理后台中：

1. 打开 BrowserWing 管理界面
2. 进入 "脚本管理"
3. 点击 "导入脚本"
4. 分别导入以下文件：
   - `bw-scripts/知乎当天的关键词文章列表（前20）.json` → ID: `dd8a7911-69b4-409f-aa14-42a7a5aeddc2`
   - `bw-scripts/知乎文章评论.json` → ID: `f3ac1d6a-0489-467f-a0eb-c275faecd839`
   - `bw-scripts/知乎发布文章.json` → ID: `8478f76d-5a6b-4fee-9155-4dbedb3a5aa4`

> **注意**：确保 BrowserWing 中知乎账号已登录，脚本才能正常工作。

### 4. 验证安装

```bash
# 检查配置文件
python3 scripts/zhihu-campaign.py --init-config

# 运行测试模式
python3 scripts/zhihu-campaign.py --dry-run --publish --product-url "https://example.com"
python3 scripts/zhihu-campaign.py --dry-run --acquire --product-url "https://example.com"
```

---

## 快速使用

### 发布文章

```bash
# 测试模式（不会实际发布）
python3 scripts/zhihu-campaign.py --publish --product-url "https://your-product.com" --dry-run

# 实际发布（自动确认，跳过人工确认）
python3 scripts/zhihu-campaign.py --publish --product-url "https://your-product.com" --auto-confirm

# 实际发布（默认需要人工确认草稿）
python3 scripts/zhihu-campaign.py --publish --product-url "https://your-product.com"
```

### 评论区获客

```bash
# 测试模式
python3 scripts/zhihu-campaign.py --acquire --product-url "https://your-product.com" --dry-run

# 实际执行（默认评论5条）
python3 scripts/zhihu-campaign.py --acquire --product-url "https://your-product.com"

# 指定评论数量
python3 scripts/zhihu-campaign.py --acquire --product-url "https://your-product.com" --max-comments 10

# 手动指定搜索关键词
python3 scripts/zhihu-campaign.py --acquire --product-url "https://your-product.com" --keywords "AI 大模型" "API 价格" "模型 选型"
```

### 完整流程（先发布后获客）

```bash
python3 scripts/zhihu-campaign.py --all --product-url "https://your-product.com" --auto-confirm
```

### 检查配置

```bash
python3 scripts/zhihu-campaign.py --init-config
```

---

## 配置参考

### 反爬配置 (`config/anti-crawl.json`)

```json
{
  "time": { "work_hours": { "start": 8, "end": 23 } },
  "rate_limits": {
    "daily": { "max_comments": 15 },
    "hourly": { "max_comments": 5 }
  },
  "delays": {
    "between_comments": { "min_seconds": 60, "max_seconds": 180 },
    "between_searches": { "min_seconds": 5, "max_seconds": 15 }
  }
}
```

### 评分筛选配置 (`config/filter.json`)

```json
{
  "scoring": {
    "heat_weight": { "weight": 40 },
    "interaction_weight": { "weight": 30 },
    "timeliness_weight": { "weight": 20 },
    "quality_weight": { "weight": 10 }
  },
  "filters": {
    "top_n": 10,
    "max_days_old": 365
  }
}
```

### 关键词种子池 (`config/keywords.json`)

30 个备选关键词，当 LLM 不可用时自动降级使用。同时记录 `last_used` 实现关键词轮换。

---

## BrowserWing 脚本

本项目包含三个 BrowserWing 浏览器自动化脚本：

| 文件 | 脚本 ID | 功能 | 需要登录 |
|------|---------|------|---------|
| `bw-scripts/知乎当天的关键词文章列表（前20）.json` | `dd8a7911-69b4-409f-aa14-42a7a5aeddc2` | 搜索当天知乎文章（按关键词，取前20条） | ✅ |
| `bw-scripts/知乎文章评论.json` | `f3ac1d6a-0489-467f-a0eb-c275faecd839` | 对指定知乎文章发表评论 | ✅ |
| `bw-scripts/知乎发布文章.json` | `8478f76d-5a6b-4fee-9155-4dbedb3a5aa4` | 在知乎发布带封面的长文 | ✅ |

### 脚本调用方式

所有脚本通过 BrowserWing REST API 调用：

```bash
POST http://127.0.0.1:8080/api/v1/scripts/{script_id}/play
Content-Type: application/json

{
  "params": {
    "keyword": "...",   # 搜索脚本
    "url": "...",       # 评论脚本
    "标题": "...",      # 发布脚本
    "正文": "...",
    "封面": "/path/to/cover.jpg"
  }
}
```

---

## 文件结构

```
zhihu-campaign/
├── README.md                       ← 本文档
├── SKILL.md                        ← 技能描述（OpenClaw Agent 路由用）
│
├── bw-scripts/                     ← BrowserWing 脚本定义（.json）
│   ├── 知乎当天的关键词文章列表（前20）.json
│   ├── 知乎文章评论.json
│   └── 知乎发布文章.json
│
├── scripts/                        ← 可执行 Python 脚本
│   ├── zhihu-campaign.py           ← 统一编排入口
│   ├── zhihu-article-publisher.py  ← 文章发布模块
│   ├── zhihu-comment-acquisition.py ← 评论区获客模块
│   └── zhihu_llm.py               ← DeepSeek API 调用封装
│
├── templates/                      ← LLM 提示词模板
│   ├── article-prompt.md           ← 文章生成提示词（七段式架构）
│   ├── comment-strategic.md        ← 评论生成提示词（四种风格）
│   └── keyword-generation.md       ← 关键词生成提示词（四种类型）
│
├── config/                         ← 运行时配置
│   ├── anti-crawl.json             ← 反爬策略
│   ├── filter.json                 ← 评分过滤规则
│   └── keywords.json               ← 关键词种子池
│
└── data/                           ← 运行时数据（自动生成）
    ├── article_draft_*.md          ← 文章草稿
    ├── published-articles.json     ← 发布历史
    ├── commented-history.json      ← 评论历史
    ├── campaign_*.log              ← 执行日志
    ├── campaign_result_*.json      ← 执行结果
    └── covers/                     ← 封面图片缓存（旧版，已迁移至 ~/zhihu_cover_images/）
```

---

## 故障排查

### LLM 调用失败

```bash
# 验证 API Key
python3 scripts/zhihu_llm.py

# 预期输出：✓ API Key 找到: sk-xxxx...xxxx
```

### BrowserWing 连接失败

```bash
# 检查 BrowserWing 状态
curl http://127.0.0.1:8080/api/v1/scripts | python3 -m json.tool | grep -E "id|name"

# 检查脚本是否注册
curl -s "http://127.0.0.1:8080/api/v1/scripts/dd8a7911-69b4-409f-aa14-42a7a5aeddc2"
```

### 搜索结果为空

- 检查 BrowserWing 中知乎是否已登录
- 尝试不同关键词
- 使用 `--dry-run` 确认配置正确

### 文章发布失败

- 检查标题是否超过 30 字
- 检查正文是否超过 4000 字
- 检查 BrowserWing 知乎登录状态
- 检查封面图片路径是否存在

### 评论未发布

- 检查是否在工作时段内（8:00-23:00）
- 检查是否达到每日/每小时上限
- 检查 BrowserWing 知乎登录状态
- 查看日志 `data/campaign_*.log` 排查

---

## License

MIT
