/* Creator admin: visible duplicate checks and an explicit single-send confirmation. */
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
    const status = panel.querySelector(".co-email-status");
    const form = emailInput && emailInput.closest("form");
    const initialEmail = emailInput ? emailInput.value.trim().toLowerCase() : "";
    let requestNumber = 0, timer = null, duplicate = false;
    const saveButtons = form
      ? Array.from(form.querySelectorAll('button[name="_save"],button[name="_save_and_send"]'))
      : [];

    if (emailInput && status && checkUrl) {
      // The email field may be at the top of a long form; place warnings right beside it.
      const field = emailInput.closest(".form-row") ||
        emailInput.closest(".field-email") || emailInput.parentElement;
      field.appendChild(status);
      status.style.cssText = "display:block;margin:9px 0 2px;padding:10px 13px;" +
        "border-radius:7px;border:1px solid #b8c8c1;background:#eef7f2;" +
        "color:#174c32;font-size:13px;max-width:660px;line-height:1.4";
      status.setAttribute("aria-live", "polite");

      function show(text, problem) {
        status.replaceChildren(document.createTextNode(text));
        status.style.borderColor = problem ? "#c66056" : "#b8c8c1";
        status.style.background = problem ? "#fff0e9" : "#eef7f2";
        status.style.color = problem ? "#842f27" : "#174c32";
      }
      function setDuplicate(flag) {
        duplicate = flag;
        emailInput.setCustomValidity(flag ? "This address already has a creator record." : "");
        saveButtons.forEach(button => { button.disabled = flag; });
      }
      async function check() {
        const sequence = ++requestNumber;
        const email = emailInput.value.trim();
        setDuplicate(false);
        if (!email) {
          show("Enter an email to check the existing creator records.", false);
          return;
        }
        if (currentPk && email.toLowerCase() === initialEmail) {
          show("This address belongs to the creator you are editing.", false);
          return;
        }
        if (!emailInput.checkValidity()) {
          show("Enter a valid email address to check for duplicates.", true);
          return;
        }
        show("Checking this address against existing creator records…", false);
        const url = new URL(checkUrl, window.location.origin);
        url.searchParams.set("email", email);
        if (currentPk) url.searchParams.set("exclude_pk", currentPk);
        try {
          const response = await fetch(url.toString(), {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
          });
          if (!response.ok) throw new Error("lookup unavailable");
          const data = await response.json();
          if (sequence !== requestNumber) return;
          setDuplicate(Boolean(data.exists));
          if (data.exists) {
            show("DUPLICATE: " + email + " is already registered to ", true);
            const strong = document.createElement("strong");
            strong.textContent = String(data.creator.name || "another creator");
            status.appendChild(strong);
            status.appendChild(document.createTextNode(
              " (status: " + String(data.creator.status || "unknown") + "). "
            ));
            const link = document.createElement("a");
            const url = String(data.edit_url || "");
            if (url.startsWith("/admin/api/creator/")) {
              link.href = url;
              link.textContent = "Open existing creator →";
              link.style.cssText = "font-weight:700;color:#842f27;text-decoration:underline";
              status.appendChild(link);
            }
          } else {
            show("No existing creator uses this address. Saving will check once more.", false);
          }
        } catch (_err) {
          if (sequence !== requestNumber) return;
          setDuplicate(false);
          show("Live duplicate check is temporarily unavailable. " +
               "The server will still reject duplicates when you save.", true);
        }
      }
      emailInput.addEventListener("input", function () {
        requestNumber++;  // Discard a stale reply while the user continues typing.
        clearTimeout(timer);
        setDuplicate(false);
        show("Checking after you finish typing…", false);
        timer = setTimeout(check, 350);
      });
      emailInput.addEventListener("blur", function () {
        clearTimeout(timer);
        check();
      });
      form?.addEventListener("submit", function (event) {
        if (duplicate) {
          event.preventDefault();
          emailInput.reportValidity();
        }
      });
      if (emailInput.value.trim()) check();
    }

    const sendWrap = panel.querySelector(".co-send-now");
    const sendBtn = panel.querySelector(".co-send-now-btn");
    const sendRes = panel.querySelector(".co-send-now-result");
    if (sendWrap && sendBtn && sendUrl && !previewOnly) {
      sendBtn.addEventListener("click", async function () {
        const address = emailInput ? emailInput.value.trim() : "this creator";
        if (emailInput && address.toLowerCase() !== initialEmail) {
          sendRes.textContent = "Save your edited address before sending.";
          sendRes.style.color = "#a64e36";
          return;
        }
        if (!window.confirm("Send ONE real Milani outreach email to " + address +
                            " now using an active approved campaign?")) return;
        sendBtn.disabled = true;
        sendRes.style.color = "#666";
        sendRes.textContent = "Submitting to provider…";
        try {
          const response = await fetch(sendUrl, {
            method: "POST", credentials: "same-origin",
            headers: { "X-CSRFToken": getCookie("csrftoken") }
          });
          const result = await response.json();
          if (response.ok && result.success) {
            sendRes.style.color = "#176640";
            sendRes.textContent = "Provider accepted the outreach to " + result.email +
              ". Check Outreach Logs for delivery status.";
            sendBtn.disabled = true; // Prevent an accidental repeat in this session.
          } else {
            sendRes.style.color = "#a64e36";
            sendRes.textContent = result.error || "Not sent. Check Outreach Logs before retrying.";
            sendBtn.disabled = false;
          }
        } catch (_err) {
          sendRes.style.color = "#a64e36";
          sendRes.textContent = "Unknown network outcome. Check Outreach Logs BEFORE trying again.";
          sendBtn.disabled = true;
        }
      });
    }
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", init);
  else init();
})();
