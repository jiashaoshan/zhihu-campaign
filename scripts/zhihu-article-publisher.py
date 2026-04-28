#!/usr/bin/env python3
"""
知乎文章发布模块
功能：
  1. 读取 article-prompt.md 模板
  2. 调用 LLM 根据产品链接生成文章
  3. 默认走人工确认流程
  4. 确认后调用 BrowserWing 脚本发布到知乎
  5. 记录发布历史到 data/published-articles.json

依赖:
  - zhihu_llm.py (LLM 调用模块)
  - BrowserWing 服务 (http://127.0.0.1:8080)
  - BrowserWing 脚本 8478f76d (文章发布)
"""

import argparse
import json
import logging
import os
import sys
import time
import random
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

# 确保可以找到同级模块
SCRIPT_DIR = Path(__file__).parent.absolute()
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from zhihu_llm import call_llm_json, get_api_key

logger = logging.getLogger(__name__)

# 路径定义
TEMPLATES_DIR = SKILL_DIR / "templates"
DATA_DIR = SKILL_DIR / "data"
ARTICLE_PROMPT_FILE = TEMPLATES_DIR / "article-prompt.md"
PUBLISHED_FILE = DATA_DIR / "published-articles.json"

# BrowserWing 配置
BROWSERWING_URL = os.environ.get("BROWSERWING_EXECUTOR_URL", "http://127.0.0.1:8080")
PUBLISH_SCRIPT_ID = "8478f76d-5a6b-4fee-9155-4dbedb3a5aa4"


# ── 数据目录 ──────────────────────────────────────────────


def ensure_data_dir():
    """确保 data 目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_template() -> str:
    """加载文章生成提示词模板"""
    if not ARTICLE_PROMPT_FILE.exists():
        raise FileNotFoundError(f"模板文件不存在: {ARTICLE_PROMPT_FILE}")
    with open(ARTICLE_PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_published_history() -> List[Dict[str, Any]]:
    """加载已发布文章历史"""
    ensure_data_dir()
    if PUBLISHED_FILE.exists():
        try:
            with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"读取发布历史失败: {e}，使用空记录")
    return []


def save_published_record(record: Dict[str, Any]):
    """保存发布记录"""
    ensure_data_dir()
    history = load_published_history()
    history.append(record)
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, ensure_ascii=False)
    logger.info(f"✓ 发布记录已保存: {PUBLISHED_FILE}")


# ── LLM 生成文章 ──────────────────────────────────────────


def generate_article(product_url: str) -> Dict[str, Any]:
    """
    调用 LLM 生成知乎文章

    Args:
        product_url: 产品链接

    Returns:
        dict: {"titles": [...], "recommended_title_index": int, "body": str}
    """
    logger.info(f"⎿ 正在为 {product_url} 生成文章...")

    template = load_template()
    prompt = template.replace("{{product_url}}", product_url)

    system_prompt = (
        "你是一位知乎内容营销专家，擅长生成高转化率的知乎长文。"
        "请严格按照输出格式返回 JSON。"
    )

    try:
        result = call_llm_json(
            system_prompt=system_prompt,
            user_prompt=prompt,
            temperature=0.8,
            max_tokens=8192,
        )

        # 验证关键字段
        if "body" not in result:
            raise ValueError("LLM 返回缺少 body 字段")
        if "titles" not in result:
            raise ValueError("LLM 返回缺少 titles 字段")

        logger.info(f"✓ 文章生成完成: {result.get('titles', ['未知'])[0][:40]}...")
        return result

    except Exception as e:
        logger.error(f"生成文章失败: {e}")
        raise


# ── 草稿管理 ──────────────────────────────────────────────


def save_draft(article_data: Dict[str, Any], product_url: str, output_path: Optional[str] = None) -> str:
    """
    将生成的文章保存为草稿文件，供人工确认

    Returns:
        str: 草稿文件路径
    """
    ensure_data_dir()

    if output_path:
        draft_path = Path(output_path)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        draft_path = DATA_DIR / f"article_draft_{timestamp}.md"

    titles = article_data.get("titles", [])
    recommended_idx = article_data.get("recommended_title_index", 0)
    body = article_data.get("body", "")

    with open(draft_path, "w", encoding="utf-8") as f:
        f.write(f"# 知乎文章草稿\n\n")
        f.write(f"## 产品链接\n{product_url}\n\n")
        f.write(f"## 标题选项\n\n")
        for i, t in enumerate(titles):
            marker = " ← 【推荐】" if i == recommended_idx else ""
            f.write(f"{i+1}. {t}{marker}\n")
        f.write(f"\n## 推荐标题\n{titles[recommended_idx] if titles else '无'}\n\n")
        f.write(f"## 正文\n\n{body}\n\n")
        f.write(f"---\n")
        f.write(f"*草稿生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n")
        f.write(f"*产品链接: {product_url}*\n")

    logger.info(f"✓ 草稿已保存: {draft_path}")
    return str(draft_path)


def confirm_publish(draft_path: str, dry_run: bool) -> bool:
    """
    人工确认是否发布文章

    Args:
        draft_path: 草稿文件路径
        dry_run: 测试模式下跳过确认

    Returns:
        bool: 是否确认发布
    """
    if dry_run:
        logger.info("[DRY-RUN] 测试模式，跳过人工确认，直接模拟发布")
        return True

    print("\n" + "=" * 60)
    print("📝 文章草稿已生成")
    print(f"   文件: {draft_path}")
    print("=" * 60)
    print()
    print("请查看草稿内容，确认是否发布到知乎。")
    print()

    while True:
        response = input("确认发布？(y/n): ").strip().lower()
        if response in ("y", "yes", "是"):
            return True
        elif response in ("n", "no", "否"):
            return False
        print("请输入 y 或 n")


# ── Pexels封面图片获取 ─────────────────────────────────


PEXELS_API_KEY = "ogysj3gEKHiYFCgRdzo7PiDGyvgxRwxPldwkiANpAOvepyHrNa9q71lR"


def extract_cover_keyword(title: str, body: str) -> str:
    """从文章标题和正文提取Pexels搜索关键词"""
    # 核心主题词
    topic_keywords = ["AI", "人工智能", "科技", "technology", "大数据"]
    for kw in topic_keywords:
        if kw.lower() in title.lower() or kw.lower() in body[:200].lower():
            return "AI technology"
    return "technology"


def fetch_pexels_cover_image(keyword: str) -> Optional[str]:
    """
    从Pexels获取封面图片，下载到本地的data/covers/目录
    Returns:
        本地图片路径或None
    """
    try:
        search_url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote(keyword)}&per_page=5&size=large&orientation=landscape"
        headers = {"Authorization": PEXELS_API_KEY}

        resp = requests.get(search_url, headers=headers, timeout=10)
        resp.raise_for_status()

        data = resp.json()
        photos = data.get("photos", [])

        if not photos:
            logger.warning("Pexels未找到相关图片，使用备用关键词")
            fallback_url = f"https://api.pexels.com/v1/search?query={urllib.parse.quote('technology')}&per_page=3&size=large"
            resp2 = requests.get(fallback_url, headers=headers, timeout=10)
            resp2.raise_for_status()
            photos = resp2.json().get("photos", [])

        if photos:
            # 随机选一张
            photo = random.choice(photos)
            image_url = photo["src"]["large"]  # 用large尺寸（合适的分辨率）

            # 下载到用户目录下的临时文件夹
            covers_dir = Path.home() / "zhihu_cover_images"
            covers_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            ext = ".jpg"
            local_path = covers_dir / f"cover_{timestamp}{ext}"

            logger.info(f"⎿ 下载封面: {image_url[:60]}...")
            img_resp = requests.get(image_url, timeout=15)
            img_resp.raise_for_status()

            with open(local_path, "wb") as f:
                f.write(img_resp.content)

            logger.info(f"✓ 封面已下载到本地: {local_path}")
            logger.info(f"  Photographer: {photo.get('photographer', 'unknown')}")
            return str(local_path)

        logger.warning("Pexels未找到任何图片")
        return None

    except Exception as e:
        logger.warning(f"Pexels封面获取失败: {e}，将不带封面发布")
        return None


# ── 发布 ────────────────────────────────────────────────


def extract_title(titles: List[str], recommended_idx: int) -> str:
    """
    从标题列表中提取最终的发布标题（<=30 字）
    """
    if recommended_idx < len(titles):
        raw = titles[recommended_idx]
    elif titles:
        raw = titles[0]
    else:
        return "无标题"

    # 去掉选项编号和推荐标记
    title = raw
    for prefix in ["选项", "Option", "标题"]:
        if title.startswith(prefix) and ":" in title:
            title = title.split(":", 1)[-1].strip()

    title = title.replace("【推荐】", "").strip()
    return title[:30]


def publish_via_browserwing(article_data: Dict[str, Any], dry_run: bool = False) -> bool:
    """
    通过 BrowserWing 将文章发布到知乎
    """
    titles = article_data.get("titles", [])
    recommended_idx = article_data.get("recommended_title_index", 0)
    body = article_data.get("body", "")

    title = extract_title(titles, recommended_idx)
    body = body[:4000]  # 截断限制

    # 获取Pexels封面图片
    cover_keyword = extract_cover_keyword(title, body)
    cover_url = fetch_pexels_cover_image(cover_keyword)

    logger.info(f"⎿ 通过 BrowserWing 发布文章")
    logger.info(f"   标题: {title}")
    logger.info(f"   正文长度: {len(body)} 字")
    logger.info(f"   封面: {cover_url or '无'}")

    if dry_run:
        logger.info(f"[DRY-RUN] 模拟发布成功")
        return True

    try:
        url = f"{BROWSERWING_URL}/api/v1/scripts/{PUBLISH_SCRIPT_ID}/play"
        payload = {
            "params": {
                "封面": cover_url or "",
                "标题": title,
                "正文": body,
            }
        }

        logger.info(f"   请求 BrowserWing: {url}")
        resp = requests.post(url, json=payload, timeout=120)
        resp.raise_for_status()

        result = resp.json()
        if result.get("result", {}).get("success"):
            logger.info(f"✓ 文章发布成功！")
            return True
        else:
            logger.error(f"BrowserWing 发布失败: {result}")
            return False

    except requests.exceptions.RequestException as e:
        logger.error(f"BrowserWing 请求失败: {e}")
        return False
    except Exception as e:
        logger.error(f"发布文章异常: {e}")
        return False


# ── 主流程 ──────────────────────────────────────────────


def run(product_url: str, output_path: Optional[str] = None, dry_run: bool = False, auto_confirm: bool = False) -> Dict[str, Any]:
    """
    执行文章发布流程

    Args:
        product_url: 产品链接
        output_path: 草稿输出路径（可选）
        dry_run: 测试模式
        auto_confirm: 自动确认发布（跳过人工确认）

    Returns:
        dict: {"success": bool, "title": str, "draft_path": str, ...}
    """
    logger.info("=" * 60)
    logger.info("知乎文章发布模块启动")
    logger.info(f"产品链接: {product_url}")
    if dry_run:
        logger.info("[DRY-RUN MODE] 测试模式，不会实际发布")
    logger.info("=" * 60)

    # 步骤1: 生成文章
    logger.info("\n【步骤1】LLM 生成文章")
    article_data = generate_article(product_url)

    # 步骤2: 保存草稿
    logger.info("\n【步骤2】保存草稿")
    draft_path = save_draft(article_data, product_url, output_path)

    # 步骤3: 人工确认
    logger.info("\n【步骤3】确认发布")
    should_publish = auto_confirm or confirm_publish(draft_path, dry_run)

    if not should_publish:
        logger.info("✗ 用户取消发布")
        return {
            "success": False,
            "reason": "cancelled",
            "draft_path": draft_path,
        }

    # 步骤4: 发布
    logger.info("\n【步骤4】发布到知乎")
    publish_success = publish_via_browserwing(article_data, dry_run)

    titles = article_data.get("titles", [])
    recommended_idx = article_data.get("recommended_title_index", 0)
    final_title = extract_title(titles, recommended_idx)

    if publish_success:
        record = {
            "timestamp": datetime.now().isoformat(),
            "product_url": product_url,
            "title": final_title,
            "body_length": len(article_data.get("body", "")),
            "published": not dry_run,
            "draft_path": draft_path,
        }
        if not dry_run:
            save_published_record(record)

        logger.info(f"\n{'=' * 60}")
        logger.info(f"✓ 文章发布完成")
        logger.info(f"   标题: {final_title}")
        logger.info(f"   草稿: {draft_path}")
        logger.info(f"{'=' * 60}")

        return {"success": True, "title": final_title, "draft_path": draft_path}

    else:
        logger.error(f"\n{'=' * 60}")
        logger.error(f"✗ 文章发布失败")
        logger.error(f"   草稿已保存: {draft_path}")
        logger.error(f"   请手动发布")
        logger.error(f"{'=' * 60}")

        return {"success": False, "reason": "publish_failed", "draft_path": draft_path}


# ── CLI ──────────────────────────────────────────────────


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="知乎文章发布模块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--product-url", required=True, help="产品链接")
    parser.add_argument("--output", help="草稿输出路径")
    parser.add_argument("--dry-run", action="store_true", help="测试模式")
    parser.add_argument("--auto-confirm", action="store_true", help="自动确认发布（跳过人工确认）")
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
            output_path=args.output,
            dry_run=args.dry_run,
            auto_confirm=args.auto_confirm,
        )
        sys.exit(0 if result["success"] else 1)
    except Exception as e:
        logger.error(f"执行失败: {e}")
        sys.exit(1)
