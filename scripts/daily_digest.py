#!/usr/bin/env python3
"""Fetch AI-related RSS feeds and post a digest to a Feishu/Lark webhook."""

from __future__ import annotations

import calendar
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml

SHANGHAI = ZoneInfo("Asia/Shanghai")
USER_AGENT = "daily-ai-lark-digest/1.0 (+https://github.com)"


@dataclass
class FeedItem:
    title: str
    link: str
    source: str
    published: datetime


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.netloc and parsed.path.startswith("http"):
        parsed = urlparse(parsed.path)
    scheme = (parsed.scheme or "https").lower()
    netloc = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/")
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_")
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunparse((scheme, netloc, path, "", query, ""))


def entry_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        ts = calendar.timegm(t)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(SHANGHAI)


def entry_link(entry: feedparser.FeedParserDict) -> str:
    link = entry.get("link")
    if link:
        return link.strip()
    links = entry.get("links") or []
    for L in links:
        href = L.get("href")
        if href:
            return href.strip()
    return ""


def entry_title(entry: feedparser.FeedParserDict) -> str:
    t = entry.get("title")
    return (t or "Untitled").strip().replace("\n", " ")


def fetch_feed_xml(url: str, timeout: int) -> str | None:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except requests.RequestException as e:
        print(f"[warn] fetch failed {url!r}: {e}", file=sys.stderr)
        return None


def load_sources(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    feeds = data.get("feeds") or []
    if not isinstance(feeds, list):
        return []
    return feeds


def collect_items(
    feeds: list[dict],
    cutoff: datetime,
    default_per_feed: int,
    http_timeout: int,
) -> list[FeedItem]:
    collected: list[FeedItem] = []
    for spec in feeds:
        name = spec.get("name") or spec.get("title") or "Unknown"
        url = spec.get("url")
        if not url:
            continue
        per_cap = spec.get("max_per_feed")
        cap = int(per_cap) if isinstance(per_cap, int) else default_per_feed

        xml = fetch_feed_xml(url, http_timeout)
        if not xml:
            continue
        parsed = feedparser.parse(xml)
        batch: list[FeedItem] = []
        for entry in parsed.entries or []:
            link = entry_link(entry)
            if not link:
                continue
            dt = entry_datetime(entry)
            if dt is None or dt < cutoff:
                continue
            batch.append(
                FeedItem(
                    title=entry_title(entry),
                    link=link,
                    source=str(name),
                    published=dt,
                )
            )
        batch.sort(key=lambda x: x.published, reverse=True)
        collected.extend(batch[:cap])
    return collected


def dedupe_and_cap(items: list[FeedItem], max_total: int) -> list[FeedItem]:
    best: dict[str, FeedItem] = {}
    for it in items:
        key = normalize_url(it.link)
        if not key:
            continue
        prev = best.get(key)
        if prev is None or it.published > prev.published:
            best[key] = it
    merged = sorted(best.values(), key=lambda x: x.published, reverse=True)
    return merged[:max_total]


def format_digest_lines(items: list[FeedItem], heading: str) -> str:
    lines = [heading, ""]
    current_source = None
    for it in items:
        if it.source != current_source:
            current_source = it.source
            lines.append(f"【{current_source}】")
        lines.append(f"· {it.title}")
        lines.append(f"  {it.link}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def trim_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    truncated = text[: max_chars - 80].rstrip()
    truncated += "\n\n… 內容過長已截斷，請到 config/sources.yaml 調整 MAX_TOTAL / MAX_PER_FEED。"
    return truncated, True


def feishu_text_payload(text: str) -> dict:
    return {"msg_type": "text", "content": {"text": text}}


def post_lark_webhook(webhook_url: str, payload: dict, timeout: int) -> None:
    r = requests.post(webhook_url, json=payload, timeout=timeout)
    r.raise_for_status()
    try:
        data = r.json()
    except json.JSONDecodeError:
        print("[error] webhook response is not JSON", file=sys.stderr)
        raise SystemExit(1)
    code = data.get("code")
    if code is not None and code != 0:
        print(f"[error] Feishu API: {data}", file=sys.stderr)
        raise SystemExit(1)
    if data.get("StatusCode") not in (None, 0):
        print(f"[error] Feishu webhook: {data}", file=sys.stderr)
        raise SystemExit(1)


def summarize_cn(items: list[FeedItem], api_key: str, base_url: str, model: str, timeout: int) -> str | None:
    lines = [f"- {it.title} | {it.link}" for it in items[:15]]
    user = "以下是今日 AI 相關資訊標題與連結，請用 3～6 句**繁體中文**寫成一段「今日重點」摘要，語氣簡潔、不要編號、不要重複列出所有標題：\n" + "\n".join(lines)
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是專業的科技新聞編輯，只根據提供的標題與連結做保守摘要，不臆測未提及的細節。"},
            {"role": "user", "content": user},
        ],
        "max_tokens": 400,
        "temperature": 0.3,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        choices = data.get("choices") or []
        if not choices:
            print("[warn] LLM returned no choices", file=sys.stderr)
            return None
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            return None
        return content.strip()
    except (requests.RequestException, KeyError, ValueError) as e:
        print(f"[warn] LLM summary failed: {e}", file=sys.stderr)
        return None


def main() -> None:
    webhook = os.environ.get("LARK_WEBHOOK_URL", "").strip()
    if not webhook:
        print("[error] LARK_WEBHOOK_URL is required", file=sys.stderr)
        raise SystemExit(1)

    root = repo_root()
    raw_sources = os.environ.get("SOURCES_PATH", "config/sources.yaml")
    sources_path = Path(raw_sources)
    if not sources_path.is_absolute():
        sources_path = (root / sources_path).resolve()

    lookback_h = env_int("LOOKBACK_HOURS", 36)
    max_per_feed = env_int("MAX_PER_FEED", 5)
    max_total = env_int("MAX_TOTAL", 35)
    http_timeout = env_int("HTTP_TIMEOUT", 25)
    max_message_chars = env_int("MAX_MESSAGE_CHARS", 12000)

    now = datetime.now(tz=SHANGHAI)
    cutoff = now - timedelta(hours=lookback_h)
    heading = f"📰 AI 資訊早報（{now.strftime('%Y-%m-%d %H:%M')} Asia/Shanghai）"

    feeds = load_sources(sources_path)
    if not feeds:
        print(f"[error] no feeds in {sources_path}", file=sys.stderr)
        raise SystemExit(1)

    raw_items = collect_items(feeds, cutoff, max_per_feed, http_timeout)
    items = dedupe_and_cap(raw_items, max_total)

    summary_block = ""
    enable_llm = env_bool("ENABLE_LLM_SUMMARY", False)
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if enable_llm and api_key and items:
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip()
        blurb = summarize_cn(items, api_key, base_url, model, http_timeout)
        if blurb:
            summary_block = f"✨ 今日重點（AI 摘要）\n{blurb}\n\n{'—' * 12}\n\n"

    if not items:
        body = summary_block + "（這段期間內沒有抓到符合時間範圍的 RSS 條目，或來源暫時無法連線。）"
    else:
        body = summary_block + format_digest_lines(items, heading)

    body, _ = trim_text(body, max_message_chars)
    payload = feishu_text_payload(body)
    post_lark_webhook(webhook, payload, http_timeout)

    print(f"Posted digest with {len(items)} items to Feishu/Lark.")


if __name__ == "__main__":
    main()
