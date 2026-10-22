/**
 * DigitalSoul Observer - Popup Logic
 */

document.getElementById("dashboardBtn").addEventListener("click", () => {
  chrome.tabs.create({ url: "http://127.0.0.1:7777" });
});

const toggleBtn = document.getElementById("toggleBtn");
let isPaused = false;

toggleBtn.addEventListener("click", async () => {
  isPaused = !isPaused;
  toggleBtn.innerText = isPaused ? "Resume Observation" : "Pause Observation";
  toggleBtn.className = isPaused ? "btn btn-secondary" : "btn";

  try {
    await fetch("http://127.0.0.1:7777/api/v1/settings/observation", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        observing: !isPaused,
        allowlist: ["acme-portal", "acme-crm"],
        paused_until: isPaused ? new Date(Date.now() + 3600000).toISOString() : null,
      }),
    });
  } catch (err) {
    // Agent offline
  }
});
