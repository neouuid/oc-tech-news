# 🚀 硬核科技新闻收集器 (Tech News Collector)

一个专为硬核程序员和科技爱好者打造的自动化科技新闻抓取与摘要增强工具。通过聚合高质量的国内外 RSS 源、整合 Tavily 搜索引擎进行深度挖掘，并利用火山引擎（Volcengine）大模型对新闻进行智能摘要与翻译，每天为你生成一份高信息密度的 Markdown 格式极客早报。

## ✨ 核心特性

- 📡 **多源 RSS 聚合**：内置 10+ 个顶尖国内外技术媒体、开源社区（Hacker News, GitHub, InfoQ, 36氪 等）的 RSS 源。
- 🔍 **Tavily 智能搜索增强**：不仅依赖 RSS，还会通过设定的双语科技关键词（如 `AI Agent`, `RAG`, `大模型发布`），使用 Tavily 在全网范围内主动搜刮过去 48 小时内的核心技术新闻。
- 🧠 **大模型智能摘要增强**：
  - 遇到短新闻或纯链接时，自动抓取网页正文。
  - 调用火山引擎（Volcengine）大模型，剔除营销废话，提取核心技术突破，限制在 300 字以内。
  - **自动英译中**：无缝将英文外媒长文总结为精炼的中文摘要。
- ⚖️ **智能打分与去重**：基于标题、摘要中的“硬核关键词（如 AI, LLM, Kubernetes 等）”进行权重打分；使用文本相似度算法（Difflib）将多个媒体报道的同一事件进行聚类与去重。
- 📝 **Markdown 优雅输出**：自动生成排版精美的 Markdown 文件，包含时间大盘、新闻标题、AI 摘要、原文链接，方便阅读与二次分发。

## 🛠️ 安装与配置

### 1. 克隆代码与安装依赖

确保你的环境中已安装 Python 3.8+。

```bash
# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境 (重点)

项目依赖外部 API（Tavily 搜索 和 火山引擎大模型），为了安全，请不要将含有密钥的文件提交到公开仓库。

复制配置模板：
```bash
cp config.example.json config.json
```

打开 `config.json` 并填写你的配置：
- `tavily_config.api_key`: 你的 [Tavily API Key](https://tavily.com/)
- `llm_config.api_key`: 你的 [火山引擎 API Key](https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey)
- `llm_config.model_endpoint`: 你的大模型接入点（Endpoint ID），推荐使用 `doubao-pro` 级别模型以获得更好的摘要效果。

你也可以在配置文件中自定义你关心的 `high_weight_keywords`（高权重热词）和 `exclude_keywords`（屏蔽词）。

## 🚀 运行使用

直接运行主程序：

```bash
python fetch_news.py
```

运行结束后，你可以在以下目录找到输出：
- `outputs/`: 存放生成的 Markdown 格式新闻早报（例如 `TechNews_20260329_092204.md`）。
- `logs/`: 存放详细的运行日志，方便排查抓取和 API 调用问题。

## 🤖 自动化部署 (GitHub Actions)

本项目支持通过 GitHub Actions 实现自动化定时运行。

1. Fork 本仓库。
2. 在仓库的 **Settings > Secrets and variables > Actions** 中，添加以下 Repository Secrets：
   - `TAVILY_API_KEY`: 对应配置文件中的 Tavily API Key。
   - `VOLCENGINE_API_KEY`: 对应配置文件中的 火山引擎 API Key。
   - `VOLCENGINE_MODEL_ENDPOINT`: 对应配置文件中的 大模型 Endpoint。
3. 在 Actions 面板中启用 `Daily Tech News Crawler` 工作流。
4. 每天早上（UTC 时间 00:00，北京时间 08:00），爬虫会自动运行，并将生成的 `.md` 文件打包作为 Artifact 上传，供你下载阅读。

## 🤝 贡献与优化建议

欢迎提交 Pull Request 或者 Issue 提出优化建议。目前的潜在优化方向包括：
- [ ] 增加 SQLite/JSON 历史抓取缓存，避免同一天多次运行导致内容重复。
- [ ] 增加 Webhook 推送功能（飞书/钉钉/Telegram）。
- [ ] 增加大模型语义级文章聚类去重。

## 📄 许可证

MIT License