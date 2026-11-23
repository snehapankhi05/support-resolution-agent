"""Eligibility, amount and customer-message tests. No database."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.eligibility import RefundFacts, evaluate_refund_eligibility, refundable_amount
from app.services.messages import customer_message, format_inr

NOW = datetime(2026, 11, 19, tzinfo=UTC)
CUSTOMER = "customer-1"


def _facts(**overrides) -> RefundFacts:
    base = {
        "order_status": "delivered",
        "order_customer_id": CUSTOMER,
        "requesting_customer_id": CUSTOMER,
        "delivered_at": NOW - timedelta(days=10),
        "return_requested_at": NOW - timedelta(days=8),
        "return_status": "received",
        "inspection_status": "passed",
        "paid_amount_minor": 249900,
        "refund_committed_minor": 0,
        "returned_items_paid_minor": 249900,
        "policy_found": True,
        "policy_conflict": False,
    }
    base.update(overrides)
    return RefundFacts(**base)


def test_refundable_amount_is_capped_by_what_is_left():
    assert refundable_amount(1000, 400, 1000) == 600
    assert refundable_amount(1000, 0, 250) == 250
    assert refundable_amount(1000, 1000, 1000) == 0


def test_refundable_amount_rejects_non_integers():
    with pytest.raises(TypeError):
        refundable_amount(10.5, 0, 10)  # type: ignore[arg-type]


def test_eligible_full_return():
    decision = evaluate_refund_eligibility(_facts())
    assert decision.eligible is True
    assert decision.amount_minor == 249900
    assert decision.reason == "eligible"
    assert decision.escalate is False


@pytest.mark.parametrize(
    ("overrides", "reason", "escalate"),
    [
        ({"requesting_customer_id": "other"}, "not_owner", False),
        ({"order_status": "shipped", "delivered_at": None}, "not_delivered", False),
        ({"delivered_at": None}, "missing_delivery", True),
        ({"inspection_status": "failed"}, "inspection_failed", True),
        ({"return_requested_at": None, "return_status": None}, "no_return", False),
        (
            {"return_requested_at": NOW - timedelta(days=10) + timedelta(days=31)},
            "return_window",
            False,
        ),
        ({"return_status": "in_transit", "inspection_status": "pending"}, "return_not_received", False),
        ({"inspection_status": "pending"}, "inspection_pending", False),
        ({"policy_found": False}, "no_policy", True),
        ({"policy_conflict": True}, "policy_conflict", True),
        (
            {
                "refund_committed_minor": 249900,
                "existing_refund_status": "succeeded",
                "existing_refund_amount_minor": 249900,
            },
            "already_refunded",
            False,
        ),
    ],
)
def test_ineligible_rules(overrides, reason, escalate):
    decision = evaluate_refund_eligibility(_facts(**overrides))
    assert decision.eligible is False
    assert decision.amount_minor == 0
    assert decision.reason == reason
    assert decision.escalate is escalate


def test_return_on_the_30th_day_is_eligible():
    delivered = NOW - timedelta(days=30)
    decision = evaluate_refund_eligibility(
        _facts(delivered_at=delivered, return_requested_at=delivered + timedelta(days=30))
    )
    assert decision.eligible is True


def test_customer_messages_match_verified_states():
    assert customer_message("awaiting_approval") == "Your refund request is awaiting approval."
    pending = "The payment service accepted your refund request. Processing is still pending."
    assert customer_message("submitted") == pending
    assert customer_message("processing") == pending
    assert customer_message("succeeded", amount_minor=249900) == (
        "Your refund of ₹2,499 has been confirmed."
    )
    assert customer_message("unknown") == (
        "I couldn't confirm the refund outcome yet. The case is being checked."
    )
    assert customer_message("failed") == "The refund could not be completed."
    assert customer_message("escalated") == "A support specialist will review your case."
    assert customer_message("rejected") == "A reviewer declined this refund request."
    assert customer_message("ineligible", policy_explanation="Returns must be within 30 days.") == (
        "Returns must be within 30 days."
    )
    assert format_inr(249900) == "₹2,499"


def test_verified_message_follows_the_refund_row():
    from app.services.messages import verified_customer_message

    assert verified_customer_message("open", None, None) is None
    assert verified_customer_message("resolved", "reserved", 249900) is None
    assert verified_customer_message("open", "succeeded", 249900) == (
        "Your refund of ₹2,499 has been confirmed."
    )
    assert verified_customer_message("awaiting_approval", None, None) == (
        "Your refund request is awaiting approval."
    )
    assert verified_customer_message("open", None, None, proposal_status="rejected") == (
        "A reviewer declined this refund request."
    )


def test_omitted_policy_flag_does_not_allow_a_refund():
    base = {
        "order_status": "delivered",
        "order_customer_id": CUSTOMER,
        "requesting_customer_id": CUSTOMER,
        "delivered_at": NOW - timedelta(days=10),
        "return_requested_at": NOW - timedelta(days=8),
        "return_status": "received",
        "inspection_status": "passed",
        "paid_amount_minor": 249900,
        "refund_committed_minor": 0,
        "returned_items_paid_minor": 249900,
    }
    decision = evaluate_refund_eligibility(RefundFacts(**base))
    assert decision.eligible is False
    assert decision.reason == "no_policy"


def test_refund_policy_decision_uses_metadata_not_prose():
    from app.services.eligibility import refund_policy_decision

    prose = {"content": "refunds are prohibited", "applies_to": "shipping"}
    assert refund_policy_decision([prose, {"content": "refund"}]) == (False, False)
    assert refund_policy_decision([{"applies_to": "refund", "effect": "allow"}]) == (True, False)
    assert refund_policy_decision([{"applies_to": "refund", "effect": "deny"}]) == (True, True)
    assert refund_policy_decision(
        [
            {"applies_to": "refund", "effect": "allow"},
            {"applies_to": "refund", "effect": "deny"},
        ]
    ) == (True, True)
    assert refund_policy_decision([{"applies_to": "refund", "effect": "maybe"}]) == (True, True)

