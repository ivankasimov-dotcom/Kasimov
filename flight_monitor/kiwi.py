from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

from .config import Route, Settings
from .models import FlightOffer

logger = logging.getLogger(__name__)


class KiwiAPIError(RuntimeError):
    """Raised when Kiwi Tequila returns an unusable API response."""


class KiwiClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.kiwi_api_key:
            raise KiwiAPIError("KIWI_API_KEY is required when PRICE_SOURCE=kiwi_api")
        self.settings = settings
        self.session = requests.Session()

    def search_offers(self, route: Route) -> list[FlightOffer]:
        url = f"{self.settings.kiwi_base_url}/v2/search"
        params: dict[str, Any] = {
            "fly_from": route.origin,
            "fly_to": route.destination,
            "date_from": _kiwi_date(route.departure_date),
            "date_to": _kiwi_date(route.departure_date),
            "adults": route.adults,
            "curr": route.currency,
            "sort": "price",
            "asc": 1,
            "limit": route.max_results,
            "one_for_city": 0,
            "one_per_date": 0,
        }
        if route.return_date:
            params["return_from"] = _kiwi_date(route.return_date)
            params["return_to"] = _kiwi_date(route.return_date)

        response = self.session.get(
            url,
            headers={"apikey": self.settings.kiwi_api_key},
            params=params,
            timeout=self.settings.request_timeout_seconds,
        )
        self._raise_for_status(response, "Kiwi Tequila search request failed")

        payload = response.json()
        data = payload.get("data", [])
        if not isinstance(data, list):
            raise KiwiAPIError("Kiwi Tequila response data is not a list")

        offers = [offer for raw_offer in data if (offer := _extract_offer(raw_offer, route))]
        logger.debug("Parsed %s Kiwi offers for route %s", len(offers), route.id)
        return offers

    @staticmethod
    def _raise_for_status(response: requests.Response, message: str) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise KiwiAPIError(f"{message}: {response.status_code} {response.text}") from exc


def _extract_offer(raw_offer: dict[str, Any], route: Route) -> FlightOffer | None:
    amount = _decimal_or_none(raw_offer.get("price"))
    if amount is None:
        logger.warning("Skipping Kiwi offer with invalid price: %r", raw_offer.get("price"))
        return None

    segments = [segment for segment in raw_offer.get("route", []) if isinstance(segment, dict)]
    outbound_segments = [segment for segment in segments if int(segment.get("return", 0) or 0) == 0]
    return_segments = [segment for segment in segments if int(segment.get("return", 0) or 0) == 1]
    first_outbound = outbound_segments[0] if outbound_segments else {}
    first_return = return_segments[0] if return_segments else {}

    carrier_codes = _carrier_codes(raw_offer, segments)
    airline_code = carrier_codes[0] if carrier_codes else ""
    flight_number = _flight_number(first_outbound)
    return FlightOffer(
        amount=amount,
        currency=route.currency,
        offer_id=str(raw_offer.get("id") or raw_offer.get("booking_token") or "unknown"),
        airline_code=airline_code,
        airline_names=carrier_codes,
        carrier_codes=carrier_codes,
        flight_number=flight_number,
        departure_at=_string_or_none(first_outbound.get("local_departure") or raw_offer.get("local_departure")),
        return_at=_string_or_none(first_return.get("local_departure")),
        outbound_stops=_stops(outbound_segments),
        return_stops=_stops(return_segments) if route.return_date else None,
        deep_link=_string_or_none(raw_offer.get("deep_link")),
    )


def find_minimum_offer(offers: list[FlightOffer]) -> FlightOffer | None:
    if not offers:
        return None
    return min(offers, key=lambda offer: offer.amount)


def _kiwi_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d/%m/%Y")


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _string_or_none(value: Any) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _carrier_codes(raw_offer: dict[str, Any], segments: list[dict[str, Any]]) -> list[str]:
    codes = [str(code).strip().upper() for code in raw_offer.get("airlines", []) if str(code).strip()]
    for segment in segments:
        code = str(segment.get("airline") or "").strip().upper()
        if code:
            codes.append(code)
    return sorted(set(codes))


def _flight_number(segment: dict[str, Any]) -> str:
    airline = str(segment.get("airline") or "").strip().upper()
    number = str(segment.get("flight_no") or "").strip()
    if airline and number:
        return f"{airline}{number}"
    return number


def _stops(segments: list[dict[str, Any]]) -> int | None:
    if not segments:
        return None
    return max(0, len(segments) - 1)
