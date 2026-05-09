---
name: zhihu-campaign
description: |
  知乎获客全技能
  功能：文章发布 + 评论区获客
  基于 BrowserWing 实现浏览器自动化 + DeepSeek API 驱动 AI 内容生成
metadata:
  openclaw:
    emoji: "📢"
    requires:
      env: ["BROWSERWING_EXECUTOR_URL", "DEEPSEEK_API_KEY"]
    category: "acquisition"
    tags: ["zhihu", "acquisition", "publish", "comment", "browserwing", "ai"]
---

# 知乎获客技能 (zhihu-campaign)

AI 驱动的知乎获客全自动解决方案。两个核心能力：

## 功能矩阵

| 功能 | 说明 | 关联脚本 | BW 脚本 ID |
|------|------|----------|------------|
| 📝 发布文章 | LLM 生成 → 人工确认 → BW 发布 | `zhihu-article-publisher.py` | `8478f76d` |
| 🎯 评论区获客 | 搜索 → AI评分 → LLM评论 → BW 评论 | `zhihu-comment-acquisition.py` | `dd8a7911`, `f3ac1d6a` |

## 依赖

- Python 3.8+
- BrowserWing 服务（默认 `http://127.0.0.1:8080`）
- DeepSeek API Key（环境变量 `DEEPSEEK_API_KEY`）
- BrowserWing 注册脚本：
  - `8478f76d` — 知乎文章发布
  - `f3ac1d6a` — 知乎文章评论
  - `dd8a7911` — 当天关键词搜索

## 快速使用

```bash
# 配置文件
python3 scripts/zhihu-campaign.py --init-config

# 测试模式
python3 scripts/zhihu-campaign.py --dry-run --acquire --product-url "https://example.com"

# 发布文章
python3 scripts/zhihu-campaign.py --publish --product-url "https://example.com"

# 评论区获客
python3 scripts/zhihu-campaign.py --acquire --product-url "https://example.com"

# 完整流程
python3 scripts/zhihu-campaign.py --all --product-url "https://example.com"
```

## 文件结构

```
zhihu-campaign/
├── SKILL.md                     ← 本文
├── README.md                    ← 完整设计说明
├── templates/                   ← LLM 提示词模板
│   ├── article-prompt.md
│   ├── comment-strategic.md
│   └── keyword-generation.md
├── config/                      ← 配置
│   ├── keywords.json
│   ├── filter.json
│   └── anti-crawl.json
├── scripts/
│   ├── zhihu-campaign.py             ← 统一编排入口
│   ├── zhihu-article-publisher.py    ← 文章发布模块
│   ├── zhihu-comment-acquisition.py  ← 评论区获客模块
│   └── zhihu_llm.py                  ← LLM API 封装
└── data/                              ← 运行时数据（自动创建）
```
