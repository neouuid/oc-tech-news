# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **AI 智能摘要增强**: 集成火山引擎大模型 (Volcengine)，对短新闻自动抓取网页正文并生成摘要，对英文长文自动生成中文摘要，并添加 `✨(AI摘要)` 标识。
- **Tavily 智能搜索补充**: 引入 Tavily 搜索引擎，根据设定的硬核科技关键词（如 AI Agent、RAG、大语言模型等）主动检索全网最新资讯，与 RSS 结果合并打分。
- **自动化工作流**: 新增 `.github/workflows/daily_news.yml`，支持通过 GitHub Actions 每天定时抓取新闻并上传为 Artifact。
- **开源配置完善**: 新增 `.gitignore`、`config.example.json`、`README.md` 和 `LICENSE` (MIT)，方便其他开发者克隆和部署。

### Changed
- **评分机制优化**: 提升了标题和摘要中出现“硬核高权重关键词”的得分比重；降低了单纯发布时间的排序权重。
- **去重机制强化**: 使用 `difflib` 引入字符串相似度对比，并在多源新闻中实现了基于全网格比对的相似新闻聚类和去重，合并相同事件的热度。
- **性能优化**: 引入 `concurrent.futures.ThreadPoolExecutor` 实现多线程并发抓取（RSS、网页正文和 AI 摘要生成），大幅提升运行速度。
- **容错机制**: 增加 `requests` 网络重试机制（Retry），有效应对 RSSHub 或部分外媒网站的偶尔连接超时。

### Fixed
- 修复了 Hacker News RSS 源摘要直接显示为 Article URL 的问题。
- 修复了使用旧版 `volcenginesdkarkruntime` 导致的包冲突和鉴权失败问题，改为直接使用 `requests` 调用兼容 OpenAI 格式的 REST API。

## [0.1.0] - 2026-03-28

### Added
- 初始版本发布。
- 支持从 10+ 个国内外优质科技媒体（如 36氪、机器之心、InfoQ、GitHub Blog 等）抓取 RSS 订阅流。
- 实现了基础的关键词屏蔽（如“票房”、“影评”等）过滤逻辑。
- 实现了将新闻数据格式化输出为排版清晰的 Markdown 格式文件。
- 引入 `logging` 模块，实现了按时间戳命名的日志记录和控制台输出机制。
