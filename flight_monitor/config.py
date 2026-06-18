from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    price_source: str
    kiwi_api_key: str | None
    kiwi_base_url: str
    browser_executable_path: str | None
    browser_timeout_seconds: int
    browser_headless: bool
    ils_per_usd: Decimal
    telegram_bot_token: str
    telegram_chat_id: str
    routes_file: Path
    prices_csv: Path
    default_adults: int
    default_currency: str
    default_alert_threshold: Decimal
    default_max_results: int
    notify_every_check: bool
    check_interval_seconds: int
    request_timeout_seconds: int


@dataclass(frozen=True)
class Route:
    id: str
    name: str
    enabled: bool
    trip_type: str
    origin: str
    destination: str
    departure_date: str
    return_date: str | None
    adults: int
    currency: str
    alert_threshold: Decimal
    max_results: int
    search_url: str

    @property
    def is_round_trip(self) -> bool:
        return self.trip_type == "round-trip"


def load_settings() -> Settings:
    load_dotenv()
    price_source = _price_source(_env("PRICE_SOURCE", "kiwi_browser"))

    return Settings(
        price_source=price_source,
        kiwi_api_key=_kiwi_api_key(price_source),
        kiwi_base_url=_env("KIWI_BASE_URL", "https://api.tequila.kiwi.com").rstrip("/"),
        browser_executable_path=_optional_env("BROWSER_EXECUTABLE_PATH"),
        browser_timeout_seconds=_positive_int("BROWSER_TIMEOUT_SECONDS", "60"),
        browser_headless=_bool_value("BROWSER_HEADLESS", _env("BROWSER_HEADLESS", "true")),
        ils_per_usd=_decimal("ILS_PER_USD", "3.60"),
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_required("TELEGRAM_CHAT_ID"),
        routes_file=Path(_env("ROUTES_FILE", "routes.json")),
        prices_csv=Path(_env("PRICES_CSV", "prices.csv")),
        default_adults=_positive_int("ADULTS", "1"),
        default_currency=_env("CURRENCY", "USD").strip().upper(),
        default_alert_threshold=_decimal("ALERT_THRESHOLD", "1000"),
        default_max_results=_positive_int("MAX_RESULTS", "20"),
        notify_every_check=_bool_value("NOTIFY_EVERY_CHECK", _env("NOTIFY_EVERY_CHECK", "true")),
        check_interval_seconds=_positive_int("CHECK_INTERVAL_SECONDS", "7200"),
        request_timeout_seconds=_positive_int("REQUEST_TIMEOUT_SECONDS", "30"),
    )


def load_routes(settings: Settings) -> list[Route]:
    if not settings.routes_file.exists():
        raise ValueError(f"Routes file not found: {settings.routes_file}")

    with settings.routes_file.open("r", encoding="utf-8") as routes_file:
        payload = json.load(routes_file)

    raw_routes = payload.get("routes") if isinstance(payload, dict) else payload
    if not isinstance(raw_routes, list):
        raise ValueError("routes.json must contain a list or an object with a 'routes' list")

    routes = [_route_from_json(raw_route, settings) for raw_route in raw_routes]
    route_ids = [route.id for route in routes]
    duplicate_ids = sorted({route_id for route_id in route_ids if route_ids.count(route_id) > 1})
    if duplicate_ids:
        raise ValueError(f"Duplicate route ids in routes.json: {', '.join(duplicate_ids)}")
    return routes


def _route_from_json(raw_route: Any, settings: Settings) -> Route:
    if not isinstance(raw_route, dict):
        raise ValueError("Each route must be a JSON object")

    origin = _iata_code_value("origin", _required_field(raw_route, "origin"))
    destination = _iata_code_value("destination", _required_field(raw_route, "destination"))
    departure_date = _date_value("departure_date", _required_field(raw_route, "departure_date"))
    return_date = _optional_date_value("return_date", raw_route.get("return_date"))
    trip_type = _trip_type(raw_route.get("trip_type"), return_date)

    if trip_type == "round-trip" and not return_date:
        raise ValueError(f"Route {origin}-{destination} is round-trip but return_date is missing")
    if trip_type == "one-way" and return_date:
        raise ValueError(f"Route {origin}-{destination} is one-way but return_date is set")
    if return_date and departure_date > return_date:
        raise ValueError(f"Route {origin}-{destination}: departure_date must be before return_date")

    adults = _positive_int_value("adults", raw_route.get("adults", settings.default_adults))
    currency = _currency_value("currency", raw_route.get("currency", settings.default_currency))
    alert_threshold = _decimal_value("alert_threshold", raw_route.get("alert_threshold", settings.default_alert_threshold))
    max_results = _positive_int_value("max_results", raw_route.get("max_results", settings.default_max_results))
    route_id = str(raw_route.get("id") or _generated_route_id(origin, destination, departure_date, return_date, adults)).strip()
    if not route_id:
        raise ValueError(f"Route {origin}-{destination}: id must not be blank")

    route = Route(
        id=route_id,
        name=str(raw_route.get("name") or route_id).strip(),
        enabled=_bool_value("enabled", raw_route.get("enabled", True)),
        trip_type=trip_type,
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        adults=adults,
        currency=currency,
        alert_threshold=alert_threshold,
        max_results=max_results,
        search_url=str(raw_route.get("search_url") or "").strip(),
    )

    if not route.search_url:
        return replace(route, search_url=build_search_url(route))
    return route


def build_search_url(route: Route) -> str:
    dates = route.departure_date
    if route.return_date:
        dates = f"{dates}/{route.return_date}"
    return f"https://www.kiwi.com/en/search/results/{route.origin}/{route.destination}/{dates}"


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _optional_env(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    if value.startswith("your_"):
        raise ValueError(f"Environment variable {name} still contains a placeholder value")
    return value


def _kiwi_api_key(price_source: str) -> str | None:
    value = os.getenv("KIWI_API_KEY", "").strip()
    if price_source == "kiwi_api":
        if not value:
            raise ValueError("Missing required environment variable: KIWI_API_KEY")
        if value.startswith("your_"):
            raise ValueError("Environment variable KIWI_API_KEY still contains a placeholder value")
        return value
    if not value or value.startswith("your_"):
        return None
    return value


def _price_source(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in {"kiwi_browser", "kiwi_api"}:
        raise ValueError("PRICE_SOURCE must be 'kiwi_browser' or 'kiwi_api'")
    return normalized


def _required_field(raw_route: dict[str, Any], name: str) -> Any:
    value = raw_route.get(name)
    if value is None or str(value).strip() == "":
        raise ValueError(f"Route is missing required field: {name}")
    return value


def _iata_code_value(name: str, raw_value: Any) -> str:
    value = str(raw_value).strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError(f"{name} must be a 3-letter IATA code")
    return value


def _currency_value(name: str, raw_value: Any) -> str:
    value = str(raw_value).strip().upper()
    if len(value) != 3 or not value.isalpha():
        raise ValueError(f"{name} must be a 3-letter currency code")
    return value


def _date_value(name: str, raw_value: Any) -> str:
    value = str(raw_value).strip()
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date like 2026-09-15") from exc
    return value


def _optional_date_value(name: str, raw_value: Any) -> str | None:
    if raw_value is None or str(raw_value).strip() == "":
        return None
    return _date_value(name, raw_value)


def _trip_type(raw_value: Any, return_date: str | None) -> str:
    if raw_value is None or str(raw_value).strip() == "":
        return "round-trip" if return_date else "one-way"
    value = str(raw_value).strip().lower().replace("_", "-")
    if value not in {"one-way", "round-trip"}:
        raise ValueError("trip_type must be 'one-way' or 'round-trip'")
    return value


def _bool_value(name: str, raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    value = str(raw_value).strip().lower()
    if value in {"true", "1", "yes", "y"}:
        return True
    if value in {"false", "0", "no", "n"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _positive_int(name: str, default: str) -> int:
    return _positive_int_value(name, _env(name, default))


def _positive_int_value(name: str, raw_value: Any) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _decimal(name: str, default: str) -> Decimal:
    return _decimal_value(name, _env(name, default))


def _decimal_value(name: str, raw_value: Any) -> Decimal:
    try:
        value = Decimal(raw_value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a valid decimal number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be greater than 0")
    return value


def _generated_route_id(
    origin: str,
    destination: str,
    departure_date: str,
    return_date: str | None,
    adults: int,
) -> str:
    return_date_part = return_date or "one-way"
    return f"{origin}-{destination}-{departure_date}-{return_date_part}-{adults}adults".lower()
