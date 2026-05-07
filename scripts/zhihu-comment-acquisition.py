#!/usr/bin/env python3
"""
知乎评论区获客模块
功能：
  1. 读取产品信息 → 调用 LLM 生成搜索关键词
  2. 每次从关键词池随机选 3 个，上次用过的降权
  3. 调用 BrowserWing 搜索脚本逐个搜索
  4. AI 四维评分筛选（热度40 + 互动30 + 时效20 + 内容质量10）
  5. 用 LLM 生成自然评论
  6. 串行评论：60-180s 随机延迟，每日上限15条，每小时上限5条
  7. 只 8:00-23:00 操作
  8. 记录已评论 URL 到 data/commented-history.json
  9. 同一作者最多评论 1 篇文章

依赖:
  - zhihu_llm.py (LLM 调用模块)
  - BrowserWing 服务 (http://127.0.0.1:8080)
  - BW 搜索脚本 dd8a7911
  - BW 评论脚本 f3ac1d6a
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Set, Tuple

SCRIPT_DIR = Path(__file__).parent.absolute()
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from zhihu_llm import call_llm, call_llm_json, get_api_key

logger = logging.getLogger(__name__)

# 路径
TEMPLATES_DIR = SKILL_DIR / "templates"
CONFIG_DIR = SKILL_DIR / "config"
DATA_DIR = SKILL_DIR / "data"
COMMENT_PROMPT_FILE = TEMPLATES_DIR / "comment-strategic.md"
KEYWORD_PROMPT_FILE = TEMPLATES_DIR / "keyword-generation.md"
COMMENTED_FILE = DATA_DIR / "commented-history.json"
KEYWORDS_SEED_FILE = CONFIG_DIR / "keywords.json"
ANTI_CRAWL_FILE = CONFIG_DIR / "anti-crawl.json"
FILTER_FILE = CONFIG_DIR / "filter.json"

# BrowserWing
BROWSERWING_URL = os.environ.get("BROWSERWING_EXECUTOR_URL", "http://127.0.0.1:8080")
SEARCH_SCRIPT_ID = "dd8a7911-69b4-409f-aa14-42a7a5aeddc2"
COMMENT_SCRIPT_ID = "f3ac1d6a-0489-467f-a0eb-c275faecd839"


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class Article:
    """知乎文章数据结构"""
    title: str
    url: str
    author: str = ""
    heat: int = 0
    vote_count: int = 0
    comment_count: int = 0
    published_at: str = ""
    content: str = ""
    score: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "heat": self.heat,
            "vote_count": self.vote_count,
            "comment_count": self.comment_count,
            "published_at": self.published_at,
            "content": self.content[:300] if self.content else "",
            "score": self.score,
        }


@dataclass
class CommentRecord:
    """评论记录"""
    url: str
    author: str
    title: str
    comment: str
    timestamp: str


# ── 配置 ──────────────────────────────────────────────────


def load_json_config(path: Path, default: dict) -> dict:
    """加载 JSON 配置文件"""
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载配置失败 {path}: {e}")
    return default


def load_anti_crawl_config() -> dict:
    return load_json_config(ANTI_CRAWL_FILE, {
        "time": {"work_hours": {"start": 8, "end": 23}},
        "rate_limits": {"daily": {"max_comments": 15}, "hourly": {"max_comments": 5},
                        "per_article_author": {"max_comments": 1}},
        "delays": {"between_comments": {"min_seconds": 60, "max_seconds": 180},
                   "between_searches": {"min_seconds": 5, "max_seconds": 15},
                   "between_keywords": {"min_seconds": 10, "max_seconds": 30}},
        "retry": {"max_attempts": 3, "base_delay_seconds": 30},
    })


# ── 时间守护 ──────────────────────────────────────────────


def is_work_hours(ac_config: dict) -> bool:
    """检查当前是否在工作时段内"""
    now = datetime.now()
    ac_time = ac_config.get("time", {})
    work_hours = ac_time.get("work_hours", {"start": 8, "end": 23})
    start = work_hours.get("start", 8)
    end = work_hours.get("end", 23)

    if start <= now.hour < end:
        return True
    logger.warning(f"当前时间 {now.hour}:00 不在工作时段 ({start}:00-{end}:00)，跳过")
    return False


def check_daily_hourly_limits(ac_config: dict, history: List[CommentRecord]) -> Tuple[bool, str]:
    """检查每日/每小时评论上限"""
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    this_hour_str = now.strftime("%Y-%m-%d %H:00")

    daily_max = ac_config.get("rate_limits", {}).get("daily", {}).get("max_comments", 15)
    hourly_max = ac_config.get("rate_limits", {}).get("hourly", {}).get("max_comments", 5)

    today_count = sum(1 for r in history if r.timestamp.startswith(today_str))
    hour_count = sum(1 for r in history if r.timestamp.startswith(this_hour_str))

    if today_count >= daily_max:
        return False, f"今日已评论 {today_count} 条，上限 {daily_max}"
    if hour_count >= hourly_max:
        return False, f"本小时已评论 {hour_count} 条，上限 {hourly_max}"

    return True, f"今日 {today_count}/{daily_max}，本小时 {hour_count}/{hourly_max}"


# ── 关键词管理 ────────────────────────────────────────────


class KeywordManager:
    """关键词管理：种子池 + LLM 动态生成 + 轮换"""

    def __init__(self, product_url: str):
        self.product_url = product_url
        self.llm_keywords: List[str] = []
        self.seed_keywords: List[str] = []
        self._load_seeds()
        self._used_keywords: List[str] = []

    def _load_seeds(self):
        """加载关键词种子池"""
        config = load_json_config(KEYWORDS_SEED_FILE, {"keywords": []})
        self.seed_keywords = config.get("keywords", [])
        self._used_keywords = config.get("last_used", [])

    def _save_used(self):
        """保存已使用关键词（用于下次权重调整）"""
        config = load_json_config(KEYWORDS_SEED_FILE, {"keywords": self.seed_keywords})
        config["last_used"] = self._used_keywords[-20:]  # 只保留最近20个
        try:
            with open(KEYWORDS_SEED_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存关键词使用记录失败: {e}")

    def _llm_generate_keywords(self) -> List[str]:
        """调用 LLM 生成关键词"""
        logger.info("⎿ LLM 生成搜索关键词...")

        if not KEYWORD_PROMPT_FILE.exists():
            logger.warning("关键词提示词模板不存在，使用种子池")
            return []

        try:
            with open(KEYWORD_PROMPT_FILE, "r", encoding="utf-8") as f:
                template = f.read()

            prompt = template.replace("{{product_url}}", self.product_url)

            system_prompt = "你是一位知乎内容策略分析师。请生成搜索关键词，返回 JSON 格式。"

            result = call_llm_json(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.7,
                max_tokens=2048,
            )

            keywords = result.get("keywords", [])
            if isinstance(keywords, list) and len(keywords) > 0:
                logger.info(f"✓ LLM 生成 {len(keywords)} 个关键词")
                return keywords
            else:
                logger.warning("LLM 返回的关键词列表为空")
                return []

        except Exception as e:
            logger.warning(f"LLM 生成关键词失败: {e}，使用种子池")
            return []

    def get_keywords_for_round(self, count: int = 3) -> List[str]:
        """
        获取本次使用的关键词列表
        策略：优先 LLM 生成，不足时从种子池补充；已用过的降权
        """
        if not self.llm_keywords:
            self.llm_keywords = self._llm_generate_keywords()

        pool = []

        # 添加 LLM 生成的关键词
        for kw in self.llm_keywords:
            if kw not in self._used_keywords:
                pool.append(kw)

        # 如果不够，从种子池补充（排除已用的）
        shuffled_seeds = list(self.seed_keywords)
        random.shuffle(shuffled_seeds)
        for kw in shuffled_seeds:
            if kw not in self._used_keywords and kw not in pool:
                pool.append(kw)

        # 如果还不够，从已用的里面再选
        if len(pool) < count:
            remaining = [kw for kw in self.llm_keywords + self.seed_keywords if kw not in pool]
            pool.extend(remaining)

        # 随机选取
        if len(pool) <= count:
            selected = pool[:]
        else:
            selected = random.sample(pool, count)

        self._used_keywords.extend(selected)
        self._save_used()

        logger.info(f"✓ 选定关键词: {selected}")
        return selected


# ── 搜索 ──────────────────────────────────────────────────


class ZhihuSearcher:
    """知乎搜索模块——使用 BrowserWing"""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.ac_config = load_anti_crawl_config()
        self.delays = self.ac_config.get("delays", {}).get("between_searches",
                                                           {"min_seconds": 5, "max_seconds": 15})

    def search(self, keyword: str, limit: int = 20) -> List[Article]:
        """搜索关键词返回文章列表"""
        logger.info(f"⎿ 搜索: \"{keyword}\" (limit={limit})")

        if self.dry_run:
            logger.info(f"[DRY-RUN] 模拟搜索: {keyword}")
            return self._mock_search(keyword, limit)

        try:
            import requests
            url = f"{BROWSERWING_URL}/api/v1/scripts/{SEARCH_SCRIPT_ID}/play"
            payload = {"params": {"keyword": keyword}}

            logger.debug(f"   请求 BW: {url}")
            resp = requests.post(url, json=payload, timeout=60)
            resp.raise_for_status()

            result = resp.json()
            if not result.get("result", {}).get("success"):
                logger.warning(f"   搜索脚本执行失败: {result.get('result', {}).get('message','')}")
                return self._mock_search(keyword, limit)

            articles_data = result.get("result", {}).get("extracted_data", {}).get("ai_data_1", [])
            articles = []

            for item in articles_data[:limit]:
                try:
                    upvotes_str = re.sub(r'\D', '', item.get("upvotes", "0"))
                    vote_count = int(upvotes_str) if upvotes_str else 0

                    comments_str = re.sub(r'\D', '', item.get("comments", "0"))
                    comment_count = int(comments_str) if comments_str else 0

                    url_raw = item.get("url", "")
                    # 确保 URL 完整
                    if url_raw and not url_raw.startswith("http"):
                        url_raw = f"https://www.zhihu.com{url_raw}" if url_raw.startswith("/") else url_raw

                    article = Article(
                        title=item.get("title", ""),
                        url=url_raw,
                        vote_count=vote_count,
                        comment_count=comment_count,
                        heat=vote_count * 10,  # 用赞同数估算热度
                        published_at=item.get("time", ""),
                        content=item.get("title", ""),
                    )
                    articles.append(article)
                except Exception as e:
                    logger.warning(f"   解析文章失败: {e}")

            logger.info(f"✓ 搜索到 {len(articles)} 篇文章")
            return articles

        except requests.exceptions.RequestException as e:
            logger.error(f"BrowserWing 搜索请求失败: {e}")
            return self._mock_search(keyword, limit)
        except Exception as e:
            logger.error(f"搜索异常: {e}")
            return self._mock_search(keyword, limit)

    def _mock_search(self, keyword: str, limit: int) -> List[Article]:
        """模拟搜索（降级方案）"""
        mock_titles = [
            f"{keyword}入门指南：从零开始的学习路径",
            f"深入理解{keyword}的核心原理",
            f"{keyword}在实际项目中的应用案例",
            f"{keyword}的未来发展趋势分析",
            f"如何快速上手{keyword}？",
            f"{keyword}常见问题汇总与解决方案",
            f"为什么大家都在用{keyword}？",
            f"{keyword}与竞品的详细对比",
            f"2025年{keyword}最佳实践",
            f"从零到一：{keyword}实战教程",
        ]

        articles = []
        now = datetime.now()
        for i, t in enumerate(mock_titles[:limit]):
            random_votes = random.randint(10, 500)
            random_comments = random.randint(0, 80)
            days_ago = random.randint(0, 180)
            pub = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+08:00")

            articles.append(Article(
                title=t,
                url=f"https://zhuanlan.zhihu.com/p/{100000 + i}",
                author=f"作者_{random.choice(['A','B','C','D'])}",
                heat=random_votes * random.randint(5, 30),
                vote_count=random_votes,
                comment_count=random_comments,
                published_at=pub,
                content=t + "\n\n这是一篇关于" + keyword + "的详细文章内容，包含了实际案例分析和操作步骤。" * 30,
            ))

        logger.info(f"✓ [MOCK] 生成 {len(articles)} 篇文章")
        return articles[:limit]


# ── 评分筛选 ──────────────────────────────────────────────


class ArticleScorer:
    """AI 四维评分筛选"""

    def __init__(self):
        self.filter_config = load_json_config(FILTER_FILE, {
            "scoring": {
                "heat_weight": {"weight": 40},
                "interaction_weight": {"weight": 30},
                "timeliness_weight": {"weight": 20},
                "quality_weight": {"weight": 10},
            },
            "filters": {"top_n": 10, "max_days_old": 365}
        })

    def score(self, article: Article) -> float:
        """四维评分（0-100）"""
        scoring = self.filter_config.get("scoring", {})

        # 1. 热度（0-40）
        hw = scoring.get("heat_weight", {}).get("weight", 40)
        heat_score = min(article.heat / 10000 * hw / 40, hw) if hw > 0 else 0

        # 2. 互动（0-30）
        iw = scoring.get("interaction_weight", {}).get("weight", 30)
        interaction_score = min(
            (article.vote_count + article.comment_count * 2) / 100 * iw / 30, iw
        ) if iw > 0 else 0

        # 3. 时效（0-20）
        tw = scoring.get("timeliness_weight", {}).get("weight", 20)
        time_score = tw
        if article.published_at:
            try:
                pub = datetime.fromisoformat(article.published_at.replace("Z", "+00:00"))
                days_old = (datetime.now() - pub).days
                if days_old > 30 and tw > 0:
                    time_score = max(0, tw - (days_old - 30) * tw / 180)
            except Exception:
                pass
        time_score = max(0, min(time_score, tw))

        # 4. 质量（0-10）
        qw = scoring.get("quality_weight", {}).get("weight", 10)
        content_len = len(article.content or "")
        quality_score = min(content_len / 200 * qw / 10, qw) if qw > 0 else 0

        total = heat_score + interaction_score + time_score + quality_score
        return round(total, 2)

    def filter_top(self, articles: List[Article]) -> List[Article]:
        """评分并返回 Top N"""
        top_n = self.filter_config.get("filters", {}).get("top_n", 10)
        max_days = self.filter_config.get("filters", {}).get("max_days_old", 365)
        min_score = self.filter_config.get("filters", {}).get("min_score", 0)

        for a in articles:
            a.score = self.score(a)

        # 时效性过滤
        filtered = []
        for a in articles:
            if a.published_at:
                try:
                    pub = datetime.fromisoformat(a.published_at.replace("Z", "+00:00"))
                    days_old = (datetime.now() - pub).days
                    if days_old > max_days:
                        continue
                except Exception:
                    pass
            filtered.append(a)

        filtered.sort(key=lambda x: x.score, reverse=True)
        result = [a for a in filtered if a.score >= min_score][:top_n]

        logger.info(f"✓ 评分筛选完成: {len(filtered)} 篇 → Top {len(result)} 篇")
        for i, a in enumerate(result[:5], 1):
            logger.info(f"  [{i}] {a.title[:35]:35s} score={a.score:5.1f} votes={a.vote_count}")
        if len(result) > 5:
            logger.info(f"  ... 还有 {len(result)-5} 篇")

        return result


# ── 评论生成 ──────────────────────────────────────────────


class CommentGenerator:
    """基于 LLM 的评论生成"""

    def __init__(self, product_url: str, product_info: Optional[str] = None):
        self.product_url = product_url
        self.product_info = product_info or product_url

    def generate(self, article: Article) -> str:
        """生成自然评论"""
        prompt_template = ""
        if COMMENT_PROMPT_FILE.exists():
            with open(COMMENT_PROMPT_FILE, "r", encoding="utf-8") as f:
                prompt_template = f.read()

        if not prompt_template:
            return self._fallback_comment(article)

        prompt = (
            prompt_template
            .replace("{{product_info}}", self.product_info)
            .replace("{{article_title}}", article.title or "")
            .replace("{{article_summary}}", (article.content or article.title)[:500])
        )

        system_prompt = (
            "你是一位知乎资深用户。请生成一条自然、口语化的评论。"
            "产品链接要放在正文中间自然融入，不要单独一行或末尾。"
            "直接输出评论文本，不要任何标记、引号或前缀。"
        )

        try:
            comment = call_llm(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.8,
                max_tokens=512,
            )

            # 清理
            comment = comment.strip().strip('"').strip("'").strip()
            if len(comment) < 10:
                return self._fallback_comment(article)
            comment = comment[:200]  # 截断过长评论
            logger.info(f"✓ LLM 生成评论: {comment[:60]}...")
            return comment

        except Exception as e:
            logger.warning(f"LLM 生成评论失败: {e}，使用兜底评论")
            return self._fallback_comment(article)

    def _fallback_comment(self, article: Article) -> str:
        """兜底评论"""
        fallbacks = [
            f"写得不错，很有启发。我之前也遇到过类似的问题，后来用了些新方法才解决。",
            f"感谢分享，分析得很到位。特别是关于{article.title[:10]}的部分，确实是这样。",
            f"作者用心了，内容很扎实。想请教一下，在实际落地的时候有没有什么坑？",
            f"这个角度很新颖，我之前完全没想过。实践出真知，说得对。",
            f"收藏了，慢慢看。最近正在研究这个方向，您的经验很有参考价值。",
        ]
        return random.choice(fallbacks)


# ── 评论器（BrowserWing）─────────────────────────────────


class ZhihuCommenter:
    """知乎评论执行器"""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.history: List[CommentRecord] = self._load_history()
        self.ac_config = load_anti_crawl_config()
        self.author_max = (
            self.ac_config
            .get("rate_limits", {})
            .get("per_article_author", {})
            .get("max_comments", 1)
        )
        self.commented_authors: Set[str] = set(r.author for r in self.history if r.author)

    def _load_history(self) -> List[CommentRecord]:
        """加载已评论历史"""
        ensure_data_dir()
        if COMMENTED_FILE.exists():
            try:
                with open(COMMENTED_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return [CommentRecord(**r) for r in data if isinstance(r, dict)]
            except Exception as e:
                logger.warning(f"加载评论历史失败: {e}")
        return []

    def _save_history(self):
        """保存评论历史"""
        ensure_data_dir()
        data = [
            {
                "url": r.url,
                "author": r.author,
                "title": r.title,
                "comment": r.comment,
                "timestamp": r.timestamp,
            }
            for r in self.history
        ]
        with open(COMMENTED_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def is_commented(self, url: str) -> bool:
        """是否已评论过该文章"""
        return any(r.url == url for r in self.history)

    def is_author_commented(self, author: str) -> bool:
        """该作者是否已被评论过"""
        if not author:
            return False
        return author in self.commented_authors

    def post_comment(self, article: Article, comment: str) -> Tuple[bool, str]:
        """
        发表评论

        Returns:
            (success: bool, message: str)
        """
        # 检查是否已评论过
        if self.is_commented(article.url):
            return False, "已评论过该文章（URL 去重）"

        # 作者去重
        if article.author and self.is_author_commented(article.author):
            return False, f"已评论该作者 [{article.author}] 的文章（作者去重）"

        # 检查限额
        ok, msg = check_daily_hourly_limits(self.ac_config, self.history)
        if not ok:
            return False, msg

        if self.dry_run:
            logger.info(f"[DRY-RUN] 将评论: {article.title[:40]}")
            logger.info(f"[DRY-RUN] 内容: {comment[:80]}...")
            # dry run 也记录
            self._record_comment(article, comment)
            return True, "模拟评论成功（DRY-RUN）"

        logger.info(f"⎿ 发表评论: {article.title[:40]}...")
        try:
            import requests
            url = f"{BROWSERWING_URL}/api/v1/scripts/{COMMENT_SCRIPT_ID}/play"
            payload = {"params": {"url": article.url, "评论": comment}}

            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()

            result = resp.json()
            if result.get("result", {}).get("success"):
                self._record_comment(article, comment)
                logger.info(f"✓ 评论成功: {article.title[:40]}")
                return True, "评论成功"
            else:
                err_msg = result.get("result", {}).get("message", "未知错误")
                logger.warning(f"✗ 评论失败: {err_msg}")
                return False, f"评论失败: {err_msg}"

        except requests.exceptions.RequestException as e:
            logger.error(f"BrowserWing 请求失败: {e}")
            return False, f"网络错误: {e}"
        except Exception as e:
            logger.error(f"评论异常: {e}")
            return False, f"异常: {e}"

    def _record_comment(self, article: Article, comment: str):
        """记录评论"""
        record = CommentRecord(
            url=article.url,
            author=article.author or "",
            title=article.title or "",
            comment=comment,
            timestamp=datetime.now().isoformat(),
        )
        self.history.append(record)
        if article.author:
            self.commented_authors.add(article.author)
        self._save_history()


# ── 工具函数 ──────────────────────────────────────────────


def ensure_data_dir():
    """确保 data 目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def wait_random(min_s: int = 60, max_s: int = 180, reason: str = ""):
    """随机延迟"""
    delay = random.randint(min_s, max_s)
    if reason:
        logger.info(f"⏳ {reason}，等待 {delay}s...")
    else:
        logger.info(f"⏳ 等待 {delay}s...")
    time.sleep(delay)


def extract_author_from_url(url: str) -> str:
    """从 URL 尝试提取作者（简单启发式，能获取则获取，不能则返回空）"""
    # 知乎专栏 URL 格式: https://zhuanlan.zhihu.com/p/xxx
    # 知乎回答 URL: https://www.zhihu.com/question/xxx/answer/xxx
    # 不在这里做复杂提取，留给搜索脚本人
    return ""


# ── 主流程 ──────────────────────────────────────────────


def run(product_url: str, product_info: Optional[str] = None,
        dry_run: bool = False, max_comments: int = 5,
        keywords: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    执行评论区获客流程

    Args:
        product_url: 产品链接
        product_info: 产品信息描述（可选）
        dry_run: 测试模式
        max_comments: 本次最大评论数
        keywords: 指定关键词列表（可选，否则 LLM 生成）

    Returns:
        dict: 执行结果统计
    """
    logger.info("=" * 60)
    logger.info("知乎评论区获客模块启动")
    logger.info(f"产品: {product_url}")
    if dry_run:
        logger.info("[DRY-RUN MODE] 测试模式，不会实际发表评论")
    logger.info("=" * 60)

    ac_config = load_anti_crawl_config()
    searcher = ZhihuSearcher(dry_run=dry_run)
    scorer = ArticleScorer()
    commenter = ZhihuCommenter(dry_run=dry_run)
    comment_gen = CommentGenerator(product_url, product_info)

    # 时间检查
    if not is_work_hours(ac_config):
        return {"success": False, "reason": "outside_work_hours", "stats": {}}

    # 生成/获取关键词
    if keywords:
        search_keywords = keywords
        logger.info(f"使用指定关键词: {search_keywords}")
    else:
        kw_manager = KeywordManager(product_url)
        search_keywords = kw_manager.get_keywords_for_round(count=3)

    debug_info = []
    all_articles_found = 0
    comments_posted = 0
    comments_failed = 0
    comments_skipped = 0

    # 搜索 & 筛选
    for kw in search_keywords:
        logger.info(f"\n{'─' * 50}")
        logger.info(f"【搜索】关键词: {kw}")

        articles = searcher.search(kw)
        all_articles_found += len(articles)

        if not articles:
            logger.info(f"   关键词 '{kw}' 无结果")
            continue

        # 评分筛选 Top N
        top_articles = scorer.filter_top(articles)

        if not top_articles:
            continue

        # 遍历高价值文章，尝试评论
        rated_count = 0
        for article in top_articles:
            if comments_posted >= max_comments:
                logger.info(f"已达到本次最大评论数 ({max_comments})，停止")
                break

            # URL 去重
            if commenter.is_commented(article.url):
                logger.info(f"   跳过（URL 已评论）: {article.title[:40]}")
                comments_skipped += 1
                continue

            # 作者去重
            if article.author and commenter.is_author_commented(article.author):
                logger.info(f"   跳过（作者 {article.author} 已评论过）: {article.title[:40]}")
                comments_skipped += 1
                continue

            # 检查限额
            ok, limit_msg = check_daily_hourly_limits(ac_config, commenter.history)
            if not ok:
                logger.warning(f"限额已达: {limit_msg}")
                break

            # 生成评论
            comment = comment_gen.generate(article)

            # 发表评论
            success, msg = commenter.post_comment(article, comment)

            if success:
                comments_posted += 1
                debug_info.append({
                    "keyword": kw,
                    "title": article.title,
                    "url": article.url,
                    "author": article.author,
                    "score": article.score,
                    "comment": comment[:80],
                    "status": "posted",
                })
            else:
                comments_failed += 1
                debug_info.append({
                    "keyword": kw,
                    "title": article.title,
                    "url": article.url,
                    "author": article.author,
                    "score": article.score,
                    "status": f"failed: {msg}",
                })

            rated_count += 1

            # 评论间隔
            if comments_posted < max_comments:
                delays = ac_config.get("delays", {}).get("between_comments",
                                                         {"min_seconds": 60, "max_seconds": 180})
                wait_random(delays["min_seconds"], delays["max_seconds"],
                            reason="评论间隔")

        # 关键词间隔
        if comments_posted < max_comments:
            delays = ac_config.get("delays", {}).get("between_keywords",
                                                     {"min_seconds": 10, "max_seconds": 30})
            wait_random(delays["min_seconds"], delays["max_seconds"],
                        reason="切换关键词")

    # 汇总
    total_history = len(commenter.history)

    stats = {
        "keywords_used": len(search_keywords),
        "articles_found": all_articles_found,
        "comments_posted": comments_posted,
        "comments_failed": comments_failed,
        "comments_skipped": comments_skipped,
        "total_history": total_history,
        "dry_run": dry_run,
        "max_comments_per_run": max_comments,
    }

    logger.info("\n" + "=" * 60)
    logger.info("执行完成")
    logger.info("=" * 60)
    logger.info(f"  搜索关键词: {len(search_keywords)} 个")
    logger.info(f"  找到文章:   {all_articles_found} 篇")
    logger.info(f"  评论成功:   {comments_posted} 条")
    logger.info(f"  评论失败:   {comments_failed} 条")
    logger.info(f"  跳过(去重): {comments_skipped} 条")
    logger.info(f"  历史总计:   {total_history} 条")
    logger.info(f"  模式:       {'DRY-RUN' if dry_run else 'LIVE'}")

    return {
        "success": True,
        "stats": stats,
        "details": debug_info,
    }


# ── CLI ──────────────────────────────────────────────────


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="知乎评论区获客模块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--product-url", required=True, help="产品链接")
    parser.add_argument("--product-info", help="产品信息描述（可选）")
    parser.add_argument("--max-comments", type=int, default=5, help="本次最大评论数")
    parser.add_argument("--dry-run", action="store_true", help="测试模式")
    parser.add_argument("--keywords", nargs="+", help="手动指定搜索关键词")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler()],
    )

    try:
        result = run(
            product_url=args.product_url,
            product_info=args.product_info,
            dry_run=args.dry_run,
            max_comments=args.max_comments,
            keywords=args.keywords,
        )
        if result["success"]:
            sys.exit(0)
        else:
            sys.exit(1)
    except Exception as e:
        logger.error(f"执行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
