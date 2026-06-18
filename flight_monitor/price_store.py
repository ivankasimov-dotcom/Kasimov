from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .config import Route
from .models import FlightOffer


FIELDNAMES = [
    "checked_at",
    "route_id",
    "route_name",
    "trip_type",
    "origin",
    "destination",
    "departure_date",
    "return_date",
    "adults",
    "price",
    "currency",
    "offer_id",
    "airlines",
    "outbound_departure_at",
    "return_departure_at",
    "total_stops",
    "source_price",
    "source_currency",
    "search_url",
]


@dataclass(frozen=True)
class PriceRecord:
    checked_at: str
    route_id: str
    amount: Decimal
    currency: str
    offer_id: str


class PriceStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get_previous_price(self, route_id: str) -> PriceRecord | None:
        if not self.path.exists():
            return None

        previous: PriceRecord | None = None
        with self.path.open("r", encoding="utf-8", newline="") as prices_file:
            reader = csv.DictReader(prices_file)
            for row in reader:
                if row.get("route_id") != route_id:
                    continue
                try:
                    previous = PriceRecord(
                        checked_at=row.get("checked_at", ""),
                        route_id=route_id,
                        amount=Decimal(row.get("price", "")),
                        currency=row.get("currency", ""),
                        offer_id=row.get("offer_id", ""),
                    )
                except (InvalidOperation, TypeError, ValueError):
                    continue
        return previous

    def append_price(self, route: Route, offer: FlightOffer) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        should_write_header = not self.path.exists() or self.path.stat().st_size == 0

        with self.path.open("a", encoding="utf-8", newline="") as prices_file:
            writer = csv.DictWriter(prices_file, fieldnames=FIELDNAMES)
            if should_write_header:
                writer.writeheader()
            writer.writerow(
                {
                    "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "route_id": route.id,
                    "route_name": route.name,
                    "trip_type": route.trip_type,
                    "origin": route.origin,
                    "destination": route.destination,
                    "departure_date": route.departure_date,
                    "return_date": route.return_date or "",
                    "adults": route.adults,
                    "price": f"{offer.amount:.2f}",
                    "currency": offer.currency,
                    "offer_id": offer.offer_id,
                    "airlines": ", ".join(offer.airline_names),
                    "outbound_departure_at": offer.outbound_departure_at or "",
                    "return_departure_at": offer.return_departure_at or "",
                    "total_stops": offer.total_stops,
                    "source_price": f"{offer.source_amount:.2f}" if offer.source_amount is not None else "",
                    "source_currency": offer.source_currency or "",
                    "search_url": offer.deep_link or route.search_url,
                }
            )
