import os
import re
import json
import logging
import concurrent.futures
from datetime import datetime, timedelta
import pytz
import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import warnings
import difflib
from bs4 import BeautifulSoup
from bs4 import MarkupResemblesLocatorWarning
import sqlite3

# 忽略 Beautiful Soup 的 MarkupResemblesLocatorWarning 警告
warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)


def load_config(config_path="config.json"):
    """加载配置文件"""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"找不到配置文件: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

def setup_logger(log_dir, timestamp_str):
    """配置日志记录器"""
    os.makedirs(log_dir, exist_ok=True)
    log_filename = f"fetch_news_{timestamp_str}.log"
    log_filepath = os.path.join(log_dir, log_filename)
    
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # 清理已有的 handlers (防止重复输出)
    if logger.hasHandlers():
        logger.handlers.clear()
        
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    
    # 写入文件
    file_handler = logging.FileHandler(log_filepath, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # 控制台输出
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger

def clean_html_to_text(html_content):
    """提取HTML中的纯文本"""
    if not html_content:
        return ""
    soup = BeautifulSoup(html_content, "html.parser")
    # 提取纯文本并去除多余空白
    text = soup.get_text(separator=" ", strip=True)
    # 替换多个连续空格/换行为单个空格
    text = re.sub(r'\s+', ' ', text)
    return text

def fetch_webpage_content(url):
    """抓取网页正文内容（简单实现，提取段落文本）"""
    try:
        session = requests.Session()
        retry_strategy = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        # 移除脚本和样式
        for script in soup(["script", "style", "header", "footer", "nav"]):
            script.extract()
            
        # 提取所有段落
        paragraphs = soup.find_all('p')
        text = ' '.join([p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 20])
        
        # 如果段落提取失败，退级到整个 body
        if not text and soup.body:
            text = soup.body.get_text(separator=' ', strip=True)
            
        return text[:4000]  # 限制最大长度，避免超出大模型上下文
    except Exception as e:
        logging.warning(f"抓取网页正文失败 [{url}]: {e}")
        return ""

def get_short_summary(text, max_length):
    """截取指定字数的摘要"""
    if len(text) > max_length:
        return text[:max_length] + "..."
    return text

def parse_time(entry, now_utc):
    """解析RSS条目中的发布时间，统一转为UTC时间对象"""
    if hasattr(entry, 'published_parsed') and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=pytz.utc)
    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=pytz.utc)
    else:
        # 如果获取不到时间，默认当做最新时间
        return now_utc

def is_excluded(title, summary, exclude_keywords):
    """检查标题或摘要是否包含排除关键词"""
    if not exclude_keywords:
        return False
    
    # 转换为小写以进行不区分大小写的匹配
    title_lower = title.lower()
    summary_lower = summary.lower()
    
    for keyword in exclude_keywords:
        keyword_lower = keyword.lower()
        if keyword_lower in title_lower or keyword_lower in summary_lower:
            return True
    return False

# ================= 缓存数据库管理 =================

def init_db(db_path="dbs/news_pushed.db"):
    """初始化 SQLite 数据库，创建缓存表"""
    # 确保 dbs 目录存在
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    # 增加 check_same_thread=False 允许跨线程共享连接
    conn = sqlite3.connect(db_path, check_same_thread=False)
    cursor = conn.cursor()
    # url: 新闻链接 (主键)
    # title: 新闻标题
    # fetch_time: 抓取/推送时间
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS pushed_news (
            url TEXT PRIMARY KEY,
            title TEXT,
            fetch_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    return conn

def is_news_pushed(conn, url):
    """检查新闻链接是否已经在数据库中"""
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM pushed_news WHERE url = ?", (url,))
    return cursor.fetchone() is not None

def record_pushed_news(conn, news_list):
    """记录已推送的新闻到数据库"""
    cursor = conn.cursor()
    now_str = datetime.now(pytz.utc).isoformat()
    for news in news_list:
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO pushed_news (url, title, fetch_time) VALUES (?, ?, ?)",
                (news['link'], news['title'], now_str)
            )
        except sqlite3.Error as e:
            logging.error(f"记录数据库失败: {news['link']} - {e}")
    conn.commit()

def cleanup_old_cache(conn, days_to_keep=30):
    """清理 N 天前的旧缓存，防止数据库无限膨胀"""
    cursor = conn.cursor()
    cutoff_date = (datetime.now(pytz.utc) - timedelta(days=days_to_keep)).isoformat()
    cursor.execute("DELETE FROM pushed_news WHERE fetch_time < ?", (cutoff_date,))
    deleted_count = cursor.rowcount
    conn.commit()
    if deleted_count > 0:
        logging.info(f"已清理 {deleted_count} 条过期的新闻缓存记录")

# ===================================================

def calculate_keyword_score(title, summary, high_weight_keywords):
    """计算高权重关键词得分"""
    if not high_weight_keywords:
        return 0
        
    score = 0
    title_lower = title.lower()
    summary_lower = summary.lower()
    
    for keyword in high_weight_keywords:
        keyword_lower = keyword.lower()
        # 标题中出现权重更高 (例如：+100分)
        if keyword_lower in title_lower:
            score += 100
        # 摘要中出现也有加分 (例如：+50分)
        if keyword_lower in summary_lower:
            score += 50
            
    return score

def generate_llm_summary(content, config):
    """调用火山引擎大模型生成摘要"""
    llm_config = config.get("llm_config", {})
    if not llm_config.get("enable", False):
        return None
        
    api_key = llm_config.get("api_key", "")
    model_endpoint = llm_config.get("model_endpoint", "")
    base_url = llm_config.get("base_url", "https://ark.cn-beijing.volces.com/api/v3")
    
    if not api_key or api_key == "YOUR_VOLCENGINE_API_KEY" or not model_endpoint or model_endpoint == "YOUR_MODEL_ENDPOINT":
        return None
        
    try:
        # 替换为兼容的调用方式（由于旧版 sdk/新版 api 不一致，这里使用 requests 直接调用 REST API 更稳妥）
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        prompt = llm_config.get("prompt", "请对以下科技新闻内容进行摘要，提取核心技术突破或动态，限制在300字以内，直接输出摘要内容：")
        
        payload = {
            "model": model_endpoint,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content}
            ],
            "temperature": llm_config.get("temperature", 0.8),
            "max_tokens": llm_config.get("max_tokens", 10000)
        }
        
        # 火山引擎 API 兼容 OpenAI 格式
        api_url = f"{base_url.rstrip('/')}/chat/completions"
        response = requests.post(api_url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        
        result_data = response.json()
        summary = result_data["choices"][0]["message"]["content"].strip()
        return summary
    except Exception as e:
        logging.error(f"调用火山引擎大模型生成摘要失败: {e}")
        return None

def fetch_single_feed(site_name, url, time_limit, summary_max_length, exclude_keywords, high_weight_keywords, now_utc, db_conn=None):
    """抓取单个 RSS 源，包含重试机制"""
    logging.info(f"开始抓取: [{site_name}]")
    current_site_news = []
    
    # 配置带有重试机制的 Session
    session = requests.Session()
    retry_strategy = Retry(
        total=3,  # 总重试次数
        backoff_factor=1,  # 重试间隔时间 1s, 2s, 4s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        response = session.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        
        for entry in feed.entries:
            pub_time = parse_time(entry, now_utc)
            
            # 过滤条件
            if pub_time >= time_limit:
                title = entry.get('title', '无标题')
                link = entry.get('link', '')
                
                # 去重检查：如果已经在数据库中存在，跳过
                if db_conn and is_news_pushed(db_conn, link):
                    continue

                content_html = entry.get('summary', entry.get('description', ''))
                clean_text = clean_html_to_text(content_html)
                summary = get_short_summary(clean_text, summary_max_length)
                
                if "hnrss.org" in url or site_name == "Hacker News":
                    summary = "该文章无摘要内容，请点击阅读原文查看。"
                
                # 关键词过滤
                if is_excluded(title, summary, exclude_keywords):
                    continue
                    
                # 计算关键词权重分
                kw_score = calculate_keyword_score(title, summary, high_weight_keywords)
                
                current_site_news.append({
                    'site': site_name,
                    'title': title,
                    'link': link,
                    'summary': summary,
                    'pub_time': pub_time,
                    'comments': int(entry.get('comments', 0)) if str(entry.get('comments', '0')).isdigit() else 0,
                    'kw_score': kw_score
                })
        
        # 按照时间从新到旧排序
        current_site_news.sort(key=lambda x: x['pub_time'], reverse=True)
        if current_site_news:
            logging.info(f"成功获取 [{site_name}] {len(current_site_news)} 条新闻")
        else:
            logging.info(f"[{site_name}] 没有符合条件的新闻")
            
    except Exception as e:
        logging.error(f"调用 RSS 源 [{site_name}] 失败: {e}")
        
    return current_site_news

def fetch_rss_feeds(config, db_conn=None):
    """抓取并过滤所有RSS源的最新文章"""
    rss_feeds = config.get("rss_feeds", {})
    days_limit = config.get("days_limit", 3)
    summary_max_length = config.get("summary_max_length", 300)
    exclude_keywords = config.get("exclude_keywords", [])
    high_weight_keywords = config.get("high_weight_keywords", [])
    
    now_utc = datetime.now(pytz.utc)
    time_limit = now_utc - timedelta(days=days_limit)
    
    site_news_lists = []

    # 使用多线程并发抓取
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(10, len(rss_feeds) or 1)) as executor:
        future_to_url = {
            executor.submit(
                fetch_single_feed, site_name, url, time_limit, 
                summary_max_length, exclude_keywords, high_weight_keywords, now_utc, db_conn
            ): site_name for site_name, url in rss_feeds.items()
        }
        
        for future in concurrent.futures.as_completed(future_to_url):
            site_name = future_to_url[future]
            try:
                news_list = future.result()
                if news_list:
                    site_news_lists.append(news_list)
            except Exception as exc:
                logging.error(f"[{site_name}] 线程执行异常: {exc}")

    return site_news_lists

def fetch_tavily_searches(config, time_limit, now_utc, db_conn=None):
    """通过 Tavily API 进行主动检索，补充优质内容"""
    tavily_config = config.get("tavily_config", {})
    if not tavily_config.get("enable", False):
        return []
        
    api_key = tavily_config.get("api_key", "")
    if not api_key or api_key == "YOUR_TAVILY_API_KEY":
        logging.warning("Tavily 检索已开启，但未配置有效的 API Key，已跳过。请在 config.json 中配置 tavily_config.api_key。")
        return []
        
    queries = tavily_config.get("queries", [])
    include_domains = tavily_config.get("include_domains", [])
    days_limit = config.get("days_limit", 2)
    summary_max_length = config.get("summary_max_length", 300)
    exclude_keywords = config.get("exclude_keywords", [])
    high_weight_keywords = config.get("high_weight_keywords", [])
    
    tavily_news_lists = []
    
    # 我们也可以用多线程并发请求 Tavily
    def _search_single_query(query):
        logging.info(f"开始 Tavily 检索: [{query}]")
        current_query_news = []
        try:
            payload = {
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "topic": "news",
                "days": days_limit,
                "max_results": 10
            }
            if include_domains:
                payload["include_domains"] = include_domains
                
            response = requests.post("https://api.tavily.com/search", json=payload, timeout=20)
            response.raise_for_status()
            data = response.json()
            
            for item in data.get("results", []):
                # 解析发布时间，Tavily 有时返回 ISO 格式时间
                pub_time = now_utc
                if item.get("published_date"):
                    try:
                        # 处理如 "2024-03-28T10:00:00Z" 格式
                        pub_time = datetime.fromisoformat(item["published_date"].replace("Z", "+00:00"))
                    except Exception:
                        pass
                        
                # 过滤条件
                if pub_time >= time_limit:
                    title = item.get("title", "无标题")
                    link = item.get("url", "")
                    
                    # 去重检查：如果已经在数据库中存在，跳过
                    if db_conn and is_news_pushed(db_conn, link):
                        continue

                    content_raw = item.get("content", "")
                    summary = get_short_summary(content_raw, summary_max_length)
                    
                    if is_excluded(title, summary, exclude_keywords):
                        continue
                        
                    kw_score = calculate_keyword_score(title, summary, high_weight_keywords)
                    
                    # 提取域名作为来源标识
                    site_name = "Tavily"
                    try:
                        from urllib.parse import urlparse
                        domain = urlparse(link).netloc
                        site_name = f"Tavily检索 ({domain})"
                    except Exception:
                        pass
                        
                    current_query_news.append({
                        'site': site_name,
                        'title': title,
                        'link': link,
                        'summary': summary,
                        'pub_time': pub_time,
                        'comments': 0,
                        'kw_score': kw_score
                    })
                    
            # 排序
            current_query_news.sort(key=lambda x: x['pub_time'], reverse=True)
            if current_query_news:
                logging.info(f"成功通过 Tavily [{query}] 获取 {len(current_query_news)} 条新闻")
            else:
                logging.info(f"Tavily 检索 [{query}] 未获取到符合条件的新闻")
                
        except Exception as e:
            logging.error(f"Tavily 检索 [{query}] 失败: {e}")
            
        return current_query_news

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(queries) or 1)) as executor:
        future_to_query = {executor.submit(_search_single_query, q): q for q in queries}
        for future in concurrent.futures.as_completed(future_to_query):
            query = future_to_query[future]
            try:
                news_list = future.result()
                if news_list:
                    tavily_news_lists.append(news_list)
            except Exception as exc:
                logging.error(f"Tavily [{query}] 线程执行异常: {exc}")

    return tavily_news_lists

def is_similar(title1, title2, threshold=0.65):
    """判断两个标题是否相似"""
    # 转换为小写并移除常见标点
    t1 = ''.join(e for e in title1.lower() if e.isalnum())
    t2 = ''.join(e for e in title2.lower() if e.isalnum())
    
    if not t1 or not t2:
        return False
        
    # 1. 包含关系：如果一个标题完全包含另一个标题（且较短的标题不能太短，避免误伤）
    min_len = min(len(t1), len(t2))
    if min_len >= 5:
        if t1 in t2 or t2 in t1:
            return True
            
    # 2. 文本相似度计算
    return difflib.SequenceMatcher(None, t1, t2).ratio() > threshold

def select_top_news(site_news_lists, total_count):
    """从多个站点的列表中，选取真正高质量/热度的内容"""
    all_news = []
    for news_list in site_news_lists:
        all_news.extend(news_list)
        
    if not all_news:
        return []
        
    # --- 标题相似度聚类（计算跨源热度） ---
    # cluster_groups = [ [news1, news2], [news3], ... ]
    cluster_groups = []
    
    for news in all_news:
        added_to_cluster = False
        for cluster in cluster_groups:
            # 只要与簇内任何一条新闻相似，或链接完全相同，就归为同一类
            if any(news['link'] == item['link'] or is_similar(news['title'], item['title']) for item in cluster):
                cluster.append(news)
                added_to_cluster = True
                break
        
        if not added_to_cluster:
            cluster_groups.append([news])
            
    # 从每个聚类中选出最佳代表，并赋予“跨源热度加分”
    clustered_news = []
    for cluster in cluster_groups:
        # 统计该话题在多少个不同的源中出现过
        unique_sites = set(item['site'] for item in cluster)
        multi_source_bonus = (len(unique_sites) - 1) * 200  # 每多一个不同的源，加 200 分！
        
        # 在相似的新闻中，选出自身属性（关键词分、评论数）最高的一条作为代表（时间降权，只看日期）
        cluster.sort(key=lambda x: (x.get('kw_score', 0), x.get('comments', 0), x['pub_time'].date()), reverse=True)
        best_representative = cluster[0]
        
        # 收集其他来源的链接，供 Markdown 中展示
        other_links = []
        seen_sites = {best_representative['site']}
        for item in cluster[1:]:
            if item['site'] not in seen_sites:
                other_links.append((item['site'], item['link']))
                seen_sites.add(item['site'])
        
        # 加上跨源热度分
        best_representative['multi_source_bonus'] = multi_source_bonus
        # 记录一下到底有几个源报道了，方便展示
        best_representative['source_count'] = len(unique_sites)
        best_representative['other_links'] = other_links
        
        clustered_news.append(best_representative)
        
    # --- 最终排序 ---
    # 为了保证质量，我们将所有抓取到的新闻放在一起进行排序
    # 排序策略：
    # 1. 优先看“跨源热度加分” + “关键词权重分” 的总分
    # 2. 其次按照“评论数/分数”排序 (如 Hacker News)
    # 3. 最后按照“时间”最新排序
    
    clustered_news.sort(
        key=lambda x: (
            x.get('multi_source_bonus', 0) + x.get('kw_score', 0), 
            x.get('comments', 0), 
            x['pub_time'].date()  # 降低时间权重：仅按发布日期排序，而非精确到秒
        ), 
        reverse=True
    )
    
    # 返回前 total_count 条
    return clustered_news[:total_count]

def generate_markdown(news_list, display_time_str, total_fetched_count=0, site_stats=None):
    """将新闻列表转换为Markdown格式的字符串"""
    if not news_list:
        return "# 科技新闻汇总\n没有抓取到最近的新闻。\n"
        
    lines = ["# 科技新闻汇总\n"]
    lines.append(f"**生成时间:** {display_time_str}\n")
    
    # 增加统计大盘
    if site_stats:
        lines.append("### 📊 今日抓取大盘")
        lines.append(f"- **总计抓取候选:** {total_fetched_count} 条")
        lines.append(f"- **最终上榜:** {len(news_list)} 条")
        
        # 统计上榜来源分布
        on_board_stats = {}
        for news in news_list:
            site = news['site']
            on_board_stats[site] = on_board_stats.get(site, 0) + 1
            
        # 按上榜数量降序排列
        sorted_stats = sorted(on_board_stats.items(), key=lambda x: x[1], reverse=True)
        stats_str = " | ".join([f"{site}({count})" for site, count in sorted_stats])
        lines.append(f"- **上榜来源分布:** {stats_str}\n")
        lines.append("---\n")
    
    for idx, news in enumerate(news_list, 1):
        # 转换为本地时间展示（这里默认东八区）
        local_time = news['pub_time'].astimezone(pytz.timezone('Asia/Shanghai'))
        time_str = local_time.strftime('%Y-%m-%d %H:%M')
        
        # 调试信息，在标题中展示一下分数（仅供你直观感受排序效果，部署时可以去掉）
        score_info = f"[多源:{news.get('multi_source_bonus', 0)} | 关键词:{news.get('kw_score', 0)} | 热度:{news.get('comments', 0)}]"
        
        # 如果有多个源报道，在标题旁增加一个 🔥 标记
        fire_icon = "🔥" * (news.get('source_count', 1) - 1)
        if fire_icon:
            fire_icon = f" {fire_icon} ({news.get('source_count')}源)"
            
        lines.append(f"## {idx}. {news['title']} {fire_icon} ")
        lines.append(f"**来源:** {news['site']}")
        lines.append(f"**时间:** {time_str}")
        lines.append(f"**热度:** {score_info}")
        lines.append(f"**摘要:**\n> {news['summary']}")
        
        # 组装链接展示
        links_str = f"[阅读原文]({news['link']})"
        if news.get('other_links'):
            other_links_str = " | ".join([f"[{site}]({link})" for site, link in news['other_links']])
            links_str += f" | 其他来源: {other_links_str}"
            
        lines.append(f"{links_str}")
        lines.append("---")
        
    return "\n".join(lines)

def enhance_summaries_with_llm(news_list, config):
    """对筛选出的新闻列表，使用大模型进行摘要增强"""
    llm_config = config.get("llm_config", {})
    if not llm_config.get("enable", False):
        return news_list
        
    api_key = llm_config.get("api_key", "")
    model_endpoint = llm_config.get("model_endpoint", "")
    
    if not api_key or api_key == "YOUR_VOLCENGINE_API_KEY" or not model_endpoint or model_endpoint == "YOUR_MODEL_ENDPOINT":
        logging.info("未配置有效的火山引擎 API Key 或 Model Endpoint，跳过大模型摘要增强。")
        return news_list
        
    logging.info(f"开始对 Top {len(news_list)} 条新闻进行大模型摘要增强...")
    short_content_threshold = llm_config.get("short_content_threshold", 100)
    
    def _process_single_news(news):
        original_summary = news.get("summary", "")
        link = news.get("link", "")
        
        # 决定使用什么内容给大模型
        content_for_llm = original_summary
        
        # 如果摘要太短，或者是黑客新闻那种没有摘要的，尝试抓取网页正文
        if len(original_summary) < short_content_threshold or "该文章无摘要内容" in original_summary:
            logging.info(f"内容较短，尝试抓取网页正文: {link}")
            web_content = fetch_webpage_content(link)
            if web_content and len(web_content) > 50:
                content_for_llm = web_content
            else:
                # 抓取失败或依然很短，还是用原标题和摘要
                content_for_llm = f"标题: {news.get('title', '')}\n内容: {original_summary}"
                
        # 调用大模型
        logging.info(f"调用大模型生成摘要: {news.get('title', '')}")
        new_summary = generate_llm_summary(content_for_llm, config)
        
        if new_summary:
            news["summary"] = new_summary + " ✨(AI摘要)"
        
        return news

    # 使用多线程并发处理大模型调用，提升速度
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_process_single_news, news): news for news in news_list}
        enhanced_news_list = []
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                enhanced_news_list.append(result)
            except Exception as e:
                logging.error(f"大模型增强单条新闻失败: {e}")
                # 失败的话保留原新闻
                enhanced_news_list.append(futures[future])
                
    # 因为多线程打乱了顺序，需要重新按照时间/分数排序（这里维持原顺序最简单的方法是处理前记录index，处理后恢复）
    # 但由于之前我们是取前N条，这里简单地按照之前的多源热度等再排一次即可，或者更好的做法是：
    
    # 按照原列表的顺序恢复
    order_map = {news['link']: idx for idx, news in enumerate(news_list)}
    enhanced_news_list.sort(key=lambda x: order_map.get(x['link'], 999))
    
    return enhanced_news_list

def main():
    # 0. 生成统一的时间戳
    run_time = datetime.now()
    timestamp_str = run_time.strftime("%Y%m%d_%H%M%S")
    display_time_str = run_time.strftime("%Y-%m-%d %H:%M:%S")

    # 1. 加载配置并初始化日志
    config = load_config("config.json")
    logger = setup_logger(config.get("log_dir", "logs"), timestamp_str)
    
    logger.info("=== 开始科技新闻抓取任务 ===")
    
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

if __name__ == "__main__":
    main()