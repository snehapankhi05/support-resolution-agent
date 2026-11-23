"""Plain refund rules. These functions do not read the database or call a provider.

Money values are integer paise. Callers pass facts they have already loaded.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

RETURN_WINDOW = timedelta(days=30)
BLOCKING_REFUND_STATUSES = frozenset({"reserved", "processing", "succeeded"})


@dataclass(frozen=True)
class RefundFacts:
    order_status: str
    order_customer_id: object
    requesting_customer_id: object
    delivered_at: datetime | None
    return_requested_at: datetime | None
    return_status: str | None
    inspection_status: str | None
    paid_amount_minor: int
    refund_committed_minor: int
    returned_items_paid_minor: int
    existing_refund_status: str | None = None
    existing_refund_amount_minor: int | None = None
    policy_found: bool = False
    policy_conflict: bool = False


@dataclass(frozen=True)
class EligibilityDecision:
    eligible: bool
    amount_minor: int
    reason: str
    escalate: bool
    existing_refund_status: str | None = None


def _money(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an int number of paise")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    return value


def refundable_amount(
    paid_amount_minor: int, refund_committed_minor: int, returned_items_paid_minor: int
) -> int:
    """Return the refundable paise: returned-item total, capped by unpaid balance."""
    paid = _money(paid_amount_minor, "paid_amount_minor")
    committed = _money(refund_committed_minor, "refund_committed_minor")
    returned = _money(returned_items_paid_minor, "returned_items_paid_minor")
    remaining = paid - committed
    if remaining <= 0:
        return 0
    if returned < remaining:
        return returned
    return remaining


def evaluate_refund_eligibility(facts: RefundFacts) -> EligibilityDecision:
    """Apply R1–R5, R7 and R8. Approval (R6) is checked separately before money moves."""
    paid = _money(facts.paid_amount_minor, "paid_amount_minor")
    committed = _money(facts.refund_committed_minor, "refund_committed_minor")
    returned = _money(facts.returned_items_paid_minor, "returned_items_paid_minor")

    if facts.order_customer_id != facts.requesting_customer_id:
        return _no("not_owner", escalate=False)
    if facts.order_status != "delivered":
        return _no("not_delivered", escalate=False)
    if facts.delivered_at is None:
        return _no("missing_delivery", escalate=True)
    if facts.inspection_status == "failed":
        return _no("inspection_failed", escalate=True)
    if facts.return_requested_at is None or facts.return_status is None:
        return _no("no_return", escalate=False)
    if facts.return_requested_at > facts.delivered_at + RETURN_WINDOW:
        return _no("return_window", escalate=False)
    if facts.return_status != "received":
        return _no("return_not_received", escalate=False)
    if facts.inspection_status != "passed":
        return _no("inspection_pending", escalate=False)

    amount = refundable_amount(paid, committed, returned)
    blocking = facts.existing_refund_status in BLOCKING_REFUND_STATUSES
    same_amount = (
        facts.existing_refund_amount_minor is not None
        and facts.existing_refund_amount_minor == amount
        and amount > 0
    )
    if blocking and (same_amount or amount == 0):
        return EligibilityDecision(
            False, 0, "already_refunded", False, facts.existing_refund_status
        )
    if amount <= 0:
        return _no("nothing_to_refund", escalate=False)
    if facts.policy_conflict:
        return _no("policy_conflict", escalate=True)
    if not facts.policy_found:
        return _no("no_policy", escalate=True)
    return EligibilityDecision(True, amount, "eligible", False)


def refund_policy_decision(metadata_rows: list[dict]) -> tuple[bool, bool]:
    """Return (policy_found, policy_conflict) from stored policy metadata.

    Only a row with applies_to exactly "refund" and effect exactly "allow" or
    "deny" is a decision. Chunk text is not an input. Those metadata fields are
    written by trusted policy ingestion, not by a model tool argument.
    """
    effects: set[str] = set()
    for row in metadata_rows:
        if not isinstance(row, dict) or row.get("applies_to") != "refund":
            continue
        effect = row.get("effect")
        if effect not in {"allow", "deny"}:
            return True, True
        effects.add(effect)
    if not effects:
        return False, False
    if effects == {"allow"}:
        return True, False
    return True, True


def approval_is_current(
    *,
    status: str | None,
    expires_at: datetime | None,
    reviewer_present: bool,
    decided_at: datetime | None,
    now: datetime,
) -> str | None:
    """Return None when the stored approval may be used, otherwise a reason code.

    The reviewer id is the one saved on the approval row. This function does not
    accept a reviewer id from a request.
    """
    if status != "approved" or not reviewer_present or decided_at is None:
        return "approval_required"
    if expires_at is None or expires_at <= now:
        return "approval_expired"
    return None


def _no(reason: str, *, escalate: bool) -> EligibilityDecision:
    return EligibilityDecision(False, 0, reason, escalate)
