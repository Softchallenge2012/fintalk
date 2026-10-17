"""
Verification Agent
==================
Orchestrates vendor verification, vendor phone call verification, and invoice
phone call verification to produce a final decision:

- If (vendor verification is True OR vendor phone verification is True)
    AND invoice verification is True → "normal"
- Otherwise → "abnormal"
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Add agents dir to path so we can import sibling agents
sys.path.insert(0, str(Path(__file__).parent))

from langchain_classic.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from vendor_agent import initiate_phone_verification
from vendor_agent import run_vendor_verification
from invoice_agent import run_invoice_verification


# ---------------------------------------------------------------------------
# Tools — these wrap the sub-agents
# ---------------------------------------------------------------------------
@tool
def run_vendor_verification_check(
    vendor_name: str,
    account_number: str,
    business_address: str,
    phone: str,
) -> str:
    """
    Run vendor verification (details + reputation workflow) and return whether
    it is verified as a boolean.

    Args:
        vendor_name: Business name of the vendor.
        account_number: Account number to verify.
        business_address: Business address to verify.
        phone: Phone number to verify.
    """
    result = run_vendor_verification(
        vendor_name=vendor_name,
        account_number=account_number,
        business_address=business_address,
        phone=phone,
    )
    verdict_text = str(result.get("verdict", "")).lower()
    verified = "match" in verdict_text and "not" not in verdict_text
    return json.dumps({"verified": verified, "raw_result": result.get("verdict", "")})


@tool
def run_vendor_phone_check(operator_override: bool = False) -> str:
    """
    Run vendor phone call verification only.
    Default phone verification result is True.
    Frontend/operator can override to False.

    Args:
        operator_override: Set True if frontend operator marked vendor phone call as not verified.
    """
    call_result_raw = initiate_phone_verification.invoke({"reason": "vendor phone call verification"})
    call_result = json.loads(call_result_raw)
    verified = bool(call_result.get("verified", True)) and not operator_override
    return json.dumps({
        "verified": verified,
        "source": "frontend_override" if operator_override else "default_phone_verified",
        "phone_called": call_result.get("phone_called", ""),
    })


@tool
def run_invoice_check(
    invoice_id: str,
    amount: float,
    vendor_name: str,
    operator_override: bool = False,
) -> str:
    """
    Run the invoice phone call verification.
    Returns True (verified) or False (not verified).

    Args:
        invoice_id: Invoice identifier (e.g. "INV-2026-0042").
        amount: Invoice dollar amount.
        vendor_name: Vendor name on the invoice.
        operator_override: Set True if frontend operator marked 'Not Verified'.
    """
    verified = run_invoice_verification(
        invoice_id=invoice_id,
        amount=amount,
        vendor_name=vendor_name,
        operator_override=operator_override,
    )
    return json.dumps({"verified": verified, "invoice_id": invoice_id})


@tool
def make_final_decision(vendor_verified: bool, vendor_phone_verified: bool, invoice_verified: bool) -> str:
    """
    Produce the final decision based on vendor verification, vendor phone call
    verification, and invoice verification.

    Rules:
    - (vendor_verified == True OR vendor_phone_verified == True)
      AND invoice_verified == True  → "normal"
    - Otherwise → "abnormal"

    Args:
        vendor_verified: Vendor verification result.
        vendor_phone_verified: Vendor phone call verification result.
        invoice_verified: Invoice verification result.

    Returns:
        JSON with decision: "normal" or "abnormal", plus a reason.
    """
    if (vendor_verified or vendor_phone_verified) and invoice_verified:
        decision = "normal"
        reason = "Invoice is verified and vendor is verified by details/reputation or phone call."
    else:
        decision = "abnormal"
        reason = "Invoice is not verified or vendor is not verified by both methods."

    return json.dumps({
        "decision": decision,
        "reason": reason,
        "vendor_verified": vendor_verified,
        "vendor_phone_verified": vendor_phone_verified,
        "invoice_verified": invoice_verified,
    })


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
VERIFICATION_SYSTEM_PROMPT = """You are the Master Verification Agent. You coordinate
vendor verification, vendor phone verification, and invoice verification to
produce a final decision.

Workflow:
1. Use `run_vendor_verification_check` to get vendor verification result.
2. Use `run_vendor_phone_check` to get vendor phone call verification result.
3. Use `run_invoice_check` with the invoice details.
4. Use `make_final_decision` with all three results to produce "normal" or "abnormal".

Decision rules:
- "normal"   → (vendor verification is True OR vendor phone verification is True)
               AND invoice verification is True
- "abnormal" → otherwise

Always state the final decision clearly: "normal" or "abnormal", along with a brief reason.
"""


# ---------------------------------------------------------------------------
# Build agent
# ---------------------------------------------------------------------------
def build_verification_agent(model: str = "gpt-4o-mini") -> AgentExecutor:
    llm = ChatOpenAI(model=model, temperature=0)
    tools = [
        run_vendor_verification_check,
        run_vendor_phone_check,
        run_invoice_check,
        make_final_decision,
    ]
    prompt = ChatPromptTemplate.from_messages([
        ("system", VERIFICATION_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_openai_functions_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


# ---------------------------------------------------------------------------
# Convenience runner
# ---------------------------------------------------------------------------
def run_full_verification(
    vendor_name: str,
    account_number: str,
    business_address: str,
    phone: str,
    invoice_id: str,
    invoice_amount: float,
    vendor_operator_override: bool = False,
    invoice_operator_override: bool = False,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Run the complete end-to-end verification pipeline.

    Returns:
        dict with 'decision' ("normal" or "abnormal") and 'details'.
    """
    agent = build_verification_agent(model=model)
    query = (
        f"Please run a full verification for:\n"
        f"  Vendor Name: {vendor_name}\n"
        f"  Account Number: {account_number}\n"
        f"  Business Address: {business_address}\n"
        f"  Phone: {phone}\n"
        f"  Invoice ID: {invoice_id}\n"
        f"  Invoice Amount: ${invoice_amount:.2f}\n"
        + (f"  Note: The operator has marked the vendor call as NOT VERIFIED.\n"
           if vendor_operator_override else "")
        + (f"  Note: The operator has marked the invoice call as NOT VERIFIED.\n"
           if invoice_operator_override else "")
        + (
            "  Please use run_vendor_phone_check with operator_override=True.\n"
            if vendor_operator_override else
            "  Please use run_vendor_phone_check with operator_override=False.\n"
        )
    )
    result = agent.invoke({"input": query})
    output = result["output"]
    decision = "normal" if "normal" in output.lower() else "abnormal"
    return {"decision": decision, "details": output}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Quick demo run
    result = run_full_verification(
        vendor_name="Bluebird Software Inc.",
        account_number="100200300402",
        business_address="400 Technology Park, Burlington, MA 01803",
        phone="+1-555-010-3311",
        invoice_id="INV-2026-0042",
        invoice_amount=48000.00,
    )
    print("\n" + "="*50)
    print(f"FINAL DECISION: {result['decision'].upper()}")
    print("="*50)
    print(result["details"])
