"""Sync the official quarterly house price index for Bulgaria's six largest cities."""

from __future__ import annotations

import logging
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation

import requests
from bs4 import BeautifulSoup
from supabase import Client, create_client

from config import load_settings

TABLE_NAME = "city_price_snapshots"
SOURCE_URL = "https://www.nsi.bg/index.php/statistical-data/98/328"
SOURCE_NAME = "NSI"
REQUEST_TIMEOUT_SECONDS = 40
USER_AGENT = "BezAgenciaNewsSync/1.0 (+https://github.com/Trapmoney032/Newaddon)"

MARKET = "sale"
PROPERTY_TYPE = "dwellings"
BASE_YEAR = 2025

CITY_NAMES = {
    "София": "Sofia",
    "Пловдив": "Plovdiv",
    "Варна": "Varna",
    "Бургас": "Burgas",
    "Русе": "Ruse",
    "Стара Загора": "Stara Zagora",
}
DWELLING_STATUS = {
    "H.1.": "total",
    "H.1.1.": "new",
    "H.1.2.": "existing",
}
QUARTERS = {"I": 1, "II": 2, "III": 3, "IV": 4}
ON_CONFLICT = (
    "city,market,property_type,dwelling_status,period_year,period_quarter,source_name"
)

logger = logging.getLogger("price_sync")


def clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def fetch_page() -> str:
    response = requests.get(
        SOURCE_URL,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.text


def parse_period(table: BeautifulSoup) -> tuple[int, list[int]]:
    header_rows = table.find("thead").find_all("tr")
    if len(header_rows) < 2:
        raise RuntimeError("NSI table is missing the year and quarter headers")

    year = None
    for cell in header_rows[0].find_all("th"):
        text = clean(cell.get_text(" ", strip=True))
        if re.fullmatch(r"20\d{2}", text):
            year = int(text)
    quarters: list[int] = []
    for cell in header_rows[1].find_all("th"):
        match = re.search(r"\b(IV|III|II|I)\b", clean(cell.get_text(" ", strip=True)))
        if match:
            quarters.append(QUARTERS[match.group(1)])
    if year is None or not quarters:
        raise RuntimeError("NSI table did not include a year and quarter")
    return year, quarters


def index_table(soup: BeautifulSoup) -> BeautifulSoup:
    for table in soup.select("table.nsit2"):
        caption = clean(table.get_text(" ", strip=True))
        if "шестте града" in caption and "H.1." in caption:
            return table
    raise RuntimeError("Could not find the six-city house price index table")


def parse_snapshots(html: str) -> list[dict[str, object]]:
    soup = BeautifulSoup(html, "html.parser")
    table = index_table(soup)
    year, quarters = parse_period(table)
    snapshots: list[dict[str, object]] = []
    current_city: str | None = None

    for row in table.find("tbody").find_all("tr"):
        cells = [clean(cell.get_text(" ", strip=True)) for cell in row.find_all("td")]
        if len(cells) < 4:
            continue
        city_label, code = cells[0], cells[1]
        if city_label:
            current_city = CITY_NAMES.get(city_label)
            if current_city is None:
                logger.warning("Skipping unrecognised city label: %s", city_label)
                continue
        if current_city is None or code not in DWELLING_STATUS:
            continue

        values = cells[3 : 3 + len(quarters)]
        for quarter, raw_value in zip(quarters, values):
            if raw_value in {"", "/", "-", "..."}:
                logger.info(
                    "Skipped suppressed value for %s %s %s Q%s",
                    current_city,
                    DWELLING_STATUS[code],
                    year,
                    quarter,
                )
                continue
            try:
                index_value = Decimal(raw_value.replace(",", "."))
            except InvalidOperation:
                logger.warning(
                    "Skipped unreadable value %r for %s %s",
                    raw_value,
                    current_city,
                    code,
                )
                continue
            snapshots.append(
                {
                    "city": current_city,
                    "market": MARKET,
                    "property_type": PROPERTY_TYPE,
                    "dwelling_status": DWELLING_STATUS[code],
                    "period_year": year,
                    "period_quarter": quarter,
                    "index_value": str(index_value),
                    "base_year": BASE_YEAR,
                    "source_name": SOURCE_NAME,
                    "source_url": SOURCE_URL,
                }
            )

    if not snapshots:
        raise RuntimeError("NSI page was fetched but no city index values were parsed")
    return snapshots


def upsert_snapshots(client: Client, snapshots: list[dict[str, object]]) -> None:
    client.table(TABLE_NAME).upsert(snapshots, on_conflict=ON_CONFLICT).execute()


def count_by_city(client: Client) -> dict[str, int]:
    result = (
        client.table(TABLE_NAME)
        .select("city")
        .eq("source_name", SOURCE_NAME)
        .execute()
    )
    return dict(Counter(row["city"] for row in result.data))


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        settings = load_settings()
        snapshots = parse_snapshots(fetch_page())
    except Exception:
        logger.exception("City price index could not be parsed")
        return 1

    try:
        client = create_client(settings.supabase_url, settings.supabase_service_key)
        upsert_snapshots(client, snapshots)
        counts = count_by_city(client)
    except Exception:
        logger.exception("City price index could not be saved")
        return 1

    logger.info("Upserted %s index rows", len(snapshots))
    for city in CITY_NAMES.values():
        logger.info("%s: %s", city, counts.get(city, 0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
