"""
Verification Agent
==================
Orchestrates vendor and invoice agents and produces a final decision:

- If invoice agent returns True  AND vendor agent returns "match"  → "normal"
- If invoice agent returns False OR  vendor agent returns "not match" → "abnormal"

(Note: the user spec says step 2 returns "normal" for both paths,
 but the intent is clearly "normal" vs "abnormal" — this is implemented accordingly.)
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

from vendor_agent import build_vendor_agent, run_vendor_verification
from invoice_agent import build_invoice_agent, run_invoice_verification


# ---------------------------------------------------------------------------
# Tools — these wrap the sub-agents
# ---------------------------------------------------------------------------
@tool
def run_vendor_check(
    vendor_name: str,
    account_number: str,
    business_address: str,
    phone: str,
) -> str:
    """
    Run the full vendor verification workflow:
    1. Look up the vendor in the directory
    2. Verify account number, address, and phone
    3. If match, check CFPB reputation
    4. If mismatch or new vendor, initiate phone call verification

    Returns a JSON with verdict: "match", "not match", or "added new entry".

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
    verdict_text = result["verdict"].lower()
    if "match" in verdict_text and "not" not in verdict_text:
        verdict = "match"
    elif "added new entry" in verdict_text:
        verdict = "added new entry"
    else:
        verdict = "not match"
    return json.dumps({"verdict": verdict, "full_output": result["verdict"]})


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
def make_final_decision(vendor_verdict: str, invoice_verified: bool) -> str:
    """
    Produce the final verification decision based on vendor and invoice results.

    Rules:
    - vendor_verdict == "match" AND invoice_verified == True  → "normal"
    - vendor_verdict == "not match" OR invoice_verified == False → "abnormal"
    - vendor_verdict == "added new entry" → "abnormal" (new vendor, needs review)

    Args:
        vendor_verdict: Output from the vendor agent: "match", "not match", or "added new entry".
        invoice_verified: Output from the invoice agent: True or False.

    Returns:
        JSON with decision: "normal" or "abnormal", plus a reason.
    """
    vendor_match = vendor_verdict.strip().lower() == "match"
    if vendor_match and invoice_verified:
        decision = "normal"
        reason = "Vendor verified and invoice confirmed."
    elif not vendor_match and not invoice_verified:
        decision = "abnormal"
        reason = f"Vendor check: '{vendor_verdict}'. Invoice verification: failed."
    elif not vendor_match:
        decision = "abnormal"
        reason = f"Vendor check returned '{vendor_verdict}'."
    else:
        decision = "abnormal"
        reason = "Invoice verification failed (not verified by operator)."

    return json.dumps({
        "decision": decision,
        "reason": reason,
        "vendor_verdict": vendor_verdict,
        "invoice_verified": invoice_verified,
    })


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
VERIFICATION_SYSTEM_PROMPT = """You are the Master Verification Agent. You coordinate
the Vendor Agent and Invoice Agent to produce a final payment authorization decision.

Workflow:
1. Use `run_vendor_check` with the vendor's details.
2. Use `run_invoice_check` with the invoice details.
3. Use `make_final_decision` with both results to produce "normal" or "abnormal".

Decision rules:
- "normal"   → vendor returned "match" AND invoice returned True (verified)
- "abnormal" → vendor returned "not match" or "added new entry", OR invoice returned False

Always state the final decision clearly: "normal" or "abnormal", along with a brief reason.
"""


# ---------------------------------------------------------------------------
# Build agent
# ---------------------------------------------------------------------------
def build_verification_agent(model: str = "gpt-4o-mini") -> AgentExecutor:
    llm = ChatOpenAI(model=model, temperature=0)
    tools = [run_vendor_check, run_invoice_check, make_final_decision]
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
        + (f"  Note: The operator has marked the invoice call as NOT VERIFIED.\n"
           if invoice_operator_override else "")
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
