# Daily AI digest → Feishu / Lark

Fetches AI-related RSS feeds, deduplicates and sorts by time, then posts a digest to a Feishu (Lark) custom bot webhook. Intended to run daily at **09:00 Asia/Shanghai** (e.g. via GitHub Actions at **01:00 UTC**).

## Setup

1. In a Feishu/Lark group, add a **custom bot** and copy the **Webhook URL**.
2. **GitHub**: use this folder as the **repository root** (so `scripts/` and `.github/` sit at the top level). In **Settings → Secrets and variables → Actions**, add:
   - `LARK_WEBHOOK_URL` — full webhook URL from the bot.
   - Optional: `OPENAI_API_KEY` — for a short Chinese summary.
   - Optional: `ENABLE_LLM_SUMMARY` — set the secret value to `1` to enable summarization (requires `OPENAI_API_KEY`).
   - Optional: `OPENAI_BASE_URL` — defaults to OpenAI’s API; set for compatible proxies.
   - Optional: `OPENAI_MODEL` — defaults to `gpt-4o-mini`.

3. Edit [`config/sources.yaml`](config/sources.yaml) to tune RSS sources.

## Local run

```bash
cd daily-ai-lark-digest
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export LARK_WEBHOOK_URL='https://open.feishu.cn/open-apis/bot/v2/hook/...'
# optional:
# export ENABLE_LLM_SUMMARY=1
# export OPENAI_API_KEY=sk-...
python scripts/daily_digest.py
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LARK_WEBHOOK_URL` | — | **Required.** Feishu/Lark incoming webhook. |
| `SOURCES_PATH` | `config/sources.yaml` | Path to feed list (relative to repo root or absolute). |
| `LOOKBACK_HOURS` | `36` | Include entries published within this many hours. |
| `MAX_PER_FEED` | `5` | Max items per feed before global cap. |
| `MAX_TOTAL` | `35` | Max items in the final digest. |
| `HTTP_TIMEOUT` | `25` | Per-feed fetch timeout (seconds). |
| `ENABLE_LLM_SUMMARY` | `0` | Set `1` to add a short Chinese summary (uses OpenAI-compatible API). |
| `OPENAI_API_KEY` | — | API key when summarization is enabled. |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Base URL for chat completions. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name. |

## Schedule

- **GitHub Actions**: [`.github/workflows/daily-ai-digest.yml`](.github/workflows/daily-ai-digest.yml) runs on `cron: '0 1 * * *'` (01:00 UTC = 09:00 Shanghai) and supports **Run workflow** manually.
- **macOS**: use `launchd` or `cron` to run `daily_digest.py` at 09:00 local only if your machine is on and you use Shanghai time in the job.

## Notes

- Some third-party RSS bridges (e.g. RSSHub) may be rate-limited or unstable; replace those URLs in `sources.yaml` if a feed fails often.
- Feishu message length is limited; the script trims the list and appends a note if truncated.
