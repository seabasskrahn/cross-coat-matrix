"""The shared 'clipboard' every agent reads and writes while handling one message."""
import operator
from typing import Annotated, Literal, TypedDict


class ProposedAction(TypedDict, total=False):
    kind: str          # e.g. "send_message", "send_email", "write_quickbooks", "payment", "delete"
    summary: str       # plain-language description shown to the owner
    outward: bool      # True = touches the outside world -> must be approved by the owner


class MatrixState(TypedDict, total=False):
    message: str                       # the incoming text
    source: str                        # "telegram", "slack", "api", "demo"
    chat_id: str                       # where to reply
    tasks: list[str]                   # message split into tasks (max 5 per sweep)
    senior: Literal["STEWARD", "BEZEL"]
    specialist: str                    # TAPER, ARMOR, DEDUCT, AUDIT, MARGIN, LEDGER, FINISH, VECTOR
    draft: str                         # what the specialist came up with
    proposed_action: ProposedAction
    approval: Literal["not_needed", "approved", "rejected"]
    reply: str                         # final text sent back to you
    handoff_log: Annotated[list[str], operator.add]  # every step appends one line
