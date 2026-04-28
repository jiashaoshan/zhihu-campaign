#!/usr/bin/env python3
"""
知乎获客技能 (zhihu-campaign) — 统一编排入口

功能:
  1. 发布文章：根据产品链接生成知乎文章并发布
  2. 评论区获客：搜索文章 → AI筛选 → 生成评论 → 自动评论

命令行:
  python3 zhihu-campaign.py --publish --product-url "https://..."
  python3 zhihu-campaign.py --acquire --product-url "https://..."
  python3 zhihu-campaign.py --all --product-url "https://..."
  python3 zhihu-campaign.py --init-config
  python3 zhihu-campaign.py --dry-run --publish --product-url "https://..."
  python3 zhihu-campaign.py --dry-run --acquire --product-url "https://..."
"""

import argparse
import json
import logging
import sys
import os
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.absolute()
SKILL_DIR = SCRIPT_DIR.parent
DATA_DIR = SKILL_DIR / "data"

# 确保 data 目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 日志配置
log_file = DATA_DIR / f"campaign_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(log_file), encoding="utf-8"),
    ],
)
logger = logging.getLogger("zhihu-campaign")


def banner():
    """打印启动横幅"""
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║    知乎获客技能 (zhihu-campaign)     ║")
    print("  ║     文章发布 · 评论区获客            ║")
    print("  ╚══════════════════════════════════════╝")
    print()


def init_config():
    """初始化配置文件"""
    template_dir = SKILL_DIR / "templates"
    config_dir = SKILL_DIR / "config"

    files = {
        "模板": [
            template_dir / "article-prompt.md",
            template_dir / "comment-strategic.md",
            template_dir / "keyword-generation.md",
        ],
        "配置": [
            config_dir / "keywords.json",
            config_dir / "filter.json",
            config_dir / "anti-crawl.json",
        ],
    }

    print("=" * 60)
    print("📋 知乎获客技能 — 配置文件清单")
    print("=" * 60)

    for category, paths in files.items():
        print(f"\n【{category}】")
        for p in paths:
            status = "✓" if p.exists() else "✗ (缺失)"
            print(f"  {status} {p}")

    print(f"\n  数据目录: {DATA_DIR / '...'}")

    # 检查 DEEPSEEK_API_KEY
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if api_key:
        print(f"\n  ✓ DEEPSEEK_API_KEY: {api_key[:8]}...{api_key[-4:]}")
    else:
        # 检查 openclaw.json
        openclaw_config = Path.home() / ".openclaw" / "openclaw.json"
        found = False
        if openclaw_config.exists():
            try:
                with open(openclaw_config, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                env = cfg.get("env", {})
                if isinstance(env, dict):
                    for k, v in env.items():
                        if "deepseek" in k.lower() and isinstance(v, str) and v.strip():
                            print(f"\n  ✓ DEEPSEEK_API_KEY (from openclaw.json): {v[:8]}...{v[-4:]}")
                            found = True
                            break
            except Exception:
                pass
        if not found:
            print(f"\n  ⚠ DEEPSEEK_API_KEY 未配置!")
            print(f"     请设置环境变量: export DEEPSEEK_API_KEY=sk-xxx")
            print(f"     或在 ~/.openclaw/openclaw.json 的 env 中添加")

    print()
    print("  使用 --dry-run 可以测试配置是否生效。")


def main():
    banner()

    parser = argparse.ArgumentParser(
        description="知乎获客技能 — 统一编排入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 初始化配置
  python3 zhihu-campaign.py --init-config

  # 发布文章（默认人工确认）
  python3 zhihu-campaign.py --publish --product-url "https://example.com/product"

  # 评论区获客（测试模式）
  python3 zhihu-campaign.py --acquire --product-url "https://example.com/product" --dry-run

  # 先发布后获客（完整流程）
  python3 zhihu-campaign.py --all --product-url "https://example.com/product"

  # 指定评论数
  python3 zhihu-campaign.py --acquire --product-url "https://..." --max-comments 10

  # 使用更多关键词
  python3 zhihu-campaign.py --acquire --product-url "https://..." --keywords "AI 大模型" "API 价格"
        """,
    )

    # 操作模式（互斥）
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--publish", action="store_true", help="只执行发布文章")
    mode_group.add_argument("--acquire", action="store_true", help="只执行评论区获客")
    mode_group.add_argument("--all", action="store_true", dest="do_all",
                            help="先发布文章，后评论区获客")

    # 通用参数
    parser.add_argument("--product-url", help="产品链接")
    parser.add_argument("--dry-run", action="store_true", help="测试模式（不会实际操作）")
    parser.add_argument("--init-config", action="store_true", help="初始化配置文件")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")

    # 评论区获客参数
    parser.add_argument("--max-comments", type=int, default=5, help="本次最大评论数 (默认 5)")
    parser.add_argument("--keywords", nargs="+", help="手动指定搜索关键词")

    # 文章发布参数
    parser.add_argument("--auto-confirm", action="store_true",
                        help="发布文章时自动确认（跳过人工确认）")
    parser.add_argument("--output", help="文章草稿输出路径")

    args = parser.parse_args()

    if not any([args.publish, args.acquire, args.do_all, args.init_config]):
        parser.print_help()
        print("\n⚠ 请指定操作模式: --publish / --acquire / --all / --init-config")
        sys.exit(1)

    # 日志级别
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 初始化配置
    if args.init_config:
        init_config()
        sys.exit(0)

    # 验证 product-url
    if not args.product_url:
        parser.error("使用 --publish / --acquire / --all 时必须指定 --product-url")

    result = {
        "publish": None,
        "acquire": None,
        "timestamp": datetime.now().isoformat(),
        "product_url": args.product_url,
        "dry_run": args.dry_run,
    }

    try:
        # ─ 发布文章 ─
        if args.publish or args.do_all:
            logger.info("=" * 60)
            logger.info("📝 模块一：知乎文章发布")
            logger.info("=" * 60)

            # 加载脚本模块（文件名含连字符，直接用绝对路径）
            import importlib.util
            pub_path = SCRIPT_DIR / 'zhihu-article-publisher.py'
            pub_spec = importlib.util.spec_from_file_location('zhihu_article_publisher', str(pub_path))
            pub_mod = importlib.util.module_from_spec(pub_spec)
            pub_spec.loader.exec_module(pub_mod)
            publish_run = pub_mod.run

            pub_result = publish_run(
                product_url=args.product_url,
                output_path=args.output,
                dry_run=args.dry_run,
                auto_confirm=args.auto_confirm,
            )
            result["publish"] = pub_result

            if not pub_result.get("success") and args.do_all:
                logger.warning("文章发布失败，评论区获客继续执行")

        # ─ 评论区获客 ─
        if args.acquire or args.do_all:
            logger.info("=" * 60)
            logger.info("🎯 模块二：知乎评论区获客")
            logger.info("=" * 60)

            # 加载脚本模块（文件名含连字符，直接用绝对路径）
            import importlib.util
            acq_path = SCRIPT_DIR / 'zhihu-comment-acquisition.py'
            acq_spec = importlib.util.spec_from_file_location('zhihu_comment_acquisition', str(acq_path))
            acq_mod = importlib.util.module_from_spec(acq_spec)
            acq_spec.loader.exec_module(acq_mod)
            acquire_run = acq_mod.run

            acq_result = acquire_run(
                product_url=args.product_url,
                product_info=args.product_url,
                dry_run=args.dry_run,
                max_comments=args.max_comments,
                keywords=args.keywords,
            )
            result["acquire"] = acq_result

    except Exception as e:
        logger.exception(f"执行异常: {e}")
        result["error"] = str(e)

    # ─ 汇总 ─
    logger.info("\n" + "=" * 60)
    logger.info("📊 执行汇总")
    logger.info("=" * 60)

    if result.get("publish"):
        pr = result["publish"]
        status = "✓" if pr.get("success") else "✗"
        reason = pr.get("reason", "")
        title = pr.get("title", "")
        logger.info(f"  {status} 文章发布: {reason or '成功'}  {title[:40] if title else ''}")

    if result.get("acquire"):
        ar = result["acquire"]
        stats = ar.get("stats", {})
        if stats:
            logger.info(f"  ✓ 评论区获客:")
            logger.info(f"     关键词: {stats.get('keywords_used', 0)} 个")
            logger.info(f"     找到文章: {stats.get('articles_found', 0)} 篇")
            logger.info(f"     评论成功: {stats.get('comments_posted', 0)} 条")
            logger.info(f"     评论失败: {stats.get('comments_failed', 0)} 条")
            logger.info(f"     去重跳过: {stats.get('comments_skipped', 0)} 条")
            logger.info(f"     历史总计: {stats.get('total_history', 0)} 条")

    logger.info(f"  模式: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"  日志: {log_file}")
    logger.info("=" * 60)

    # 保存结果
    result_file = DATA_DIR / f"campaign_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        # 简化输出，避免过长
        simple_result = json.loads(json.dumps(result, default=str))
        json.dump(simple_result, f, indent=2, ensure_ascii=False)
    logger.info(f"结果已保存: {result_file}")

    return result


if __name__ == "__main__":
    result = main()
    has_error = result.get("error") or (
        result.get("publish") and not result["publish"].get("success")
    )
    sys.exit(1 if has_error else 0)
