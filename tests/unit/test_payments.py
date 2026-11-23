"""Timeout, HTTP 500, HTTP 409, and POST body checks become explicit provider outcomes."""

import json

import httpx
import pytest

from app.services.payments import HttpPaymentProvider, ProviderPayloadConflict, ProviderUnavailable


def test_timeout_raises_provider_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    client = httpx.Client(base_url="http://payments.test", transport=httpx.MockTransport(handler))
    provider = HttpPaymentProvider("http://payments.test", client=client)
    try:
        provider.create_refund(idempotency_key="refund-1", amount_minor=100, order_id="order-1")
        raised = False
    except ProviderUnavailable:
        raised = True
    assert raised is True


def test_http_409_is_a_payload_conflict():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["amount_minor"] != 100:
            return httpx.Response(409, json={"detail": "idempotency key reused with a different payload"})
        return httpx.Response(
            200,
            json={
                "status": "succeeded",
                "provider_reference": "mock_1",
                "amount_minor": 100,
                "order_id": "order-1",
            },
        )

    client = httpx.Client(base_url="http://payments.test", transport=httpx.MockTransport(handler))
    provider = HttpPaymentProvider("http://payments.test", client=client)
    provider.create_refund(idempotency_key="refund-1", amount_minor=100, order_id="order-1")
    try:
        provider.create_refund(idempotency_key="refund-1", amount_minor=50, order_id="order-1")
        raised = False
    except ProviderPayloadConflict:
        raised = True
    assert raised is True


def _provider(payload: dict) -> HttpPaymentProvider:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = httpx.Client(base_url="http://payments.test", transport=httpx.MockTransport(handler))
    return HttpPaymentProvider("http://payments.test", client=client)


def _accepted(**overrides: object) -> dict:
    body: dict[str, object] = {
        "status": "succeeded",
        "provider_reference": "mock_1",
        "amount_minor": 100,
        "order_id": "order-1",
    }
    body.update(overrides)
    return body


def test_matching_post_response_is_accepted():
    result = _provider(_accepted()).create_refund(
        idempotency_key="refund-1", amount_minor=100, order_id="order-1"
    )
    assert result.status == "succeeded"
    assert result.provider_reference == "mock_1"


@pytest.mark.parametrize(
    "payload",
    [
        {key: value for key, value in _accepted().items() if key != "amount_minor"},
        {key: value for key, value in _accepted().items() if key != "order_id"},
    ],
)
def test_post_response_missing_amount_or_order_is_unavailable(payload: dict):
    provider = _provider(payload)
    with pytest.raises(ProviderUnavailable):
        provider.create_refund(idempotency_key="refund-1", amount_minor=100, order_id="order-1")


@pytest.mark.parametrize(
    "payload",
    [
        _accepted(amount_minor=99),
        _accepted(amount_minor=True),
        _accepted(order_id="order-2"),
    ],
)
def test_post_response_mismatched_amount_or_order_is_unavailable(payload: dict):
    provider = _provider(payload)
    with pytest.raises(ProviderUnavailable):
        provider.create_refund(idempotency_key="refund-1", amount_minor=100, order_id="order-1")
