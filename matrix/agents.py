"""Who's who. This is where you add new agents (see ROADMAP.md, Zone 1).

Purpose right now: the Matrix's only job is to build itself. The drywall/business specialists below
are placeholders for FUTURE functions (not active); they are switched on later, one at a time,
only with the owner's approval.
"""

# Tier 1 senior staff (NOT CEOs - the owner is the only CEO). SUNDAY routes to one of these.
SENIOR_STAFF = {
    "STEWARD": ["strategy", "advice", "plan", "price", "pricing", "quote", "bid", "rate", "margin",
                "blueprint", "takeoff", "drawing", "quality", "handover", "deficiency", "grow"],
    "BEZEL": ["crew", "tailgate", "briefing", "punch", "labour", "labor", "truck", "silverado", "obd",
              "oil", "invoice", "paid", "payment", "owe", "receivable", "cash", "expense", "receipt",
              "cca", "tax", "quickbooks", "qbo", "bookkeeping", "ledger", "schedule", "admin"],
}

# Which specialists each senior staff member can delegate to.
DELEGATES = {
    "STEWARD": ["MARGIN", "VECTOR", "FINISH"],
    "BEZEL": ["TAPER", "ARMOR", "DEDUCT", "AUDIT", "LEDGER"],
}

# Specialist stubs: job description + mock-mode keywords.
SPECIALISTS = {
    "TAPER":  ("field ops: tailgate briefings, punch lists, labour",
               ["crew", "tailgate", "briefing", "punch", "labour", "labor", "site", "schedule", "tomorrow"]),
    "ARMOR":  ("truck: 2012 Silverado 1500 LTZ OBD2 maintenance and EDC readiness",
               ["truck", "silverado", "obd", "oil", "tire", "engine", "edc", "check engine"]),
    "DEDUCT": ("CCA and expense tracking (Canada)",
               ["expense", "receipt", "cca", "tax", "deduct", "write off", "fuel"]),
    "AUDIT":  ("accounts receivable and cash flow",
               ["invoice", "paid", "owe", "overdue", "receivable", "cash", "reminder", "collect"]),
    "MARGIN": ("pricing, sq-ft rates, labour-to-material ratios",
               ["price", "pricing", "quote", "bid", "rate", "sq ft", "sqft", "margin", "material"]),
    "LEDGER": ("bookkeeping and QuickBooks Online sync",
               ["quickbooks", "qbo", "bookkeeping", "ledger", "reconcile", "entry"]),
    "FINISH": ("quality checks and handover PDFs",
               ["quality", "handover", "deficiency", "walkthrough", "finish", "pdf"]),
    "VECTOR": ("blueprint takeoffs",
               ["blueprint", "takeoff", "drawing", "plans", "board count", "sheets"]),
}

# Words that mean "this would touch the outside world" -> approval gate.
OUTWARD_WORDS = {
    "send_email": ["email", "e-mail"],
    "send_message": ["send", "text ", "message", "remind", "reminder", "tell ", "post"],
    "write_quickbooks": ["quickbooks", "qbo", "record in", "enter into"],
    "payment": ["pay ", "payment to", "e-transfer", "etransfer", "purchase", "order "],
    "delete": ["delete", "remove", "erase"],
}
# These specialists always write outward when they act (LEDGER writes to QuickBooks).
ALWAYS_OUTWARD = {"LEDGER": "write_quickbooks"}

# Short role labels for the Keeper job envelope (each task carries agent name + role).
ROLE_LABELS = {
    "SUNDAY": "router",
    "STEWARD": "senior staff", "BEZEL": "senior staff",
    "TAPER": "field ops", "ARMOR": "truck maintenance", "DEDUCT": "expense tracker",
    "AUDIT": "receivables", "MARGIN": "pricing", "LEDGER": "bookkeeper",
    "FINISH": "quality checker", "VECTOR": "takeoff estimator",
}
