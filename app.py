from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from flask import Flask, jsonify, render_template, request

from agents.invoice_agent import run_invoice_verification
from agents.vendor_agent import (
    _find_company_in_directory,
    _find_company_in_vendors,
    _load_vendors,
    _normalize,
    _save_vendors,
    initiate_phone_verification,
    search_cfpb_reputation,
)

app = Flask(__name__)


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.post("/api/vendor-verify")
def vendor_verify() -> Any:
    payload = request.get_json(silent=True) or {}

    vendor_name = str(payload.get("vendor_name", "")).strip()
    account_number = str(payload.get("account_number", "")).strip()
    phone = str(payload.get("phone", "")).strip()
    business_address = str(payload.get("business_address", "")).strip()

    if not all([vendor_name, account_number, phone, business_address]):
        return jsonify({"error": "All vendor fields are required."}), 400

    try:
        company = _find_company_in_vendors(vendor_name)

        if not company:
            directory_company = _find_company_in_directory(vendor_name)
            if not directory_company:
                return jsonify({
                    "verdict": "not found",
                    "message": "Vendor not found in vendor.json or directorydb.json.",
                })

            vendors = _load_vendors()
            existing_names = [_normalize(v.get("name", "")) for v in vendors]
            if _normalize(directory_company.get("name", "")) not in existing_names:
                vendors.append(directory_company)
                _save_vendors(vendors)

            return jsonify({
                "verdict": "added new entry",
                "message": "Vendor was not in vendor.json and has been copied from directorydb.json.",
            })

        acct_match = _normalize(company.get("accountNumber", "")) == _normalize(account_number)
        addr_match = _normalize(company.get("businessAddress", "")) == _normalize(business_address)
        phone_match = _normalize(company.get("contact", {}).get("phone", "")) == _normalize(phone)

        if not (acct_match and addr_match and phone_match):
            return jsonify({
                "verdict": "not match",
                "message": "Input does not match vendor.json record.",
                "details": {
                    "account_match": acct_match,
                    "address_match": addr_match,
                    "phone_match": phone_match,
                },
            })

        reputation_raw = search_cfpb_reputation.invoke({"business_name": vendor_name})
        reputation_data = json.loads(reputation_raw)
        reputation = str(reputation_data.get("reputation", "unknown")).lower()

        if reputation == "positive":
            return jsonify({
                "verdict": "match",
                "message": "Vendor details matched and CFPB reputation is positive.",
                "reputation": reputation,
                "reputation_summary": reputation_data.get("summary", ""),
            })

        return jsonify({
            "verdict": "not match",
            "message": "Vendor details matched, but CFPB reputation is not positive.",
            "reputation": reputation,
            "reputation_summary": reputation_data.get("summary", ""),
        })
    except Exception as exc:  # pragma: no cover - depends on runtime config
        return jsonify({"error": f"Vendor verification failed: {exc}"}), 500


@app.post("/api/vendor-phone-verify")
def vendor_phone_verify() -> Any:
    payload = request.get_json(silent=True) or {}
    reason = str(payload.get("reason", "manual phone verification")).strip() or "manual phone verification"

    try:
        phone_result_raw = initiate_phone_verification.invoke({"reason": reason})
        phone_result = json.loads(phone_result_raw)
        return jsonify({
            "verified": bool(phone_result.get("verified", True)),
            "phone_called": phone_result.get("phone_called", ""),
            "note": phone_result.get("note", ""),
            "reason": reason,
        })
    except Exception as exc:  # pragma: no cover - depends on runtime config
        return jsonify({"error": f"Phone call verification failed: {exc}"}), 500


@app.post("/api/invoice-verify")
def invoice_verify() -> Any:
    payload = request.get_json(silent=True) or {}

    vendor_name = str(payload.get("vendor_name", "")).strip() or "Unknown Vendor"
    operator_override = bool(payload.get("operator_override", False))

    # Invoice row intentionally has one button in the UI, so we generate defaults here.
    invoice_id = f"INV-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    amount = 5000.0

    try:
        verified = run_invoice_verification(
            invoice_id=invoice_id,
            amount=amount,
            vendor_name=vendor_name,
            operator_override=operator_override,
        )
        return jsonify({
            "invoice_id": invoice_id,
            "amount": amount,
            "verified": bool(verified),
        })
    except Exception as exc:  # pragma: no cover - depends on runtime model config
        return jsonify({"error": f"Invoice verification failed: {exc}"}), 500


@app.post("/api/final-result")
def final_result() -> Any:
    payload = request.get_json(silent=True) or {}

    vendor_verdict = str(payload.get("vendor_verdict", "")).strip().lower()
    invoice_verified = bool(payload.get("invoice_verified", False))

    vendor_match = vendor_verdict == "match"
    if vendor_match and invoice_verified:
        decision = "normal"
        reason = "Vendor verified and invoice confirmed."
    elif not vendor_match and not invoice_verified:
        decision = "abnormal"
        reason = "Vendor check failed and invoice verification failed."
    elif not vendor_match:
        decision = "abnormal"
        reason = f"Vendor verification result is '{vendor_verdict or 'unknown'}'."
    else:
        decision = "abnormal"
        reason = "Invoice verification failed."

    return jsonify({
        "decision": decision,
        "reason": reason,
        "vendor_verdict": vendor_verdict or "unknown",
        "invoice_verified": invoice_verified,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
