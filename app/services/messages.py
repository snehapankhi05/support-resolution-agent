"""Customer-facing sentences tied to a verified state."""


def format_inr(amount_minor: int) -> str:
    """Format integer paise as rupees, for example 249900 -> ₹2,499."""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise TypeError("amount_minor must be an int number of paise")
    if amount_minor < 0:
        raise ValueError("amount_minor cannot be negative")
    rupees, paise = divmod(amount_minor, 100)
    whole = f"{rupees:,}"
    if paise:
        return f"₹{whole}.{paise:02d}"
    return f"₹{whole}"


def customer_message(
    state: str, *, amount_minor: int | None = None, policy_explanation: str | None = None
) -> str:
    """Return the sentence for a verified state. Status strings stay distinct."""
    if state == "awaiting_approval":
        return "Your refund request is awaiting approval."
    if state in {"submitted", "processing"}:
        return "The payment service accepted your refund request. Processing is still pending."
    if state == "succeeded":
        if amount_minor is None:
            raise ValueError("succeeded message requires amount_minor")
        return f"Your refund of {format_inr(amount_minor)} has been confirmed."
    if state == "unknown":
        return "I couldn't confirm the refund outcome yet. The case is being checked."
    if state == "failed":
        return "The refund could not be completed."
    if state == "escalated":
        return "A support specialist will review your case."
    if state == "rejected":
        return "A reviewer declined this refund request."
    if state == "ineligible":
        if not policy_explanation:
            raise ValueError("ineligible message requires the policy explanation")
        return policy_explanation
    raise ValueError(f"unknown customer state: {state}")


_INELIGIBLE = {
    "not_owner": "This order cannot be refunded for this account.",
    "not_delivered": "This order has not been delivered, so a refund cannot be requested yet.",
    "no_return": "A refund needs a recorded return. This order has no return.",
    "return_window": (
        "The return window is 30 days from delivery. This return was requested after that "
        "window. Policy section: return-window."
    ),
    "return_not_received": "The return has not been received yet, so a refund cannot be requested.",
    "inspection_pending": "Inspection has not passed yet, so a refund cannot be requested.",
    "already_refunded": "A refund for this amount is already reserved, processing, or succeeded.",
    "nothing_to_refund": "There is no refundable balance on this order.",
}


def ineligible_message(reason: str) -> str:
    """Fixed sentence for an eligibility reason. The reason code comes from Python."""
    text = _INELIGIBLE.get(reason)
    if text is None:
        raise ValueError(f"unknown ineligible reason: {reason}")
    return customer_message("ineligible", policy_explanation=text)


def order_status_message(order_status: str) -> str:
    """Report a stored order status. An unknown status is not turned into a guess."""
    labels = {
        "placed": "The latest order status is placed.",
        "shipped": "The latest order status is shipped.",
        "delivered": "The latest order status is delivered.",
        "cancelled": "The latest order status is cancelled.",
    }
    text = labels.get(order_status)
    if text is None:
        raise ValueError(f"unknown order status: {order_status}")
    return text


def verified_customer_message(
    ticket_status: str,
    refund_status: str | None,
    amount_minor: int | None,
    *,
    proposal_status: str | None = None,
) -> str | None:
    """Sentence for a stored ticket, proposal, or refund. A reserved refund is not called a payment."""
    if refund_status in {"submitted", "processing", "failed", "unknown"}:
        return customer_message(refund_status)
    if refund_status == "succeeded" and amount_minor is not None:
        return customer_message("succeeded", amount_minor=amount_minor)
    if refund_status == "reserved":
        return None
    if proposal_status == "rejected":
        return customer_message("rejected")
    if ticket_status == "awaiting_approval":
        return customer_message("awaiting_approval")
    if ticket_status == "escalated":
        return customer_message("escalated")
    return None
