from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Flask, jsonify, render_template, request

from agents.invoice_agent import run_invoice_verification
from agents.vendor_agent import run_vendor_verification

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
        result = run_vendor_verification(
            vendor_name=vendor_name,
            account_number=account_number,
            business_address=business_address,
            phone=phone,
        )
        verdict_text = str(result.get("verdict", "")).lower()

        if "added new entry" in verdict_text:
            verdict = "added new entry"
        elif "match" in verdict_text and "not" not in verdict_text:
            verdict = "match"
        else:
            verdict = "not match"

        return jsonify({
            "verdict": verdict,
            "raw_result": result.get("verdict", ""),
        })
    except Exception as exc:  # pragma: no cover - depends on runtime model config
        return jsonify({"error": f"Vendor verification failed: {exc}"}), 500


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
