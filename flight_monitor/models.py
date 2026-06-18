from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class FlightOffer:
    amount: Decimal
    currency: str
    offer_id: str
    airline_code: str
    airline_names: list[str]
    carrier_codes: list[str]
    flight_number: str
    departure_at: str | None
    return_at: str | None
    outbound_stops: int | None
    return_stops: int | None
    deep_link: str | None
    expires_at: str | None = None
    source_amount: Decimal | None = None
    source_currency: str | None = None

    @property
    def outbound_departure_at(self) -> str | None:
        return self.departure_at

    @property
    def return_departure_at(self) -> str | None:
        return self.return_at

    @property
    def transfers(self) -> int | None:
        stops = [value for value in [self.outbound_stops, self.return_stops] if value is not None]
        if not stops:
            return None
        return sum(stops)

    @property
    def total_stops(self) -> int:
        return self.transfers or 0
