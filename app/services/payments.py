"""Payment provider boundary. Refund execution calls this only after the reservation commits."""

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote

import httpx

PROVIDER_STATUSES = frozenset({"submitted", "processing", "succeeded", "failed"})


class ProviderUnavailable(Exception):
    """Timeout or no usable response. The operation must be marked unknown, not retried blindly."""


class ProviderPayloadConflict(Exception):
    """The idempotency key already belongs to a different amount or order."""


@dataclass(frozen=True)
class ProviderResult:
    status: str
    provider_reference: str | None = None


@dataclass(frozen=True)
class ProviderRecord:
    """A payment the provider already stored for one idempotency key."""

    status: str
    amount_minor: int
    order_id: str
    provider_reference: str | None = None


class PaymentProvider(Protocol):
    def lookup_refund(self, *, idempotency_key: str) -> ProviderRecord | None:
        """Return the stored payment. None means the provider confirmed it has no record.

        Raise ProviderUnavailable when the lookup itself times out or returns a server error.
        """

    def create_refund(
        self, *, idempotency_key: str, amount_minor: int, order_id: str
    ) -> ProviderResult:
        """Send one refund. The same idempotency_key must be reused for that operation."""


class HttpPaymentProvider:
    """POST /refunds on the configured mock provider. Amounts stay integer paise."""

    def __init__(self, base_url: str, client: httpx.Client | None = None, timeout: float = 5.0):
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def create_refund(
        self, *, idempotency_key: str, amount_minor: int, order_id: str
    ) -> ProviderResult:
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
            raise ValueError("amount_minor must be a positive int number of paise")
        body = {
            "idempotency_key": idempotency_key,
            "amount_minor": amount_minor,
            "order_id": order_id,
        }
        try:
            response = self._client.post("/refunds", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("payment provider timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("payment provider request failed") from exc
        if response.status_code == 409:
            raise ProviderPayloadConflict("idempotency key reused with a different payload")
        if response.status_code >= 500:
            raise ProviderUnavailable(f"payment provider status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("payment provider returned no JSON") from exc
        status = payload.get("status")
        if status not in PROVIDER_STATUSES:
            raise ProviderUnavailable("payment provider returned an unknown status")
        reference = payload.get("provider_reference")
        if reference is not None and not isinstance(reference, str):
            raise ProviderUnavailable("payment provider reference was not a string")
        reported_amount = payload.get("amount_minor")
        reported_order = payload.get("order_id")
        if (
            isinstance(reported_amount, bool)
            or not isinstance(reported_amount, int)
            or reported_amount != amount_minor
            or not isinstance(reported_order, str)
            or reported_order != order_id
        ):
            raise ProviderUnavailable("payment provider response did not match the reserved refund")
        return ProviderResult(status=status, provider_reference=reference)

    def lookup_refund(self, *, idempotency_key: str) -> ProviderRecord | None:
        try:
            response = self._client.get(f"/refunds/{quote(idempotency_key, safe='')}")
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable("payment provider lookup timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("payment provider lookup failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code >= 500:
            raise ProviderUnavailable(f"payment provider lookup status {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("payment provider lookup returned no JSON") from exc
        status = payload.get("status")
        amount = payload.get("amount_minor")
        order_id = payload.get("order_id")
        reference = payload.get("provider_reference")
        if status not in PROVIDER_STATUSES or isinstance(amount, bool) or not isinstance(amount, int):
            raise ProviderUnavailable("payment provider lookup returned an unusable record")
        if not isinstance(order_id, str) or (reference is not None and not isinstance(reference, str)):
            raise ProviderUnavailable("payment provider lookup returned an unusable record")
        return ProviderRecord(
            status=status,
            amount_minor=amount,
            order_id=order_id,
            provider_reference=reference,
        )
