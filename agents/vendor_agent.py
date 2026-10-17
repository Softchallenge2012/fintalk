"""
Vendor Agent
============
1. Search vendor name → find account number, business address, phone from vendor.json
2. Verify input account number, business address, phone against search results
3. If match → check CFPB reputation → if positive return "match", else "not match"
   If no match → return "not match" + trigger phone call verification
   If not found → copy entry to vendor.json, return "added new entry" + trigger phone call
4. Phone call verification is a frontend-controlled step:
   - Default result is "match" (verified)
   - Frontend has a button to manually override to "not verified"
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from langchain_classic.agents import AgentExecutor, create_openai_functions_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent.parent
DIRECTORY_DB = BASE_DIR / "directorydb.json"
VENDOR_DB = Path(__file__).parent / "vendor.json"

PHONE_CALL_NUMBER = "646-523-2879"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _load_directory() -> list[dict]:
    with open(DIRECTORY_DB) as f:
        return json.load(f)["companies"]


def _load_vendors() -> list[dict]:
    if not VENDOR_DB.exists():
        return []
    with open(VENDOR_DB) as f:
        data = json.load(f)
        return data.get("vendors", [])


def _save_vendors(vendors: list[dict]) -> None:
    with open(VENDOR_DB, "w") as f:
        json.dump({"vendors": vendors}, f, indent=2)


def _normalize(s: str) -> str:
    """Lower-case and strip whitespace/punctuation for fuzzy comparison."""
    return re.sub(r"[\s\-\+\(\)]+", "", s).lower()


def _find_company_in_vendors(name: str) -> Optional[dict]:
    name_norm = name.strip().lower()
    for company in _load_vendors():
        if company["name"].strip().lower() == name_norm:
            return company
    # partial match fallback
    for company in _load_vendors():
        if name_norm in company["name"].strip().lower():
            return company
    return None


def _find_company_in_directory(name: str) -> Optional[dict]:
    name_norm = name.strip().lower()
    for company in _load_directory():
        if company["name"].strip().lower() == name_norm:
            return company
    # partial match fallback
    for company in _load_directory():
        if name_norm in company["name"].strip().lower():
            return company
    return None


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@tool
def lookup_vendor(vendor_name: str) -> str:
    """
    Look up a vendor by name in vendor.json.
    Returns the vendor's account number, business address, and phone number
    if found, otherwise reports not found.

    Args:
        vendor_name: The business name of the vendor to search.
    """
    company = _find_company_in_vendors(vendor_name)
    if not company:
        return json.dumps({"found": False, "vendor_name": vendor_name})
    return json.dumps({
        "found": True,
        "name": company["name"],
        "accountNumber": company.get("accountNumber", ""),
        "businessAddress": company.get("businessAddress", ""),
        "phone": company["contact"].get("phone", ""),
    })


@tool
def verify_vendor_details(
    vendor_name: str,
    input_account_number: str,
    input_business_address: str,
    input_phone: str,
) -> str:
    """
    Verify whether the provided account number, business address, and phone
    match what is stored for the vendor. Returns:
    - "match"          — all fields match
    - "not match"      — vendor exists but one or more fields differ
    - "not found"      — vendor does not exist in the directory

    Args:
        vendor_name: Business name of the vendor.
        input_account_number: Account number provided for verification.
        input_business_address: Business address provided for verification.
        input_phone: Phone number provided for verification.
    """
    company = _find_company_in_vendors(vendor_name)
    if not company:
        return json.dumps({"status": "not found", "vendor_name": vendor_name})

    acct_match = _normalize(company.get("accountNumber", "")) == _normalize(input_account_number)
    addr_match = _normalize(company.get("businessAddress", "")) == _normalize(input_business_address)
    phone_match = _normalize(company["contact"].get("phone", "")) == _normalize(input_phone)

    all_match = acct_match and addr_match and phone_match
    return json.dumps({
        "status": "match" if all_match else "not match",
        "details": {
            "accountNumber": {"expected": company.get("accountNumber"), "input": input_account_number, "match": acct_match},
            "businessAddress": {"expected": company.get("businessAddress"), "input": input_business_address, "match": addr_match},
            "phone": {"expected": company["contact"].get("phone"), "input": input_phone, "match": phone_match},
        },
    })


@tool
def add_new_vendor_entry(vendor_name: str) -> str:
    """
    Copy the vendor's entry from directorydb.json to vendor.json.
    Use this when the vendor is not yet in vendor.json (not found in prior verification).
    Returns "added new entry" if successful.

    Args:
        vendor_name: Business name of the vendor to copy.
    """
    company = _find_company_in_directory(vendor_name)
    if not company:
        return json.dumps({"status": "error", "message": f"Vendor '{vendor_name}' not found in directory."})

    vendors = _load_vendors()
    # Avoid duplicates
    existing_names = [_normalize(v["name"]) for v in vendors]
    if _normalize(company["name"]) in existing_names:
        return json.dumps({"status": "already exists", "name": company["name"]})

    vendors.append(company)
    _save_vendors(vendors)
    return json.dumps({"status": "added new entry", "name": company["name"]})


@tool
def search_cfpb_reputation(business_name: str) -> str:
    """
    Search the Consumer Financial Protection Bureau (CFPB) complaint database
    for complaints against the given business. Summarizes whether the reputation
    is positive or negative.

    Args:
        business_name: The name of the business to look up.
    """
    # CFPB public API: https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/
    encoded_name = urllib.parse.quote(business_name)
    url = (
        f"https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"
        f"?company_public_response=&company={encoded_name}&format=json&no_aggs=false"
        f"&no_highlight=false&size=5&sort=created_date_desc"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "VendorAgent/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return json.dumps({"reputation": "unknown", "error": str(e), "business_name": business_name})

    hits = data.get("hits", {})
    total = hits.get("total", {})
    total_complaints = total.get("value", 0) if isinstance(total, dict) else int(total or 0)
    complaints = hits.get("hits", [])

    if total_complaints == 0:
        return json.dumps({
            "reputation": "positive",
            "summary": f"No CFPB complaints found for '{business_name}'.",
            "total_complaints": 0,
        })

    # Sample the first few for context
    samples = []
    for c in complaints[:5]:
        src = c.get("_source", {})
        samples.append({
            "issue": src.get("issue", ""),
            "product": src.get("product", ""),
            "company_response": src.get("company_public_response", ""),
            "date": src.get("date_received", ""),
        })

    reputation = "negative" if total_complaints > 5 else "mixed"
    return json.dumps({
        "reputation": reputation,
        "total_complaints": total_complaints,
        "summary": (
            f"Found {total_complaints} CFPB complaint(s) for '{business_name}'. "
            f"Reputation classified as '{reputation}'."
        ),
        "sample_complaints": samples,
    })


@tool
def initiate_phone_verification(reason: str) -> str:
    """
    Initiate a phone call verification to 646-523-2879.
    The verification result defaults to 'verified' (match = True).
    At the frontend, a button allows the operator to manually override this to 'not verified'.

    Args:
        reason: Brief explanation of why verification is needed (e.g., "address mismatch", "new vendor").

    Returns:
        JSON with verified=True (default) and the phone number called.
    """
    return json.dumps({
        "verified": True,  # default; frontend button can override to False
        "phone_called": PHONE_CALL_NUMBER,
        "reason": reason,
        "note": "Default result is verified=True. Frontend operator can click 'Not Verified' to override.",
    })


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
VENDOR_SYSTEM_PROMPT = """You are a Vendor Verification Agent. Your job is to verify vendor details
and assess their trustworthiness before authorizing payments.

Follow this exact workflow:

**Step 1:** Use `lookup_vendor` to search vendor.json by vendor name.

**Step 2:** Use `verify_vendor_details` to compare the input account number,
    business address, and phone against the vendor.json record.

**Step 3a — If status is "not found":**
  - Use `add_new_vendor_entry` to copy the vendor from directorydb.json to vendor.json.
  - Use `initiate_phone_verification` with reason="new vendor entry".
  - Return "added new entry".

**Step 3b — If status is "not match":**
  - Use `initiate_phone_verification` with reason="details mismatch".
  - Return "not match".

**Step 3c — If status is "match":**
  - Use `search_cfpb_reputation` to check the CFPB complaint database.
  - If reputation is "positive" → return "match".
  - If reputation is "negative" or "mixed" → use `initiate_phone_verification`
    with reason="poor CFPB reputation", then return "not match".

Always present results clearly with the final verdict: "match", "not match", or "added new entry".
"""


# ---------------------------------------------------------------------------
# Build agent
# ---------------------------------------------------------------------------
def build_vendor_agent(model: str = "gpt-4o-mini") -> AgentExecutor:
    llm = ChatOpenAI(model=model, temperature=0)
    tools = [
        lookup_vendor,
        verify_vendor_details,
        add_new_vendor_entry,
        search_cfpb_reputation,
        initiate_phone_verification,
    ]
    prompt = ChatPromptTemplate.from_messages([
        ("system", VENDOR_SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_openai_functions_agent(llm=llm, tools=tools, prompt=prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True)


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------
def run_vendor_verification(
    vendor_name: str,
    account_number: str,
    business_address: str,
    phone: str,
    model: str = "gpt-4o-mini",
) -> dict:
    """
    Convenience function to run vendor verification programmatically.
    Returns a dict with 'verdict' and 'details'.
    """
    agent = build_vendor_agent(model=model)
    query = (
        f"Please verify vendor '{vendor_name}' with:\n"
        f"  Account Number: {account_number}\n"
        f"  Business Address: {business_address}\n"
        f"  Phone: {phone}"
    )
    result = agent.invoke({"input": query})
    return {"verdict": result["output"], "query": query}


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 5:
        _, name, acct, addr, phone = sys.argv
        res = run_vendor_verification(name, acct, addr, phone)
        print("\nVerdict:", res["verdict"])
    else:
        print("Usage: python vendor_agent.py <name> <account_number> <address> <phone>")
        print("Example: python vendor_agent.py 'Bluebird Software Inc.' 100200300402 '400 Technology Park, Burlington, MA 01803' '+1-555-010-3311'")
