/* Manual only: never auto-dispatch on page load, refresh, or confirm. */
(function () {
  "use strict";
  const select = document.getElementById("variant");
  const checks = Array.from(document.querySelectorAll(".creator-check"));
  const review = document.getElementById("review-button");
  const counter = document.getElementById("selected-count");
  const filter = document.getElementById("creator-filter");
  function updateSelection() {
    const count = checks.filter(c => c.checked).length;
    if (counter) counter.textContent = String(count);
    if (review) review.disabled = !select || !select.value || count < 1 || count > 20;
  }
  checks.forEach(c => c.addEventListener("change", updateSelection));
  select?.addEventListener("change", updateSelection);
  filter?.addEventListener("input", () => {
    const q = filter.value.trim().toLocaleLowerCase();
    document.querySelectorAll(".creator-row").forEach(row => {
      row.hidden = q.length > 0 && !row.dataset.query.includes(q);
    });
  });
  updateSelection();

  const start = document.getElementById("start-delivery");
  const stop = document.getElementById("stop-browser");
  const events = document.getElementById("delivery-events");
  const stepUrl = document.querySelector("script[data-step-url]")?.dataset.stepUrl;
  let active = false;
  let inFlight = false;
  function report(text) { events.textContent += "\n" + text; }
  function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
  async function begin() {
    if (active || inFlight || !stepUrl) return;
    active = true;
    start.disabled = true;
    stop.disabled = false;
    events.textContent = "Delivery explicitly started in this tab.";
    const csrf = document.querySelector('input[name="csrfmiddlewaretoken"]')?.value;
    if (!csrf) {
      report("Missing session safety token. No email request made.");
      active = false;
    }
    while (active) {
      let result, response;
      try {
        inFlight = true;
        response = await fetch(stepUrl, {
          method: "POST", credentials: "same-origin",
          headers: { "X-CSRFToken": csrf, "Accept": "application/json" }
        });
        result = await response.json();
      } catch (_error) {
        report("STOPPED: uncertain network response. Refresh and review. Do not retry blindly.");
        break;
      } finally {
        inFlight = false;
      }
      if (!response.ok) {
        report("STOPPED: " + (result.reason || "Server rejected the request."));
        break;
      }
      if (result.state === "wait") {
        report("Pacing: wait " + result.seconds + " seconds.");
        await sleep(Math.min(60, Math.max(1, result.seconds)) * 1000);
        continue;
      }
      if (result.position) {
        const pill = document.querySelector("#person-" + result.position + " .batch-pill");
        if (pill) {
          pill.textContent = result.state.replaceAll("_", " ");
          pill.className = "batch-pill " + result.state;
        }
      }
      if (result.state === "sent") report("Recipient " + result.position + ": provider accepted message.");
      else if (result.state === "blocked") report("Recipient " + result.position + ": blocked. " + (result.reason || ""));
      else if (result.state === "needs_review") {
        report("STOPPED: delivery may have been accepted. Reconcile before any further sending.");
        break;
      } else if (result.state === "refresh_required") {
        report("PAUSED: " + (result.reason || "Prepared content changed.") +
               " Refresh this page, then choose Refresh & Re-review. Nothing more was sent.");
        break;
      } else if (result.state === "complete") {
        report("Completed. Refresh to see the final saved delivery statuses.");
        break;
      } else {
        report("Unexpected response: STOPPED for manual review.");
        break;
      }
    }
    active = false;
    stop.disabled = true;
    start.disabled = false;
  }
  start?.addEventListener("click", begin);
  stop?.addEventListener("click", () => {
    active = false;
    stop.disabled = true;
    report("Stopping. Any one already in-flight request may finish; no subsequent requests will start.");
    if (!inFlight) start.disabled = false;
  });
})();
