"""The shared 'clipboard' every agent reads and writes while handling one message.

It follows the Keeper job envelope (docs/KEEPER_SPEC.md, matrix/envelope.py): message, tasks,
agents, drafts, approvals, step_log. Every change is also written straight to the `jobs` row
(job_id) by matrix/keeper_store.py.
"""
import operator
from typing import Annotated, Literal, TypedDict


class AgentRef(TypedDict):
    name: str          # e.g. "TAPER"
    role: str          # short role label, e.g. "field ops"


class Task(TypedDict, total=False):
    num: int                   # 1, 2, 3 ...
    title: str
    depends_on: list[int]      # task numbers this one waits on
    agent: AgentRef            # who is working on it


class Draft(TypedDict, total=False):
    task: int
    agent: str
    role: str
    output: str                # exactly as the agent produced it
    at: str                    # ISO-8601 UTC


class Approval(TypedDict, total=False):
    # One exchange in the approvals thread. The job waits while any entry is "pending".
    type: Literal["yes_no", "context_request"]
    status: Literal["pending", "yes", "no", "answered"]
    text: str
    at: str
    kind: str                  # outward action kind this asks about (send_message, write_quickbooks, ...)


class ProposedAction(TypedDict, total=False):
    kind: str          # e.g. "send_message", "send_email", "write_quickbooks", "payment", "delete"
    summary: str       # plain-language description shown to the owner
    outward: bool      # True = touches the outside world -> must be approved by the owner


class MatrixState(TypedDict, total=False):
    job_id: int | None                 # row in the Keeper `jobs` table
    message: str                       # 1. the incoming text
    source: str                        # "telegram", "slack", "api", "demo", "terminal"
    chat_id: str                       # where to reply
    tasks: list[Task]                  # 2. numbered, with depends_on (max 5 per sweep)
    agents: list[AgentRef]             # 3. who is on the job (name + role)
    senior: Literal["STEWARD", "BEZEL"]
    specialist: str                    # TAPER, ARMOR, DEDUCT, AUDIT, MARGIN, LEDGER, FINISH, VECTOR
    drafts: Annotated[list[Draft], operator.add]      # 4. each agent's output, as-is
    proposed_action: ProposedAction
    approvals: list[Approval]          # 5. the dialogue thread (replaced whole when a status changes)
    step_log: Annotated[list[dict], operator.add]     # 6. {"tag", "at", ...}, starter + freeform tags
    reply: str                         # final text sent back to you
