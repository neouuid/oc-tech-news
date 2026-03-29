#!/usr/bin/env python3
"""
OpenClaw 定时任务适配版本的科技新闻抓取脚本
复用原有所有功能，新增飞书推送能力
"""
import os
import sys
import subprocess
from datetime import datetime
from fetch_news import (
    load_config, setup_logger, init_db, cleanup_old_cache,
    fetch_rss_feeds, fetch_tavily_searches, select_top_news,
    enhance_summaries_with_llm, record_pushed_news, generate_markdown
)
import pytz
from datetime import timedelta

def send_to_feishu(content: str, title: str = "📰 全球科技新闻每日汇总") -> bool:
    """
    通过 OpenClaw message 工具推送内容到飞书
    """
    try:
        # 构造飞书消息内容，支持 markdown
        message_content = f"# {title}\n\n{content}"
        
        # 调用 OpenClaw message 工具发送
        cmd = [
            "openclaw", "message", "send",
            "--channel", "feishu",
            "--target", "ou_a0129f24abde586612dc307548c0037c",
            "--message", message_content
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        print(f"飞书推送成功: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"飞书推送失败: {e.stderr}")
        return False
    except Exception as e:
        print(f"推送过程发生异常: {str(e)}")
        return False

def main():
    # 0. 生成统一的时间戳
    run_time = datetime.now()
    timestamp_str = run_time.strftime("%Y%m%d_%H%M%S")
    display_time_str = run_time.strftime("%Y-%m-%d %H:%M:%S")
    title = f"📰 全球科技新闻汇总 {run_time.strftime('%Y年%m月%d日')}"

    # 1. 加载配置并初始化日志
    config = load_config("config.json")
    logger = setup_logger(config.get("log_dir", "logs"), timestamp_str)
    
    logger.info("=== OpenClaw 定时任务：开始科技新闻抓取 ===")
    
    # 初始化缓存数据库
    db_conn = init_db()
    cleanup_old_cache(db_conn, days_to_keep=7)
    
    # 2. 抓取新闻
    site_news_lists = fetch_rss_feeds(config, db_conn)
    
    # 补充 Tavily 搜索结果
    days_limit = config.get("days_limit", 2)
    now_utc = datetime.now(pytz.utc)
    time_limit = now_utc - timedelta(days=days_limit)
    tavily_news_lists = fetch_tavily_searches(config, time_limit, now_utc, db_conn)
    
    # 合并结果
    if tavily_news_lists:
        site_news_lists.extend(tavily_news_lists)
    
    # 3. 筛选前N条
    max_news_count = config.get("max_news_count", 10)
    logger.info(f"正在从 {len(site_news_lists)} 个有效站点中筛选出前 {max_news_count} 条...")
    
    # 统计抓取总数
    total_fetched_count = sum(len(news_list) for news_list in site_news_lists)
    # 简单收集来源列表（用于大盘显示）
    site_stats = [news_list[0]['site'] for news_list in site_news_lists if news_list]
    
    if total_fetched_count == 0:
        logger.warning("没有抓取到任何符合条件的新闻。")
        db_conn.close()
        send_to_feishu("⚠️ 今日未抓取到符合条件的科技新闻，请检查配置或网络。", title)
        return
        
    top_news = select_top_news(site_news_lists, max_news_count)
    
    # 3.5 对最终上榜的 Top N 条新闻，进行大模型摘要增强
    top_news = enhance_summaries_with_llm(top_news, config)
    
    # 记录已推送的新闻到数据库
    record_pushed_news(db_conn, top_news)
    db_conn.close()
    
    # 4. 生成 Markdown 内容
    md_content = generate_markdown(top_news, display_time_str, total_fetched_count, site_stats)
    
    # 5. 保存文件 (支持一天多次运行，采用时间戳命名)
    output_dir = config.get("output_dir", "outputs")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"TechNews_{timestamp_str}.md"
    filepath = os.path.join(output_dir, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)
        
    logger.info(f"任务完成！共处理 {len(top_news)} 条新闻。")
    logger.info(f"输出文件路径: {os.path.abspath(filepath)}")
    
    # 6. 推送至飞书
    logger.info("开始推送至飞书...")
    push_success = send_to_feishu(md_content, title)
    
    if push_success:
        logger.info("✅ 飞书推送成功！")
    else:
        logger.error("❌ 飞书推送失败！")
        sys.exit(1)

if __name__ == "__main__":
    main()
