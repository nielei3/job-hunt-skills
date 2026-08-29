# interview-question-scout setup

## 前置条件

1. **ai-chrome 要开着并且登录过 1point3acres**

   ai-chrome 是你已有的一个 Chrome 实例，用于 CDP 自动化：
   ```
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
     --remote-debugging-port=9222 \
     --user-data-dir=$HOME/.config/ai-chrome
   ```
   如果它没在跑，scraper 启动时会在 Discord 发告警并退出。

   登录 1point3acres：在 ai-chrome 里手动打开 `https://www.1point3acres.com/bbs/`
   登录一次，cookie 自动持久化到 `~/.config/ai-chrome`。

2. **`~/.hermes/.env` 里有这些 key**（已经有了，直接复用）
   - `DISCORD_BOT_TOKEN`
   - `DISCORD_HOME_CHANNEL`
   - `OPENAI_API_KEY`

3. **Obsidian vault**：`~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/`
   第一次运行会自动建 `Career/Interviews/<Company>.md`。

---

## 首次跑通

```bash
cd ~/github/agent-skills/interview-question-scout

# 1. 确认 CDP
curl -s http://localhost:9222/json/version | head -3

# 2. Dry-run：只抓列表，不总结不发 Discord 不写 Obsidian
python3 scripts/run_interview_question_scout.py --dry-run --verbose

# 3. 真跑 2 条，端到端验证
python3 scripts/run_interview_question_scout.py --limit 2 --verbose

# 4. 复跑一次，应该全 dedup 成 0 new
python3 scripts/run_interview_question_scout.py --verbose
```

验证检查：
- `ls -la "$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Career/Interviews/"`
  → 应有 `OpenAI.md`
- Discord 频道应收到 N 条帖子通知 + 1 条 run summary
- `sqlite3 data/seen_posts.sqlite 'select count(*), locked_by_dami from seen_posts group by locked_by_dami'`

---

## 安装 launchd（每 12 小时）

```bash
# 先看生成的 plist
python3 scripts/install_launchd.py --every-hours 12 --print-only

# 真装
python3 scripts/install_launchd.py --every-hours 12

# 验证已加载
launchctl list | grep interview-question-scout

# 手动触发一次
launchctl kickstart gui/$(id -u)/com.example.interview-question-scout.periodic

# 看 log
tail -f data/logs/launchd.stdout.log data/logs/launchd.stderr.log
```

卸载：
```bash
python3 scripts/install_launchd.py --unload
```

---

## 新增公司

编辑 `config/interview-question-scout.yaml`：

```yaml
companies:
  - name: OpenAI
    slug: openai
    enabled: true
    obsidian_file: Career/Interviews/OpenAI.md
  - name: Anthropic              # 新加
    slug: anthropic
    enabled: true
    obsidian_file: Career/Interviews/Anthropic.md
```

slug = 1point3acres URL 路径里的公司标识，从 `https://jobs.1point3acres.com/companies/<slug>/interview`
复制。下次 launchd 触发自动开始抓。

---

## 常见问题

**Chrome CDP unreachable**
你把 ai-chrome 关了。重新用上面的命令启动。

**1point3acres 返回 0 条**
可能是登录态失效。在 ai-chrome 里手动访问 1point3acres 看是否要求登录，重新登一次。

**大米锁帖子**
当前策略：不花大米，locked 帖只记标题，Obsidian 和 Discord 都会加 🔒 标记。
想改策略 → `config/interview-question-scout.yaml` 未来可加 `respect_dami` 开关。

**总结质量不行**
改 `scripts/summarize_posts.py` 里的 `SUMMARIZE_PROMPT`。
改完可用 `python3 scripts/run_interview_question_scout.py --company openai --limit 1` 快速验证。

**想重新总结旧帖**
当前没有 `--retry-summary` flag（MVP 阶段未实现）。
临时方案：`sqlite3 data/seen_posts.sqlite "delete from seen_posts where tid='XXXXX'"` 然后重跑。
原始 HTML 留在 `data/raw/<slug>/<tid>.html` 里，不需要重新爬。
