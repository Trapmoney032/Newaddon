"""Daily ingestion of Bulgarian home sale and rent price news into Supabase.

A headline is kept only when it is about the price of homes for sale or rent
in Bulgaria. Company profiles, commercial property, and foreign stories are left out.
"""

from __future__ import annotations

import calendar
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup
from supabase import Client, create_client

from config import load_settings

TABLE_NAME = "financial_news"
REQUEST_TIMEOUT_SECONDS = 30
UPSERT_BATCH_SIZE = 50
USER_AGENT = "BezAgenciaNewsSync/1.0 (+https://github.com/Trapmoney032/Newaddon)"

logger = logging.getLogger("news_sync")


@dataclass(frozen=True)
class Feed:
    source_name: str
    url: str
    # Used when a headline does not match a more specific topic.
    # None means the headline is skipped unless a topic matches.
    default_category: str | None


# More specific topics are listed first. A dedicated property feed still
# keeps general housing headlines; a broad news feed does not.
TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "mortgages",
        (
            "ипотек",
            "жилищен кредит",
            "жилищни кредит",
            "жилищните кредит",
            "лихвена статистика",
            "лихвен процент",
            "euribor",
            "евробор",
        ),
    ),
    (
        "home_costs",
        (
            "нотариус",
            "нотариал",
            "етажна собственост",
            "такса смет",
            "такса битов",
            "данък сград",
            "данък върху недвижим",
            "местен данък",
            "саниран",
        ),
    ),
    (
        "real_estate",
        (
            "имот",
            "жилищ",
            "наем",
            "апартамент",
            "строител",
        ),
    ),
)

HOME_COST_CONTEXT = ("жилищ", "имот", "апартамент", "дом", "вход", "сград")
HOME_COST_CONTEXT_KEYWORDS = (
    "ремонт",
    "поддръж",
    "сметк",
    "отоплен",
    "парно",
    "застрах",
    "данъчн",
)

FEEDS: tuple[Feed, ...] = (
    Feed(
        source_name="Investor.bg",
        url="https://www.investor.bg/rss/c/534-novini",
        default_category=None,
    ),
    Feed(
        source_name="Investor.bg",
        url="https://www.investor.bg/rss/c/109-imoti",
        default_category=None,
    ),
    Feed(
        source_name="Investor.bg",
        url="https://www.investor.bg/rss/c/536-finansi",
        default_category=None,
    ),
    Feed(
        source_name="Investor.bg",
        url="https://www.investor.bg/rss/c/514-ikonomika-i-politika",
        default_category=None,
    ),
    Feed(
        source_name="Economic.bg",
        url="https://www.economic.bg/rss/ikonomika.xml",
        default_category=None,
    ),
    Feed(
        source_name="Capital.bg",
        url="https://www.capital.bg/rss/",
        default_category=None,
    ),
    Feed(
        source_name="BNB",
        url="https://www.bnb.bg/AboutUs/PressOffice/PORSS/index.htm?getRSS=1&lang=BG&dRubric=Statistics&cat=2",
        default_category=None,
    ),
)

# Short explainers written for BezAgencia. The URL is the official source.
TIPS: tuple[dict[str, str], ...] = (
    {
        "title": (
            "Данъкът при покупка на имот зависи от общината. "
            "Ставката е между 0,1% и 3% и се смята върху по-високата от договорената цена и данъчната оценка."
        ),
        "source_name": "Министерство на финансите",
        "url": "https://www.minfin.bg/bg/784",
        "category": "home_costs",
    },
    {
        "title": (
            "Месечната вноска по кредита не е целият разход за дома. "
            "Към нея обикновено се добавят застраховка на имота, данък върху имота, такса смет и поддръжка на входа."
        ),
        "source_name": "BezAgencia",
        "url": "https://www.minfin.bg/bg/784#bezagencia-monthly-home-cost",
        "category": "home_costs",
    },
    {
        "title": (
            "В началото на един жилищен кредит по-голямата част от вноската е лихва. "
            "С времето делът на главницата расте, затова една и съща вноска изплаща все повече от заема."
        ),
        "source_name": "BezAgencia",
        "url": "https://www.bnb.bg/Statistics/StMonetaryInterestRate/index.htm#bezagencia-annuity",
        "category": "mortgages",
    },
)


def clean_text(value: str | None) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def clean_url(value: str | None) -> str:
    return " ".join(unescape(value or "").split())


def published_at(entry: Any) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    timestamp = calendar.timegm(parsed)
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


PLACES = (
    "софия",
    "пловдив",
    "варна",
    "бургас",
    "русе",
    "стара загора",
    "плевен",
    "българи",
    "у нас",
    "в страната",
)
HOMES = ("апартамент", "жилищ", "къщ", "двустаен", "тристаен", "наем", "имот")
PRICE_WORDS = ("цен", "поскъп", "поевтин", "наем", "продажб", "сделк")
BLOCKED_SOURCES = ("imot.bg", "imoti.net", "imoti", "airbnb")
FOREIGN = (
    "европ",
    "сащ",
    "щати",
    "великобритания",
    "англия",
    "германия",
    "франция",
    "испания",
    "гърция",
    "италия",
    "полша",
    "румъния",
    "еврозон",
)
NOISE = ("всс", "кандидат", "бейби", "старческ", "строителен бизнес")


def has_word(text: str, stem: str, endings: tuple[str, ...]) -> bool:
    start = 0
    while True:
        index = text.find(stem, start)
        if index < 0:
            return False
        if index == 0 or not text[index - 1].isalpha():
            rest = text[index + len(stem) :]
            for ending in endings:
                if not rest.startswith(ending):
                    continue
                after = rest[len(ending) :]
                if not after or not after[0].isalpha():
                    return True
        start = index + 1


def is_home_price_story(title: str) -> bool:
    """True only for a Bulgarian sale or rent price headline."""
    folded = title.casefold()
    if any(marker in folded for marker in BLOCKED_SOURCES):
        return False
    if any(marker in folded for marker in NOISE):
        return False
    if has_word(folded, "мол", ("", "а", "ът", "ове", "овете")):
        return False
    if has_word(folded, "офис", ("", "а", "ът", "и", "ите")):
        return False
    if any(word in folded for word in ("индустриал", "логистич", "ритейл", "хотел", "склад", "бизнес имот")):
        return False
    if any(marker in folded for marker in FOREIGN) and not any(place in folded for place in PLACES):
        return False
    return (
        any(place in folded for place in PLACES)
        and any(word in folded for word in HOMES)
        and any(word in folded for word in PRICE_WORDS)
    )


def category_for(title: str, default_category: str | None) -> str | None:
    if is_home_price_story(title):
        return "real_estate"
    return None


def normalize_entry(feed: Feed, entry: Any) -> dict[str, str] | None:
    title = clean_text(entry.get("title"))
    url = clean_url(entry.get("link"))
    if not title or not url.lower().startswith(("http://", "https://")):
        return None
    category = category_for(title, feed.default_category)
    if category is None:
        return None

    record: dict[str, str] = {
        "title": title,
        "source_name": feed.source_name,
        "url": url,
        "category": category,
    }
    timestamp = published_at(entry)
    if timestamp:
        record["published_at"] = timestamp
    return record


def fetch_feed(feed: Feed) -> list[dict[str, str]]:
    response = requests.get(
        feed.url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml"},
    )
    response.raise_for_status()

    parsed = feedparser.parse(response.content)
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise RuntimeError(f"RSS parse failed: {getattr(parsed, 'bozo_exception', 'unknown error')}")

    records: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for entry in parsed.entries:
        record = normalize_entry(feed, entry)
        if record is None or record["url"] in seen_urls:
            continue
        seen_urls.add(record["url"])
        records.append(record)
    return records


def upsert_records(client: Client, records: list[dict[str, str]]) -> int:
    written = 0
    for start in range(0, len(records), UPSERT_BATCH_SIZE):
        batch = records[start : start + UPSERT_BATCH_SIZE]
        client.table(TABLE_NAME).upsert(batch, on_conflict="url").execute()
        written += len(batch)
    return written


def sync_feed(client: Client, feed: Feed) -> tuple[int, int]:
    records = fetch_feed(feed)
    if not records:
        logger.info("No matching entries from %s (%s)", feed.source_name, feed.url)
        return 0, 0
    written = upsert_records(client, records)
    logger.info("Upserted %s rows from %s (%s)", written, feed.source_name, feed.url)
    return len(records), written


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        settings = load_settings()
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    client = create_client(settings.supabase_url, settings.supabase_service_key)
    failures = 0
    fetched = 0
    written = 0

    for feed in FEEDS:
        try:
            feed_fetched, feed_written = sync_feed(client, feed)
            fetched += feed_fetched
            written += feed_written
        except Exception:
            failures += 1
            logger.exception("Feed failed, continuing with the rest: %s", feed.url)

    try:
        written += upsert_records(client, list(TIPS))
        logger.info("Upserted %s home tips", len(TIPS))
    except Exception:
        failures += 1
        logger.exception("Home tips failed")

    logger.info(
        "Finished. feeds=%s failed=%s fetched=%s upserted=%s",
        len(FEEDS),
        failures,
        fetched,
        written,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
