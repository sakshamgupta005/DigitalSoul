"use strict";

// Saves the selected tag with PATCH /api/customers/{id} (the request the workflow's network_2xx check expects).
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("cust");
  if (!form) return;
  const recordId = document.querySelector('[data-testid="customer-record"]').dataset.recordId;
  const select = document.querySelector('[data-testid="tag-select"]');
  const status = document.querySelector('[data-testid="save-status"]');
  const current = document.querySelector('[data-testid="current-tag"]');

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    status.textContent = "Saving...";
    try {
      const response = await fetch(`/api/customers/${encodeURIComponent(recordId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag: select.value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ? data.error.message : response.statusText);
      current.textContent = data.customer.tag || "(no tag)";
      status.textContent = "Saved";
    } catch (error) {
      status.textContent = `Save failed: ${error.message}`;
    }
  });
});
