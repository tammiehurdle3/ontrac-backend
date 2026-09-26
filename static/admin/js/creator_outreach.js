/*
 * static/admin/js/creator_outreach.js
 *
 * Creator admin add/edit page tools:
 *   1. Live duplicate-email check as you type the email.
 *   2. "Save & Send Outreach" is a normal submit button (name=_save_and_send)
 *      handled server-side, so no JS is needed for it to work.
 *   3. "Send outreach now" (edit page only) — sends without re-saving.
 *
 * All URLs come from data-* attributes on .creator-outreach-tools, which are
 * built server-side with reverse(); this file hardcodes nothing.
 */
(function () {
  "use strict";

  function getCookie(name) {
    const m = document.cookie.match("(^|;)\\s*" + name + "\\s*=\\s*([^;]+)");
    return m ? m.pop() : "";
  }

  function init() {
    const panel = document.querySelector(".creator-outreach-tools");
    if (!panel) return;

    const checkUrl = panel.dataset.checkUrl || "";
    const sendUrl = panel.dataset.sendUrl || "";
    const currentPk = panel.dataset.currentPk || "";
    const previewOnly = panel.dataset.previewOnly === "true";

    const emailInput = document.getElementById("id_email");
    const statusEl = panel.querySelector(".co-email-status");

    // ── 1. Live duplicate-email check ──────────────────────────────────────
    if (emailInput && statusEl && checkUrl) {
      let timer = null;

      const runCheck = function () {
        const email = (emailInput.value || "").trim();
        if (!email) {
          statusEl.style.color = "#888";
          statusEl.textContent = "Enter an email above to check for duplicates.";
          return;
        }
        statusEl.style.color = "#888";
        statusEl.textContent = "Checking email…";

        let url = checkUrl + "?email=" + encodeURIComponent(email);
        if (currentPk) url += "&exclude_pk=" + encodeURIComponent(currentPk);

        fetch(url, { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (data.exists) {
              statusEl.style.color = "#dc3545";
              // Creator names are external input. Never concatenate them into HTML.
              statusEl.replaceChildren(document.createTextNode("Already exists: "));
              const strong = document.createElement("strong");
              strong.textContent = String(data.creator.name || "");
              statusEl.appendChild(strong);
              statusEl.appendChild(document.createTextNode(
                " (status: " + String(data.creator.status || "Unknown") + ") — "
              ));
              const href = String(data.edit_url || "");
              if (href.startsWith("/admin/api/creator/")) {
                const link = document.createElement("a");
                link.href = href;
                link.textContent = "open existing record";
                statusEl.appendChild(link);
              }
            } else {
              statusEl.style.color = "#28a745";
              statusEl.textContent = "Email is available — not in the database yet.";
            }
          })
          .catch(function () {
            statusEl.style.color = "#888";
            statusEl.textContent = "Could not check email right now.";
          });
      };

      const schedule = function () {
        clearTimeout(timer);
        timer = setTimeout(runCheck, 400);
      };

      emailInput.addEventListener("input", schedule);
      emailInput.addEventListener("blur", runCheck);
      if (emailInput.value.trim()) runCheck();
    }

    // ── 2. Send outreach now (edit page only) ──────────────────────────────
    const sendWrap = panel.querySelector(".co-send-now");
    const sendBtn = panel.querySelector(".co-send-now-btn");
    const sendRes = panel.querySelector(".co-send-now-result");

    if (sendWrap && sendBtn && sendUrl && !previewOnly) {
      sendWrap.hidden = false;
      sendBtn.addEventListener("click", function () {
        sendBtn.disabled = true;
        sendRes.style.color = "#888";
        sendRes.textContent = "Sending…";

        fetch(sendUrl, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": getCookie("csrftoken") },
        })
          .then(function (r) {
            return r.json().then(function (data) { return { ok: r.ok, data: data }; });
          })
          .then(function (res) {
            sendBtn.disabled = false;
            if (res.ok && res.data.success) {
              sendRes.style.color = "#28a745";
              sendRes.textContent = "Sent to " + res.data.email;
            } else {
              sendRes.style.color = "#dc3545";
              sendRes.textContent = res.data.error || "Send failed";
            }
          })
          .catch(function (e) {
            sendBtn.disabled = false;
            sendRes.style.color = "#dc3545";
            sendRes.textContent = "Network error: " + e;
          });
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
