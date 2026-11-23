"""One deterministic workflow per ticket.

Python loads the ticket, reads the order, and writes any proposal. The resume
value is ignored. Payment goes through execute_approved_refund.
"""

import uuid
from datetime import UTC, datetime
from typing import TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from app.db.models import (
    ActionProposal,
    Approval,
    RefundOperation,
    Ticket,
    WorkflowRun,
)
from app.db.session import SessionLocal
from app.mcp_servers.queries import (
    create_refund_proposal,
    read_order,
    read_refund_status,
    read_return,
)
from app.services.messages import (
    customer_message,
    ineligible_message,
    order_status_message,
    verified_customer_message,
)
from app.services.payments import PaymentProvider
from app.services.refunds import REFUND_ACTION, execute_approved_refund

ACTIVE_RUN = frozenset({"running", "awaiting_approval"})
FINISHED_RUN = frozenset({"completed", "escalated", "failed"})
KNOWN_CATEGORIES = frozenset({"order_status", "refund_request", "damaged_item"})


class WorkflowState(TypedDict, total=False):
    ticket_id: str
    run_id: str
    customer_id: str
    order_id: str
    category: str
    order_status: str
    proposal_id: str
    proposal_status: str
    reason: str
    escalate: bool
    decision: str
    message: str
    terminal: str


def build_support_graph(
    provider: PaymentProvider, checkpointer: BaseCheckpointSaver | None = None
):
    """Compile the ticket workflow. Tests may pass an in-memory checkpointer."""
    saver = checkpointer if checkpointer is not None else InMemorySaver()

    def load(state: WorkflowState) -> WorkflowState:
        ticket_id = uuid.UUID(state["ticket_id"])
        thread_id = str(get_config()["configurable"]["thread_id"])
        with SessionLocal() as session:
            ticket = session.get(Ticket, ticket_id)
            run = session.scalar(select(WorkflowRun).where(WorkflowRun.thread_id == thread_id))
            if ticket is None or ticket.order_id is None or run is None or run.ticket_id != ticket.id:
                return {"terminal": "failed", "message": "This request could not be completed."}
            if run.status in FINISHED_RUN:
                return {
                    "terminal": run.status,
                    "category": ticket.category,
                    "customer_id": str(ticket.customer_id),
                    "order_id": str(ticket.order_id),
                    "message": _stored_message(session, ticket),
                }
            if run.status not in ACTIVE_RUN or ticket.category not in KNOWN_CATEGORIES:
                return {"terminal": "failed", "message": "This request could not be completed."}
            return {
                "run_id": str(run.id),
                "customer_id": str(ticket.customer_id),
                "order_id": str(ticket.order_id),
                "category": ticket.category,
                "terminal": "",
                "proposal_id": "",
                "proposal_status": "",
                "reason": "",
                "escalate": False,
                "decision": "",
                "message": "",
            }

    def investigate(state: WorkflowState) -> WorkflowState:
        customer_id = uuid.UUID(state["customer_id"])
        order_id = uuid.UUID(state["order_id"])
        order = read_order(customer_id, order_id)
        read_return(customer_id, order_id)
        read_refund_status(customer_id, order_id)
        if not order.success or order.data is None:
            return {"terminal": "failed", "message": "This request could not be completed."}
        status = order.data.get("status")
        if not isinstance(status, str):
            return {"terminal": "failed", "message": "This request could not be completed."}
        return {"order_status": status}

    def propose(state: WorkflowState) -> WorkflowState:
        existing = _latest_proposal(state["run_id"])
        if existing is not None:
            proposal_id, proposal_status = existing
            if proposal_status == "pending_approval":
                _mark_awaiting(state["run_id"], state["ticket_id"])
            return {"proposal_id": proposal_id, "proposal_status": proposal_status}
        result = create_refund_proposal(
            uuid.UUID(state["customer_id"]), uuid.UUID(state["ticket_id"])
        )
        if not result.success or result.data is None:
            return {"terminal": "failed", "message": "This request could not be completed."}
        if result.data.get("proposed") is True:
            _mark_awaiting(state["run_id"], state["ticket_id"])
            return {
                "proposal_id": str(result.data["proposal_id"]),
                "proposal_status": "pending_approval",
            }
        reason = str(result.data.get("reason") or "")
        return {"reason": reason, "escalate": bool(result.data.get("escalate"))}

    def pause(state: WorkflowState) -> WorkflowState:
        interrupt({"proposal_id": state.get("proposal_id", "")})
        return {}

    def read_approval(state: WorkflowState) -> WorkflowState:
        proposal_id = uuid.UUID(state["proposal_id"])
        with SessionLocal() as session:
            proposal = session.get(ActionProposal, proposal_id)
            approval = session.scalar(select(Approval).where(Approval.proposal_id == proposal_id))
        if (
            proposal is None
            or approval is None
            or proposal.status != approval.status
            or approval.status not in {"approved", "rejected"}
        ):
            return {"decision": "waiting", "proposal_status": "pending_approval"}
        return {"decision": approval.status, "proposal_status": proposal.status}

    def execute(state: WorkflowState) -> WorkflowState:
        """Pay one stored proposal. The resume payload is not an approval.

        execute_approved_refund reads the approval row, reserves paise, and posts
        only when that approval still matches. A second call reuses the same key.
        """
        execute_approved_refund(SessionLocal, uuid.UUID(state["proposal_id"]), provider)
        return {}

    def explain(state: WorkflowState) -> WorkflowState:
        if state.get("run_id") and state.get("terminal") not in FINISHED_RUN:
            if state.get("decision") == "rejected" or state.get("proposal_status") == "rejected":
                _reopen_ticket(state["ticket_id"])
            _finish_run(state["run_id"], "completed")
        elif state.get("run_id") and state.get("terminal") == "failed":
            _finish_run(state["run_id"], "failed")
        sentence = _resolution_sentence(state)
        if sentence is not None and state.get("ticket_id"):
            _save_resolution_message(state["ticket_id"], sentence)
        return {"message": _message(state), "terminal": _terminal_after_explain(state)}

    def escalate(state: WorkflowState) -> WorkflowState:
        sentence = customer_message("escalated")
        _set_ticket_status(state["ticket_id"], "escalated")
        if state.get("run_id"):
            _finish_run(state["run_id"], "escalated")
        if state.get("ticket_id"):
            _save_resolution_message(state["ticket_id"], sentence)
        return {"message": sentence, "terminal": "escalated"}

    graph = StateGraph(WorkflowState)
    graph.add_node("load", load)
    graph.add_node("investigate", investigate)
    graph.add_node("propose", propose)
    graph.add_node("pause", pause)
    graph.add_node("read_approval", read_approval)
    graph.add_node("execute", execute)
    graph.add_node("explain", explain)
    graph.add_node("escalate", escalate)
    graph.add_edge(START, "load")
    graph.add_conditional_edges("load", _after_load, ["investigate", "escalate", "explain"])
    graph.add_conditional_edges("investigate", _after_investigate, ["propose", "explain"])
    graph.add_conditional_edges(
        "propose", _after_propose, ["pause", "read_approval", "escalate", "explain"]
    )
    graph.add_edge("pause", "read_approval")
    graph.add_conditional_edges("read_approval", _after_read, ["execute", "explain", "pause"])
    graph.add_edge("execute", "explain")
    graph.add_edge("explain", END)
    graph.add_edge("escalate", END)
    return graph.compile(checkpointer=saver)


def _after_load(state: WorkflowState) -> str:
    if state.get("terminal") in FINISHED_RUN or state.get("terminal") == "failed":
        return "explain"
    if state.get("category") == "damaged_item":
        return "escalate"
    return "investigate"


def _after_investigate(state: WorkflowState) -> str:
    if state.get("terminal") == "failed":
        return "explain"
    if state.get("category") == "order_status":
        return "explain"
    return "propose"


def _after_propose(state: WorkflowState) -> str:
    if state.get("terminal") == "failed":
        return "explain"
    status = state.get("proposal_status") or ""
    if status == "pending_approval":
        return "pause"
    if status == "approved":
        return "read_approval"
    if state.get("escalate"):
        return "escalate"
    return "explain"


def _after_read(state: WorkflowState) -> str:
    if state.get("decision") == "approved":
        return "execute"
    if state.get("decision") == "rejected":
        return "explain"
    return "pause"


def _terminal_after_explain(state: WorkflowState) -> str:
    if state.get("terminal") in FINISHED_RUN:
        return str(state["terminal"])
    if state.get("terminal") == "failed":
        return "failed"
    return "completed"


def _resolution_sentence(state: WorkflowState) -> str | None:
    """Code sentence for a finished non-payment outcome. The ticket summary is not an input."""
    if state.get("terminal") == "failed":
        return None
    if state.get("decision") == "rejected" or state.get("proposal_status") == "rejected":
        return customer_message("rejected")
    if _refund_sentence(state.get("ticket_id", "")) is not None:
        return None
    if state.get("category") == "order_status":
        status = state.get("order_status")
        if not isinstance(status, str):
            return None
        return order_status_message(status)
    reason = state.get("reason")
    if not reason:
        return None
    try:
        return ineligible_message(str(reason))
    except ValueError:
        return None


def _save_resolution_message(ticket_id: str, sentence: str) -> None:
    """Store one code sentence. This does not read the customer summary or move money."""
    with SessionLocal.begin() as session:
        ticket = session.get(Ticket, uuid.UUID(ticket_id))
        if ticket is None:
            return
        ticket.resolution_message = sentence
        ticket.updated_at = datetime.now(UTC)


def _message(state: WorkflowState) -> str:
    if state.get("message") and state.get("terminal") in FINISHED_RUN | {"failed"}:
        return str(state["message"])
    if state.get("decision") == "rejected" or state.get("proposal_status") == "rejected":
        return customer_message("rejected")
    stored = _refund_sentence(state.get("ticket_id", ""))
    if stored is not None:
        return stored
    if state.get("category") == "order_status":
        return order_status_message(str(state.get("order_status") or ""))
    if state.get("reason"):
        try:
            return ineligible_message(str(state["reason"]))
        except ValueError:
            return "This request could not be completed."
    if state.get("message"):
        return str(state["message"])
    return "This request could not be completed."


def _stored_message(session, ticket: Ticket) -> str:
    sentence = _refund_sentence(str(ticket.id))
    if sentence is not None:
        return sentence
    if ticket.status == "escalated":
        return customer_message("escalated")
    verified = verified_customer_message(ticket.status, None, None)
    if verified is not None:
        return verified
    return "This request could not be completed."


def _refund_sentence(ticket_id: str) -> str | None:
    if not ticket_id:
        return None
    with SessionLocal() as session:
        ticket = session.get(Ticket, uuid.UUID(ticket_id))
        if ticket is None:
            return None
        refund = session.scalar(
            select(RefundOperation)
            .join(ActionProposal, ActionProposal.id == RefundOperation.proposal_id)
            .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
            .where(WorkflowRun.ticket_id == ticket.id)
            .order_by(RefundOperation.created_at.desc())
        )
        proposal_status = session.scalar(
            select(ActionProposal.status)
            .join(WorkflowRun, WorkflowRun.id == ActionProposal.run_id)
            .where(WorkflowRun.ticket_id == ticket.id)
            .order_by(ActionProposal.created_at.desc())
        )
        if refund is None and proposal_status != "rejected":
            return None
        return verified_customer_message(
            ticket.status,
            None if refund is None else refund.status,
            None if refund is None else refund.amount_minor,
            proposal_status=proposal_status,
        )


def _latest_proposal(run_id: str) -> tuple[str, str] | None:
    with SessionLocal() as session:
        row = session.scalar(
            select(ActionProposal)
            .where(
                ActionProposal.run_id == uuid.UUID(run_id),
                ActionProposal.action_type == REFUND_ACTION,
            )
            .order_by(ActionProposal.created_at.desc())
        )
        if row is None:
            return None
        return str(row.id), row.status


def _mark_awaiting(run_id: str, ticket_id: str) -> None:
    with SessionLocal.begin() as session:
        run = session.get(WorkflowRun, uuid.UUID(run_id))
        ticket = session.get(Ticket, uuid.UUID(ticket_id))
        if run is not None and run.status == "running":
            run.status = "awaiting_approval"
        if ticket is not None and ticket.status == "open":
            ticket.status = "awaiting_approval"
            ticket.updated_at = datetime.now(UTC)


def _reopen_ticket(ticket_id: str) -> None:
    _set_ticket_status(ticket_id, "open", only_from="awaiting_approval")


def _set_ticket_status(ticket_id: str, status: str, *, only_from: str | None = None) -> None:
    with SessionLocal.begin() as session:
        ticket = session.get(Ticket, uuid.UUID(ticket_id))
        if ticket is None:
            return
        if only_from is not None and ticket.status != only_from:
            return
        ticket.status = status
        ticket.updated_at = datetime.now(UTC)


def _finish_run(run_id: str, status: str) -> None:
    with SessionLocal.begin() as session:
        run = session.get(WorkflowRun, uuid.UUID(run_id))
        if run is None or run.status in FINISHED_RUN:
            return
        run.status = status
        run.completed_at = datetime.now(UTC)


def workflow_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 25}
