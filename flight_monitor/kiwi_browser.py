from __future__ import annotations

import logging
from pathlib import Path
import re
import time
from datetime import datetime
from decimal import Decimal
from typing import Any

from .config import Route, Settings
from .models import FlightOffer

logger = logging.getLogger(__name__)


class KiwiBrowserError(RuntimeError):
    """Raised when the Kiwi browser monitor cannot extract a usable price."""


class KiwiBrowserClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def search_offers(self, route: Route) -> list[FlightOffer]:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise KiwiBrowserError(
                "Playwright is not installed. Run: .venv/bin/python -m pip install -r requirements.txt"
            ) from exc

        timeout_ms = self.settings.browser_timeout_seconds * 1000
        launch_kwargs: dict[str, Any] = {"headless": self.settings.browser_headless}
        if self.settings.browser_executable_path:
            launch_kwargs["executable_path"] = self.settings.browser_executable_path

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(**launch_kwargs)
                try:
                    context = browser.new_context(
                        locale="en-US",
                        timezone_id="Asia/Jerusalem",
                        user_agent=(
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/126.0.0.0 Safari/537.36"
                        ),
                    )
                    page = context.new_page()
                    page.goto(route.search_url, wait_until="domcontentloaded", timeout=timeout_ms)
                    _try_accept_cookies(page)
                    _try_set_currency(page, route.currency)
                    page.wait_for_timeout(3000)
                    body_text = _wait_for_price_text(page, timeout_ms)
                    page.wait_for_timeout(7000)
                    body_text = page.locator("body").inner_text(timeout=timeout_ms)
                    current_url = page.url
                finally:
                    browser.close()
        except PlaywrightTimeoutError as exc:
            raise KiwiBrowserError(f"Timed out waiting for Kiwi search results: {route.search_url}") from exc

        offer = _extract_offer_from_text(body_text, route, current_url, self.settings)
        if not offer:
            raise KiwiBrowserError("Could not extract a flight price from the Kiwi page")
        return [offer]


def _wait_for_price_text(page: Any, timeout_ms: int) -> str:
    deadline = time.monotonic() + timeout_ms / 1000
    last_text = ""
    while time.monotonic() < deadline:
        try:
            last_text = page.locator("body").inner_text(timeout=5000)
        except Exception:
            last_text = ""
        if re.search(r"(?:\$|USD|₪)\s*[0-9][0-9,\s]{2,}", last_text):
            return last_text
        page.wait_for_timeout(3000)

    debug_path = Path("work/kiwi_last_page.txt")
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(last_text, encoding="utf-8")
    raise KiwiBrowserError(f"Timed out waiting for Kiwi search results. Last page text saved to {debug_path}")


def _try_accept_cookies(page: Any) -> None:
    for label in ["Accept all", "Accept", "Agree", "Принять", "Согласен"]:
        try:
            button = page.get_by_role("button", name=re.compile(label, re.IGNORECASE))
            if button.count() > 0:
                button.first.click(timeout=1500)
                return
        except Exception:
            pass
        try:
            text_button = page.get_by_text(label, exact=True)
            if text_button.count() > 0:
                text_button.first.click(timeout=1500)
                return
        except Exception:
            pass


def _try_set_currency(page: Any, currency: str) -> None:
    if currency.upper() != "USD":
        return
    try:
        current_currency = page.get_by_text(re.compile(r"^(ILS|EUR|GBP|USD)$"))
        if current_currency.count() > 0:
            current_currency.first.click(timeout=2500)
            page.wait_for_timeout(500)
    except Exception:
        return

    for selector in [
        lambda: page.get_by_text("USD", exact=True),
        lambda: page.get_by_role("button", name=re.compile(r"\bUSD\b")),
        lambda: page.locator("text=USD"),
    ]:
        try:
            option = selector()
            if option.count() > 0:
                option.first.click(timeout=2500)
                page.wait_for_timeout(3000)
                return
        except Exception:
            continue


def _extract_offer_from_text(text: str, route: Route, url: str, settings: Settings) -> FlightOffer | None:
    price, source, symbol, price_text = _extract_price(text)
    if price is None:
        return None
    source_amount = None
    source_currency = None
    amount = price

    if route.currency == "USD" and symbol == "₪":
        source_amount = price
        source_currency = "ILS"
        amount = price / settings.ils_per_usd
        logger.info(
            "Kiwi rendered ILS; converted %s ILS to %s USD using ILS_PER_USD=%s",
            f"{price:.2f}",
            f"{amount:.2f}",
            f"{settings.ils_per_usd:.4f}",
        )

    details_text = _context_around_price(text, price_text) if price_text else text
    outbound_time, return_time = _extract_departure_times(details_text)
    stop_counts = _extract_stop_counts(details_text)
    outbound_stops = stop_counts[0] if stop_counts else None
    return_stops = stop_counts[1] if len(stop_counts) > 1 else outbound_stops if route.return_date else None

    offer_id = f"kiwi-browser:{route.id}:{source}:{price}"
    return FlightOffer(
        amount=amount,
        currency=route.currency,
        offer_id=offer_id,
        airline_code="",
        airline_names=[],
        carrier_codes=[],
        flight_number="",
        departure_at=_combine_date_time(route.departure_date, outbound_time),
        return_at=_combine_date_time(route.return_date, return_time),
        outbound_stops=outbound_stops,
        return_stops=return_stops,
        deep_link=url,
        source_amount=source_amount,
        source_currency=source_currency,
    )


def _extract_price(text: str) -> tuple[Decimal | None, str, str | None, str | None]:
    labeled_patterns = [
        ("cheapest", r"(?:Cheapest|Самый деш[её]вый)[\s\S]{0,160}?(\$|₪)\s*([0-9][0-9,\s]*)"),
        ("best-option", r"(?:Best option|Лучший вариант)[\s\S]{0,180}?(\$|₪)\s*([0-9][0-9,\s]*)"),
    ]
    for source, pattern in labeled_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            symbol = match.group(1)
            amount_text = match.group(2)
            return _to_decimal(amount_text), source, symbol, f"{symbol} {amount_text.strip()}"

    raw_prices = [
        (symbol, _to_decimal(value))
        for symbol, value in re.findall(r"(\$|₪)\s*([0-9][0-9,\s]*)", text)
    ]
    raw_prices = [(symbol, price) for symbol, price in raw_prices if price is not None]
    if raw_prices:
        logger.warning("Using fallback Kiwi page-min price extraction; verify the result manually if it alerts.")
        symbol, price = min(raw_prices, key=lambda item: item[1])
        return price, "page-min", symbol, f"{symbol} {price:,.0f}"
    return None, "none", None, None


def _context_around_price(text: str, price_text: str) -> str:
    compact_price = re.escape(price_text).replace(r"\ ", r"\s*")
    matches = list(re.finditer(compact_price, text))
    if not matches:
        return text
    # The first occurrence is often the summary tab. The last occurrence is usually the offer card.
    match = matches[-1]
    start = max(0, match.start() - 900)
    end = min(len(text), match.end() + 250)
    return text[start:end]


def _extract_departure_times(text: str) -> tuple[str | None, str | None]:
    times = re.findall(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    formatted = [f"{hour.zfill(2)}:{minute}" for hour, minute in times]
    if not formatted:
        return None, None
    if len(formatted) == 1:
        return formatted[0], None
    return formatted[0], formatted[2] if len(formatted) > 2 else formatted[1]


def _extract_stop_counts(text: str) -> list[int]:
    outbound_start = text.rfind("Outbound")
    inbound_start = text.rfind("Inbound")
    if outbound_start != -1 and inbound_start != -1 and outbound_start < inbound_start:
        counts = [
            _first_stop_count(text[outbound_start:inbound_start]),
            _first_stop_count(text[inbound_start:]),
        ]
        return [count for count in counts if count is not None]

    counts = []
    for match in re.findall(r"(\d+)\s*(?:stop|stops|пересадк[аиу]?)", text, re.IGNORECASE):
        try:
            counts.append(int(match))
        except ValueError:
            continue
    if counts:
        return counts[:2]
    if re.search(r"\b(?:direct|nonstop|прямой)\b", text, re.IGNORECASE):
        return [0]
    return []


def _first_stop_count(text: str) -> int | None:
    match = re.search(r"(\d+)\s*(?:stop|stops|пересадк[аиу]?)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    if re.search(r"\b(?:direct|nonstop|прямой)\b", text, re.IGNORECASE):
        return 0
    return None


def _to_decimal(value: str) -> Decimal | None:
    normalized = value.replace(",", "").replace(" ", "")
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _combine_date_time(date_value: str | None, time_value: str | None) -> str | None:
    if not date_value or not time_value:
        return None
    try:
        datetime.fromisoformat(date_value)
    except ValueError:
        return None
    return f"{date_value}T{time_value}:00"
