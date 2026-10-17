const state = {
  vendorVerdict: null,
  invoiceVerified: null,
};

const vendorStatus = document.getElementById("vendor_status");
const invoiceStatus = document.getElementById("invoice_status");
const finalStatus = document.getElementById("final_status");

const vendorButton = document.getElementById("vendor_verify_btn");
const invoiceButton = document.getElementById("invoice_verify_btn");
const finalButton = document.getElementById("final_result_btn");

function markStatus(el, message, isError = false) {
  el.textContent = message;
  el.classList.toggle("error", isError);
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data;
}

vendorButton.addEventListener("click", async () => {
  const payload = {
    vendor_name: document.getElementById("vendor_name").value,
    account_number: document.getElementById("account_number").value,
    phone: document.getElementById("phone").value,
    business_address: document.getElementById("business_address").value,
  };

  vendorButton.disabled = true;
  markStatus(vendorStatus, "Status: running vendor phone-call verification...");

  try {
    const data = await postJson("/api/vendor-verify", payload);
    state.vendorVerdict = data.verdict;
    markStatus(vendorStatus, `Status: ${data.verdict}`);
  } catch (error) {
    markStatus(vendorStatus, `Status: ${error.message}`, true);
  } finally {
    vendorButton.disabled = false;
  }
});

invoiceButton.addEventListener("click", async () => {
  const payload = {
    vendor_name: document.getElementById("vendor_name").value,
    operator_override: false,
  };

  invoiceButton.disabled = true;
  markStatus(invoiceStatus, "Status: running invoice phone-call verification...");

  try {
    const data = await postJson("/api/invoice-verify", payload);
    state.invoiceVerified = data.verified;
    const resultText = data.verified ? "verified" : "not verified";
    markStatus(invoiceStatus, `Status: ${resultText} (${data.invoice_id})`);
  } catch (error) {
    markStatus(invoiceStatus, `Status: ${error.message}`, true);
  } finally {
    invoiceButton.disabled = false;
  }
});

finalButton.addEventListener("click", async () => {
  finalButton.disabled = true;

  if (state.vendorVerdict === null || state.invoiceVerified === null) {
    markStatus(finalStatus, "Status: run vendor and invoice verification first.", true);
    finalButton.disabled = false;
    return;
  }

  markStatus(finalStatus, "Status: computing final result...");

  try {
    const data = await postJson("/api/final-result", {
      vendor_verdict: state.vendorVerdict,
      invoice_verified: state.invoiceVerified,
    });
    markStatus(finalStatus, `Status: ${data.decision} - ${data.reason}`);
  } catch (error) {
    markStatus(finalStatus, `Status: ${error.message}`, true);
  } finally {
    finalButton.disabled = false;
  }
});
