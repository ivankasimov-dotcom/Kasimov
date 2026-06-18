from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html import escape
import logging
import sys
import time
from decimal import Decimal
from typing import Protocol

import requests

from .config import Route, Settings, load_routes, load_settings
from .kiwi import KiwiAPIError, KiwiClient, find_minimum_offer
from .kiwi_browser import KiwiBrowserClient, KiwiBrowserError
from .models import FlightOffer
from .price_store import PriceRecord, PriceStore
from .telegram import TelegramAPIError, TelegramNotifier


class PriceClient(Protocol):
    def search_offers(self, route: Route) -> list[FlightOffer]:
        ...


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor flight prices via Kiwi.")
    parser.add_argument("--once", action="store_true", help="Run one check and exit.")
    parser.add_argument("--test-telegram", action="store_true", help="Send a Telegram test message and exit.")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    _configure_logging(verbose=args.verbose)

    try:
        settings = load_settings()
    except ValueError as exc:
        logging.error("Configuration error: %s", exc)
        sys.exit(2)

    if args.test_telegram:
        _send_telegram_test(settings)
        return

    try:
        routes = load_routes(settings)
    except ValueError as exc:
        logging.error("Configuration error: %s", exc)
        sys.exit(2)

    enabled_routes = [route for route in routes if route.enabled]
    if not enabled_routes:
        logging.warning("No enabled routes found in %s", settings.routes_file)
        return

    if args.once:
        _run_once(settings, enabled_routes)
        return

    logging.info(
        "Starting flight price monitor for %s routes. Interval: %s seconds",
        len(enabled_routes),
        settings.check_interval_seconds,
    )
    while True:
        _run_once(settings, enabled_routes)
        time.sleep(settings.check_interval_seconds)


def _run_once(settings: Settings, routes: list[Route]) -> None:
    price_client = _make_price_client(settings)
    notifier = TelegramNotifier(settings)
    price_store = PriceStore(settings.prices_csv)

    for route in routes:
        _check_route(route, price_client, notifier, price_store, settings.notify_every_check)


def _make_price_client(settings: Settings) -> PriceClient:
    if settings.price_source == "kiwi_api":
        return KiwiClient(settings)
    return KiwiBrowserClient(settings)


def _send_telegram_test(settings: Settings) -> None:
    notifier = TelegramNotifier(settings)
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    message = "\n".join(
        [
            "<b>Тест Flight Monitor</b>",
            "Telegram подключен правильно.",
            f"Время проверки: {checked_at} UTC",
            "Автоматическая проверка в GitHub Actions настроена каждые 2 часа.",
        ]
    )
    try:
        notifier.send_message(message)
    except (TelegramAPIError, requests.RequestException) as exc:
        logging.exception("Telegram test failed: %s", exc)
        sys.exit(1)
    logging.info("Telegram test message sent.")


def _check_route(
    route: Route,
    price_client: PriceClient,
    notifier: TelegramNotifier,
    price_store: PriceStore,
    notify_every_check: bool,
) -> None:
    logging.info(
        "Checking route %s: %s -> %s (%s)",
        route.id,
        route.origin,
        route.destination,
        route.trip_type,
    )

    try:
        offers = price_client.search_offers(route)
        minimum_offer = find_minimum_offer(offers)
    except (KiwiAPIError, KiwiBrowserError, requests.RequestException) as exc:
        logging.exception("Flight price check failed for route %s: %s", route.id, exc)
        if notify_every_check:
            _send_telegram_message(notifier, route, _build_failure_message(route, exc), "failure status")
        return

    if minimum_offer is None:
        logging.info(
            "No flight offers found for route %s: %s -> %s, departure %s",
            route.id,
            route.origin,
            route.destination,
            route.departure_date,
        )
        if notify_every_check:
            _send_telegram_message(notifier, route, _build_no_offer_message(route), "no-offer status")
        return

    previous_price = price_store.get_previous_price(route.id)
    price_store.append_price(route, minimum_offer)

    logging.info(
        "Minimum price found for route %s: %s %s. Previous: %s",
        route.id,
        _format_decimal(minimum_offer.amount),
        minimum_offer.currency,
        _previous_price_label(previous_price),
    )

    if minimum_offer.currency != route.currency:
        logging.warning(
            "Kiwi returned %s, expected %s. Threshold comparison will still use returned amount.",
            minimum_offer.currency,
            route.currency,
        )

    if previous_price is None:
        logging.info("No alert sent for route %s. Baseline price saved to %s.", route.id, price_store.path)
        if notify_every_check:
            message = _build_status_message(route, minimum_offer, previous_price, "Базовая цена сохранена")
            _send_telegram_message(notifier, route, message, "baseline status")
        return

    if previous_price.currency and previous_price.currency != minimum_offer.currency:
        logging.warning(
            "No alert sent for route %s. Previous currency %s differs from current currency %s.",
            route.id,
            previous_price.currency,
            minimum_offer.currency,
        )
        if notify_every_check:
            message = _build_status_message(route, minimum_offer, previous_price, "Валюта изменилась, сравнение пропущено")
            _send_telegram_message(notifier, route, message, "currency status")
        return

    if minimum_offer.amount >= route.alert_threshold:
        logging.info(
            "No alert sent for route %s. Minimum price %s %s is not below threshold %s %s.",
            route.id,
            _format_decimal(minimum_offer.amount),
            minimum_offer.currency,
            _format_decimal(route.alert_threshold),
            route.currency,
        )
        if notify_every_check:
            message = _build_status_message(route, minimum_offer, previous_price, "Цена проверена, выше порога")
            _send_telegram_message(notifier, route, message, "above-threshold status")
        return

    if minimum_offer.amount >= previous_price.amount:
        logging.info(
            "No alert sent for route %s. Minimum price did not drop below previous price %s %s.",
            route.id,
            _format_decimal(previous_price.amount),
            previous_price.currency,
        )
        if notify_every_check:
            message = _build_status_message(route, minimum_offer, previous_price, "Цена проверена, снижения нет")
            _send_telegram_message(notifier, route, message, "no-drop status")
        return

    message = _build_telegram_message(route, minimum_offer, previous_price)
    _send_telegram_message(notifier, route, message, "price-drop alert")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _format_decimal(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_offer_price(offer: FlightOffer) -> str:
    price = f"{_format_decimal(offer.amount)} {offer.currency}"
    if offer.source_amount is not None and offer.source_currency:
        price = f"{price} (Kiwi показал {_format_decimal(offer.source_amount)} {offer.source_currency})"
    return price


def _previous_price_label(previous_price: PriceRecord | None) -> str:
    if previous_price is None:
        return "нет"
    return f"{_format_decimal(previous_price.amount)} {previous_price.currency}"


def _send_telegram_message(notifier: TelegramNotifier, route: Route, message: str, label: str) -> None:
    try:
        notifier.send_message(message)
    except (TelegramAPIError, requests.RequestException) as exc:
        logging.exception("Telegram %s failed for route %s: %s", label, route.id, exc)
        return
    logging.info("Telegram %s sent for route %s.", label, route.id)


def _build_status_message(
    route: Route,
    offer: FlightOffer,
    previous_price: PriceRecord | None,
    status: str,
) -> str:
    lines = [
        "<b>Проверка авиабилетов</b>",
        f"<b>{escape(route.name)}</b>",
        escape(_format_route_line(route)),
        "",
        f"Статус: {escape(status)}",
        f"Цена: <b>{escape(_format_offer_price(offer))}</b>",
        f"Предыдущая: {escape(_previous_price_label(previous_price))}",
        f"Порог: {_format_decimal(route.alert_threshold)} {escape(route.currency)}",
        "",
        f"Даты: {escape(_format_dates(route))}",
        f"Вылет: {escape(_format_departure(offer.outbound_departure_at, route.origin))}",
    ]
    if route.return_date:
        lines.append(f"Обратно: {escape(_format_departure(offer.return_departure_at, route.destination))}")
    lines.extend(
        [
            f"Пересадки: {escape(_format_stops(offer))}",
            "",
            f'<a href="{escape(offer.deep_link or route.search_url, quote=True)}">Открыть поиск Kiwi</a>',
        ]
    )
    return "\n".join(lines)


def _build_no_offer_message(route: Route) -> str:
    return "\n".join(
        [
            "<b>Проверка авиабилетов</b>",
            f"<b>{escape(route.name)}</b>",
            escape(f"{route.origin} -> {route.destination}"),
            "",
            "Статус: предложений не найдено",
            f"Даты: {escape(_format_dates(route))}",
            "",
            f'<a href="{escape(route.search_url, quote=True)}">Открыть поиск Kiwi</a>',
        ]
    )


def _build_failure_message(route: Route, exc: Exception) -> str:
    return "\n".join(
        [
            "<b>Проверка авиабилетов не удалась</b>",
            f"<b>{escape(route.name)}</b>",
            escape(f"{route.origin} -> {route.destination}"),
            "",
            f"Ошибка: {escape(str(exc))}",
            "",
            f'<a href="{escape(route.search_url, quote=True)}">Открыть поиск Kiwi</a>',
        ]
    )


def _build_telegram_message(route: Route, offer: FlightOffer, previous_price: PriceRecord) -> str:
    airlines = ", ".join(offer.airline_names or offer.carrier_codes) or "неизвестно"
    flight = offer.flight_number or offer.airline_code or "неизвестно"

    lines = [
        "<b>Цена на билет снизилась</b>",
        f"<b>{escape(route.name)}</b>",
        escape(_format_route_line(route)),
        "",
        f"Цена: <b>{escape(_format_offer_price(offer))}</b>",
        f"Предыдущая: {_format_decimal(previous_price.amount)} {escape(previous_price.currency)}",
        f"Порог: {_format_decimal(route.alert_threshold)} {escape(route.currency)}",
        "",
        f"Авиакомпания: {escape(airlines)}",
        f"Рейс: {escape(flight)}",
        f"Даты: {escape(_format_dates(route))}",
        f"Вылет: {escape(_format_departure(offer.outbound_departure_at, route.origin))}",
    ]

    if route.return_date:
        lines.append(f"Обратно: {escape(_format_departure(offer.return_departure_at, route.destination))}")

    lines.extend(
        [
            f"Пересадки: {escape(_format_stops(offer))}",
            f"Действует до: {escape(offer.expires_at or 'неизвестно')}",
            f"ID предложения: {escape(offer.offer_id)}",
            "",
            f'<a href="{escape(offer.deep_link or route.search_url, quote=True)}">Открыть поиск Kiwi</a>',
        ]
    )
    return "\n".join(lines)


def _format_route_line(route: Route) -> str:
    trip_label = "туда-обратно" if route.is_round_trip else "в одну сторону"
    return f"{route.origin} -> {route.destination} ({trip_label})"


def _format_dates(route: Route) -> str:
    if route.return_date:
        return f"{route.departure_date} - {route.return_date}"
    return route.departure_date


def _format_departure(departure_at: str | None, fallback_airport: str) -> str:
    if not departure_at:
        return f"{fallback_airport}, время неизвестно"
    try:
        parsed = datetime.fromisoformat(departure_at.replace("Z", "+00:00"))
    except ValueError:
        return departure_at
    return f"{fallback_airport}, {parsed:%Y-%m-%d %H:%M}"


def _format_stops(offer: FlightOffer) -> str:
    if offer.outbound_stops is None and offer.return_stops is None:
        return "неизвестно"
    if offer.return_stops is None:
        return _format_stop_count(offer.outbound_stops or 0)
    return (
        f"туда: {_format_stop_count(offer.outbound_stops or 0)}, "
        f"обратно: {_format_stop_count(offer.return_stops)}"
    )


def _format_stop_count(stops: int) -> str:
    if stops == 0:
        return "без пересадок"
    if stops == 1:
        return "1 пересадка"
    if 2 <= stops <= 4:
        return f"{stops} пересадки"
    return f"{stops} пересадок"
