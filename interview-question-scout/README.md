# interview-question-scout

Periodic 1point3acres 面经抓取 → Obsidian 汇总 + Discord 推送，外加交互式**复习手册**生成器。

Sibling of `../job-scout`. 不依赖 Hermes。

两条路：
- **自动**：`run_interview_question_scout.py` 每 12 小时跑一次，每帖一段总结，追加到 `Career/Interviews/<Company>.md`。
- **复习**：`aggregate_handbook.py` 把已抓到的几十~几百帖**聚合成题库**——频次榜 + 每题题面/解法/source 链接，写到 `Career/Company/<Company>-Interviews.md`。配套 skill 在 `skills/interview-question-scout/SKILL.md`，触发时 Claude 会自动用它。

## 快速开始

```bash
pip3 install -r requirements.txt     # playwright / bs4 / pyyaml / openai
# chromium binary 不需要装，走 CDP 连已开的 ai-chrome

# 确认 ai-chrome 在跑
curl -s http://localhost:9222/json/version

python3 scripts/run_interview_question_scout.py --dry-run --verbose   # 看抓到啥
python3 scripts/run_interview_question_scout.py --limit 2 --verbose   # 端到端 2 条
python3 scripts/install_launchd.py --every-hours 12          # 每 12 小时跑

# 复习手册（用 LLM 把已抓帖聚合成题库 + 频次榜）
python3 scripts/aggregate_handbook.py \
    --company OpenAI \
    --bodies-json /tmp/oai_bodies.json \
    --out "$OBSIDIAN_VAULT_PATH/Career/Company/OpenAI-Interviews.md"
```

详见 [docs/SETUP.md](docs/SETUP.md)。

## 结构

```
config/interview-question-scout.yaml    # 公司列表 + 抓取/总结/Discord 参数
scripts/
  interview_question_scout_lib.py       # env/SQLite/iCloud/CDP 工具（复用 ../job-scout/scripts/job_scout_lib.py）
  fetch_1point3acres.py        # Playwright CDP scraper
  summarize_posts.py           # LLM 结构化总结
  publish_obsidian.py          # 追加到 Career/Interviews/<Company>.md
  publish_discord.py           # Discord bot REST 推送
  run_interview_question_scout.py       # 入口
  install_launchd.py           # launchd 安装器
  _debug_dump_html.py          # 调试：把任意 URL 的渲染 HTML dump 出来
data/
  seen_posts.sqlite            # dedup (company, tid)
  raw/<slug>/<tid>.html        # 原始 HTML 快照
  logs/                        # launchd 日志
```

## Secrets（都在 `~/.hermes/.env`）

- `DISCORD_BOT_TOKEN`
- `DISCORD_HOME_CHANNEL`
- `OPENAI_API_KEY`

## 模型

总结走 OpenAI 兼容 API（`OPENAI_BASE_URL`，默认 `https://api.openai.com/v1`），模型可在 `config/interview-question-scout.yaml > summarize` 配置。
