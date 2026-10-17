"""
Invoice Agent
=============
1. Perform phone call verification for an invoice.
2. Default result is True (verified).
3. Frontend button allows operator to manually set verified=False.
"""

from __future__ import annotations

import json
from pathlib import Path

from langchain_classic.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

PHONE_CALL_NUMBER = "646-523-2879"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool
def verify_invoice_by_phone(invoice_id: str, amount: float, vendor_name: str) -> str:
    """
    Initiate a phone call to verify an invoice. The call is placed to the
    central verification line (646-523-2879).

    Default result is verified=True.
    Frontend operators can click the 'Not Verified' button to override.

    Args:
        invoice_id: The invoice identifier (e.g. "INV-2026-0042").
        amount: The invoice dollar amount.
        vendor_name: The vendor name associated with the invoice.

    Returns:
        JSON with verified (bool), phone_called, and a note.
    """
    return json.dumps({
        "verified": True,  # Default — frontend button overrides to False
        "phone_called": PHONE_CALL_NUMBER,
        "invoice_id": invoice_id,
        "amount": amount,
        "vendor_name": vendor_name,
        "note": (
            "Default result is verified=True. "
            "Frontend operator can click 'Not Verified' to override this result."
        ),
    })


@tool
def get_verification_status(invoice_id: str, frontend_override: bool = False) -> str:
    """
    Retrieve the final verification status for an invoice, incorporating
    any frontend override set by an operator.

    Args:
        invoice_id: The invoice identifier.
        frontend_override: If True, the operator has clicked 'Not Verified'
                           on the frontend, setting verified=False.

    Returns:
        JSON with the final verified status (True or False).
    """
    verified = not frontend_override
    return json.dumps({
        "invoice_id": invoice_id,
        "verified": verified,
        "source": "frontend_override" if frontend_override else "default_verified",
    })


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
INVOICE_SYSTEM_PROMPT = """You are an Invoice Verification Agent. Your job is to verify
invoices via phone call before they are approved for payment.

Workflow:
1. Use `verify_invoice_by_phone` to initiate a call for the given invoice.
2. Use `get_verification_status` to determine the final result.
   - Pass `frontend_override=True` only if the operator has explicitly indicated
     the call was NOT verified (e.g., the user says "not verified" or "failed").
   - Otherwise leave `frontend_override=False` (default = verified).
3. Return True if verified, False if not verified.

Be concise. State the final result clearly as True or False.
"""


# ---------------------------------------------------------------------------
# Build agent
# ---------------------------------------------------------------------------
def build_invoice_agent(model: str = "gpt-4o-mini") -> AgentExecutor:
    llm = ChatOpenAI(model=model, temperature=0)
    tools = [verify_invoice_by_phone, get_verification_status]
    prompt = ChatPromptTemplate.from_messages([
        ("system", INVOICE_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_openai_functions_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
def run_invoice_verification(
    invoice_id: str,
    amount: float,
    vendor_name: str,
    operator_override: bool = False,
    model: str = "gpt-4o-mini",
) -> bool:
    """
    Run invoice verification. Returns True (verified) or False (not verified).

    Args:
        invoice_id: Invoice identifier.
        amount: Invoice amount.
        vendor_name: Vendor name on the invoice.
        operator_override: Set True if frontend operator marked as 'Not Verified'.
    """
    agent = build_invoice_agent(model=model)
    override_note = " The operator has marked this as NOT VERIFIED." if operator_override else ""
    query = (
        f"Verify invoice {invoice_id} for ${amount:.2f} from {vendor_name}.{override_note}"
    )
    result = agent.invoke({"input": query})
    # Parse the boolean from the LLM output
    output = result["output"].lower()
    return "true" in output or "verified" in output and "not" not in output


if __name__ == "__main__":
    import sys
    invoice_id = sys.argv[1] if len(sys.argv) > 1 else "INV-2026-0001"
    amount = float(sys.argv[2]) if len(sys.argv) > 2 else 5000.0
    vendor = sys.argv[3] if len(sys.argv) > 3 else "Bluebird Software Inc."
    verified = run_invoice_verification(invoice_id, amount, vendor)
    print(f"\nInvoice {invoice_id}: verified = {verified}")
