/*
 * static/admin/js/shipment_ai_generate.js
 *
 * Handles three things on the Shipment admin form:
 *   1. ✦ AI Generate Shipment Data  — new shipment from scratch
 *   2. ✦ Advance to Next Stage      — go to next stage automatically
 *   3. Stage Selector Panel         — visual list of all stages, click any to jump
 *
 * Drop this file in:  api/static/admin/js/shipment_ai_generate.js
 * Reference it from admin.py via:  js = ['admin/js/shipment_ai_generate.js']
 */

(function () {
  "use strict";

  const API_BASE = window.location.origin;

  // ── Utility: get Django admin CSRF token ──────────────────────────────────
  function getCsrf() {
    const el = document.querySelector("[name=csrfmiddlewaretoken]");
    return el ? el.value : "";
  }

  // ── Utility: get shipment PK from URL ─────────────────────────────────────
  function getShipmentPk() {
    // URL pattern: /admin/api/shipment/42/change/
    const m = window.location.pathname.match(/\/shipment\/(\d+)\//);
    return m ? m[1] : null;
  }

  // ── Utility: show status message on a button's sibling span ───────────────
  function setMsg(msgEl, text, type) {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.style.color = type === "error" ? "#ff6b6b" : type === "success" ? "#51cf66" : "#aaa";
  }

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 0 — Address Parser
  // Parses "Calle Príncipe de Vergara 132, 4 planta, 28002, Madrid, Spain"
  // → city: "Madrid", country: "Spain", zip: "28002"
  // ══════════════════════════════════════════════════════════════════════════
  // ── Nominatim lookup (OpenStreetMap, free, no API key) ───────────────────
  var _nominatimTimer = null;

  async function lookupAddressNominatim(raw) {
    var url = "https://nominatim.openstreetmap.org/search?" + new URLSearchParams({
      q: raw,
      format: "json",
      addressdetails: "1",
      limit: "1",
    });
    var resp = await fetch(url, {
      headers: { "Accept-Language": "en", "User-Agent": "OnTracAdminTool/1.0" }
    });
    var data = await resp.json();
    if (!data || !data.length) return null;
    var addr = data[0].address || {};
    // Nominatim returns city/town/village/municipality — pick best available
    var city = addr.city || addr.town || addr.village || addr.municipality || addr.county || "";
    var country = addr.country || "";
    var zip = addr.postcode || "";
    return { city: city, country: country, zip: zip };
  }

  function setupAddressParser() {
    var addrInput    = document.getElementById("ai-full-address");
    var cityInput    = document.getElementById("ai-dest-city");
    var countryInput = document.getElementById("ai-dest-country");
    var zipInput     = document.getElementById("ai-dest-zip");
    var statusEl     = document.getElementById("ai-addr-status");
    if (!addrInput || !cityInput || !countryInput) return;

    addrInput.addEventListener("input", function() {
      var raw = addrInput.value.trim();
      if (!raw || raw.length < 8) {
        if (statusEl) { statusEl.style.color = "#888"; statusEl.textContent = "Keep typing…"; }
        return;
      }

      // Debounce — wait 600ms after user stops typing before hitting Nominatim
      clearTimeout(_nominatimTimer);
      if (statusEl) { statusEl.style.color = "#888"; statusEl.textContent = "⏳ Looking up address…"; }

      _nominatimTimer = setTimeout(async function() {
        try {
          var parsed = await lookupAddressNominatim(raw);

          if (!parsed || (!parsed.city && !parsed.country)) {
            if (statusEl) { statusEl.style.color = "#ff6b6b"; statusEl.textContent = "✗ Address not recognised. Check spelling or enter city/country manually."; }
            return;
          }

          if (parsed.city)    cityInput.value    = parsed.city;
          if (parsed.country) countryInput.value = parsed.country;
          if (zipInput && parsed.zip) zipInput.value = parsed.zip;

          // Write destinationZip into shipmentDetails JSON field
          if (parsed.zip) {
            var sdEl = document.querySelector("#id_shipmentDetails");
            if (sdEl) {
              try {
                var sd = JSON.parse(sdEl.value) || {};
                sd.destinationZip = parsed.zip;
                if (!sd.originZip) sd.originZip = "85001";
                sdEl.value = JSON.stringify(sd, null, 2);
              } catch(e) {}
            }
          }

          if (statusEl) {
            statusEl.style.color = "#51cf66";
            statusEl.textContent = "✓ City: " + parsed.city + "  Country: " + parsed.country + (parsed.zip ? "  ZIP: " + parsed.zip : "");
          }

        } catch(err) {
          if (statusEl) { statusEl.style.color = "#ff6b6b"; statusEl.textContent = "✗ Lookup failed. Enter fields manually."; }
        }
      }, 600);
    });
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 1 — AI Generate Shipment Data button
  // ══════════════════════════════════════════════════════════════════════════
  function setupGenerateButton() {
    const btn = document.getElementById("ai-generate-btn");
    if (!btn) return;
    // Status element is id="ai-status" in admin.py HTML
    const msgEl = document.getElementById("ai-status");

    btn.addEventListener("click", async function () {
      // Read from the custom ai-dest-city / ai-dest-country inputs in the description bar
      const cityInput    = document.getElementById("ai-dest-city");
      const countryInput = document.getElementById("ai-dest-country");
      const city    = cityInput    ? cityInput.value.trim()    : "";
      const country = countryInput ? countryInput.value.trim() : "";

      if (!city || !country) {
        setMsg(msgEl, "✗ Enter destination city and country first.", "error");
        return;
      }

      btn.disabled = true;
      btn.textContent = "Generating...";
      setMsg(msgEl, "⏳ Working...", "info");

      try {
        const resp = await fetch(`${API_BASE}/api/admin/ai-generate-shipment/`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
          body: JSON.stringify({ destination_city: city, destination_country: country }),
        });
        const data = await resp.json();

        if (!resp.ok || data.error) {
          setMsg(msgEl, "✗ " + (data.error || "Generation failed."), "error");
          return;
        }

        // resp.data is the actual payload (view wraps in {success, data})
        const d = data.data || data;

        // Fill form fields
        _setField("status",            d.status);
        _setField("destination",       d.destination);
        _setField("destination_city",  d.destination_city);
        _setField("destination_country", d.destination_country);
        _setField("current_stage_key",   d.current_stage_key);
        _setField("current_stage_index", d.current_stage_index);
        _setField("progressPercent",     d.progressPercent);
        _setField("expectedDate",        d.expectedDate);
        _setJsonField("recentEvent",     d.recentEvent);
        _setJsonField("allEvents",       d.allEvents);
        if (d.progressLabels)   _setJsonField("progressLabels",  d.progressLabels);
        if (d.shipmentDetails) {
          // Preserve destinationZip already parsed from address input
          var zipInput = document.getElementById("ai-dest-zip");
          if (zipInput && zipInput.value) d.shipmentDetails.destinationZip = zipInput.value;
          _setJsonField("shipmentDetails", d.shipmentDetails);
        }

        // Update requiresPayment checkbox
        const reqPay = document.querySelector("#id_requiresPayment");
        if (reqPay && reqPay.type === "checkbox") reqPay.checked = !!d.requiresPayment;

        setMsg(msgEl, "✓ All fields populated. Review then save.", "success");

        // Refresh the stage panel if on existing shipment
        const pk = getShipmentPk();
        if (pk) loadStagePanel(pk);

      } catch (err) {
        setMsg(msgEl, "✗ Network error: " + err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "✦ AI Generate Shipment Data";
      }
    });
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 2 — Advance to Next Stage button
  // ══════════════════════════════════════════════════════════════════════════
  function setupAdvanceButton() {
    const btn = document.getElementById("ai-advance-btn");
    if (!btn) return;
    // Status element is id="ai-advance-status" in admin.py HTML
    const msgEl = document.getElementById("ai-advance-status");
    const pk = getShipmentPk();

    if (!pk) {
      btn.disabled = true;
      setMsg(msgEl, "Save shipment first to use Advance.", "info");
      return;
    }

    btn.addEventListener("click", async function () {
      btn.disabled = true;
      btn.textContent = "Advancing...";
      setMsg(msgEl, "Processing...", "info");

      try {
        const resp = await fetch(`${API_BASE}/api/admin/ai-advance-stage/`, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
          body: JSON.stringify({ shipment_id: parseInt(pk) }),
        });
        const data = await resp.json();

        if (!resp.ok || data.error) {
          setMsg(msgEl, data.error || "Advance failed.", "error");
          return;
        }

        // View wraps result in {success, data, stages_filled, message}
        const d = data.data || data;
        applyStageData(d);
        const stagesFilled = d._stages_added || data.stages_filled || 1;
        const label = d._jumped_to_label || data.message || d.status || "";
        const skipped = stagesFilled > 1 ? `Caught up ${stagesFilled} stages → ` : "";
        setMsg(msgEl, `✓ ${skipped}${label}. Review then save.`, "success");

        // Refresh stage panel
        loadStagePanel(pk);

      } catch (err) {
        setMsg(msgEl, "Network error: " + err.message, "error");
      } finally {
        btn.disabled = false;
        btn.textContent = "✦ Advance to Next Stage";
      }
    });
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 3 — Visual Stage Selector Panel
  // ══════════════════════════════════════════════════════════════════════════
  function buildStagePanelContainer() {
    // Only build on existing shipment pages
    const pk = getShipmentPk();
    if (!pk) return;

    // Find the advance button row and insert panel below it
    const advanceBtn = document.getElementById("ai-advance-btn");
    if (!advanceBtn) return;

    const wrapper = advanceBtn.closest(".field-box, .form-row, td, div") || advanceBtn.parentElement;

    const panel = document.createElement("div");
    panel.id = "stage-selector-panel";
    panel.style.cssText = `
      margin-top: 14px;
      padding: 14px 16px;
      background: #1a1a2e;
      border: 1px solid #333;
      border-radius: 8px;
      font-family: monospace;
      font-size: 12px;
    `;
    panel.innerHTML = `
      <div style="color:#888; margin-bottom:10px; font-size:11px; letter-spacing:1px; text-transform:uppercase;">
        Stage Selector — Click any stage to jump directly
      </div>
      <div id="stage-list" style="display:flex; flex-direction:column; gap:4px;"></div>
      <div id="stage-panel-msg" style="margin-top:8px; font-size:11px; color:#aaa;"></div>
    `;

    wrapper.insertAdjacentElement("afterend", panel);
    loadStagePanel(pk);
  }

  async function loadStagePanel(pk) {
    const listEl = document.getElementById("stage-list");
    if (!listEl) return;

    listEl.innerHTML = `<div style="color:#666; padding:6px;">Loading stages...</div>`;

    try {
      const resp = await fetch(`${API_BASE}/api/admin/ai-stage-pipeline/?shipment_id=${pk}`);
      const data = await resp.json();

      if (!resp.ok || data.error) {
        listEl.innerHTML = `<div style="color:#ff6b6b;">Could not load stages: ${data.error || "unknown error"}</div>`;
        return;
      }

      renderStageList(listEl, data.pipeline, pk);

    } catch (err) {
      listEl.innerHTML = `<div style="color:#ff6b6b;">Network error loading stages.</div>`;
    }
  }

  function renderStageList(listEl, pipeline, pk) {
    listEl.innerHTML = "";

    pipeline.forEach(function (stage) {
      const row = document.createElement("div");

      let bgColor = "transparent";
      let textColor = "#555";
      let border = "1px solid #2a2a2a";
      let cursor = "pointer";
      let icon = "○";

      if (stage.is_completed) {
        textColor = "#51cf66";
        icon = "✓";
        border = "1px solid #2a3a2a";
        bgColor = "#0d1f0d";
      } else if (stage.is_current) {
        textColor = "#ffd43b";
        icon = "►";
        border = "1px solid #4a3a00";
        bgColor = "#1f1a00";
        cursor = "default";
      } else if (stage.requires_payment) {
        textColor = "#ff8c42";
        icon = "$";
      }

      row.style.cssText = `
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 5px 10px;
        border-radius: 5px;
        background: ${bgColor};
        border: ${border};
        color: ${textColor};
        cursor: ${cursor};
        transition: background 0.15s;
      `;

      const indexBadge = document.createElement("span");
      indexBadge.style.cssText = "width:18px; text-align:center; opacity:0.5; font-size:10px; flex-shrink:0;";
      indexBadge.textContent = stage.index + 1;

      const iconSpan = document.createElement("span");
      iconSpan.style.cssText = "width:14px; text-align:center; flex-shrink:0;";
      iconSpan.textContent = icon;

      const labelSpan = document.createElement("span");
      labelSpan.style.cssText = "flex:1;";
      labelSpan.textContent = stage.label;

      const locSpan = document.createElement("span");
      locSpan.style.cssText = "opacity:0.45; font-size:10px; max-width:200px; text-align:right; overflow:hidden; white-space:nowrap; text-overflow:ellipsis;";
      locSpan.textContent = stage.location;

      row.appendChild(indexBadge);
      row.appendChild(iconSpan);
      row.appendChild(labelSpan);
      row.appendChild(locSpan);

      // Hover effect for future stages
      if (stage.is_future) {
        row.addEventListener("mouseenter", function () {
          row.style.background = "#1a1a3a";
          row.style.borderColor = "#444";
          row.style.color = "#ccc";
        });
        row.addEventListener("mouseleave", function () {
          row.style.background = "transparent";
          row.style.borderColor = "#2a2a2a";
          row.style.color = textColor;
        });

        row.addEventListener("click", function () {
          jumpToStage(pk, stage.key, stage.label);
        });
      }

      listEl.appendChild(row);
    });
  }

  async function jumpToStage(pk, stageKey, stageLabel) {
    const msgEl = document.getElementById("stage-panel-msg");
    const advanceMsgEl = document.getElementById("ai-advance-status");

    setMsg(msgEl, `Jumping to "${stageLabel}"...`, "info");

    try {
      const resp = await fetch(`${API_BASE}/api/admin/ai-advance-stage/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrf() },
        body: JSON.stringify({ shipment_id: parseInt(pk), target_stage_key: stageKey }),
      });
      const data = await resp.json();

      if (!resp.ok || data.error) {
        setMsg(msgEl, "Error: " + (data.error || "failed"), "error");
        return;
      }

      // View wraps result in {success, data, stages_filled, message}
      const d = data.data || data;
      applyStageData(d);
      const stagesFilled = d._stages_added || data.stages_filled || 1;
      const skipped = stagesFilled > 1 ? ` (filled ${stagesFilled} intermediate stages)` : "";
      setMsg(msgEl, `✓ Jumped to "${stageLabel}"${skipped}. Review then save.`, "success");
      if (advanceMsgEl) setMsg(advanceMsgEl, `✓ → ${stageLabel}. Review then save.`, "success");

      // Re-render panel
      loadStagePanel(pk);

    } catch (err) {
      setMsg(msgEl, "Network error: " + err.message, "error");
    }
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 4 — Shared helpers
  // ══════════════════════════════════════════════════════════════════════════

  function applyStageData(d) {
    // d is the inner result dict from advance_shipment_stage()
    _setField("status",              d.status);
    _setField("current_stage_key",   d.current_stage_key);
    _setField("current_stage_index", d.current_stage_index);
    _setField("progressPercent",     d.progressPercent);
    _setJsonField("recentEvent",     d.recentEvent);
    _setJsonField("allEvents",       d.allEvents);
    if (d.progressLabels) _setJsonField("progressLabels", d.progressLabels);

    // requiresPayment — handle both checkbox and text field
    const reqPay = document.querySelector("#id_requiresPayment");
    if (reqPay) {
      if (reqPay.type === "checkbox") {
        reqPay.checked = !!d.requiresPayment;
      } else {
        reqPay.value = d.requiresPayment ? "true" : "false";
      }
    }
  }

  function _setField(name, value) {
    if (value === undefined || value === null) return;
    const el = document.querySelector(`#id_${name}, [name=${name}]`);
    if (el) el.value = value;
  }

  function _setJsonField(name, value) {
    if (value === undefined || value === null) return;
    const el = document.querySelector(`#id_${name}, [name=${name}]`);
    if (el) el.value = JSON.stringify(value, null, 2);
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 5 — Visual Timeline Editor
  // Renders allEvents JSON as an editable table with live validation.
  // Dates must be sequential — conflicts highlighted red instantly.
  // Syncs back to the hidden JSON textarea on every change.
  // ══════════════════════════════════════════════════════════════════════════

  function setupTimelineEditor() {
    const jsonField = document.querySelector("#id_allEvents");
    if (!jsonField) return;

    // Build container
    const wrapper = document.createElement("div");
    wrapper.id = "timeline-editor-wrapper";
    wrapper.style.cssText = "margin-top:16px;";

    const header = document.createElement("div");
    header.style.cssText = "display:flex;align-items:center;gap:12px;margin-bottom:8px;";
    header.innerHTML = `
      <span style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;">
        Timeline Editor
      </span>
      <button type="button" id="timeline-refresh-btn"
        style="padding:3px 10px;background:#2a2a3a;border:1px solid #444;border-radius:4px;
               color:#aaa;font-size:11px;cursor:pointer;">
        ↻ Reload from JSON
      </button>
      <span id="timeline-validation-msg" style="font-size:11px;color:#ff6b6b;"></span>
    `;

    const table = document.createElement("div");
    table.id = "timeline-editor-table";
    table.style.cssText = "display:flex;flex-direction:column;gap:3px;";

    wrapper.appendChild(header);
    wrapper.appendChild(table);

    // Walk up to find Django's field wrapper div and insert after it
    let insertTarget = jsonField;
    let parent = jsonField.parentElement;
    while (parent && !parent.classList.contains("form-row") && !parent.classList.contains("field-allEvents")) {
      insertTarget = parent;
      parent = parent.parentElement;
    }
    insertTarget.insertAdjacentElement("afterend", wrapper);

    function parseEvents() {
      try { return JSON.parse(jsonField.value) || []; }
      catch(e) { return []; }
    }

    function toInputValue(dateStr) {
      // "2026-03-02 at 8:50 AM" → "2026-03-02T08:50"
      try {
        const m = dateStr.match(/(\d{4}-\d{2}-\d{2}) at (\d+):(\d+) (AM|PM)/);
        if (!m) return "";
        let h = parseInt(m[2]);
        const min = m[3];
        const ampm = m[4];
        if (ampm === "PM" && h !== 12) h += 12;
        if (ampm === "AM" && h === 12) h = 0;
        return `${m[1]}T${String(h).padStart(2,"0")}:${min}`;
      } catch(e) { return ""; }
    }

    function fromInputValue(val) {
      // "2026-03-02T08:50" → "2026-03-02 at 8:50 AM"
      try {
        const [datePart, timePart] = val.split("T");
        let [h, min] = timePart.split(":").map(Number);
        const ampm = h >= 12 ? "PM" : "AM";
        if (h > 12) h -= 12;
        if (h === 0) h = 12;
        return `${datePart} at ${h}:${String(min).padStart(2,"0")} ${ampm}`;
      } catch(e) { return val; }
    }

    function validateAndSync(events) {
      const msgEl = document.getElementById("timeline-validation-msg");
      let hasError = false;

      // Check all sequential
      for (let i = 1; i < events.length; i++) {
        const prev = events[i-1]._inputEl;
        const curr = events[i]._inputEl;
        if (!prev || !curr) continue;
        const prevVal = prev.value;
        const currVal = curr.value;
        if (currVal && prevVal && currVal <= prevVal) {
          curr.style.borderColor = "#ff6b6b";
          curr.style.background = "#2a0a0a";
          hasError = true;
        } else {
          if (curr.style.borderColor === "rgb(255, 107, 107)") {
            curr.style.borderColor = "#333";
            curr.style.background = "#1a1a2e";
          }
        }
      }

      if (hasError) {
        msgEl.textContent = "⚠ Timestamp conflict — red fields are out of order";
      } else {
        msgEl.textContent = "";
      }

      // Write back to JSON textarea
      const cleaned = events.map(ev => {
        const copy = Object.assign({}, ev);
        delete copy._inputEl;
        if (ev._inputEl && ev._inputEl.value) {
          copy.date = fromInputValue(ev._inputEl.value);
          if (copy.timestamp !== undefined) copy.timestamp = copy.date;
        }
        return copy;
      });
      jsonField.value = JSON.stringify(cleaned, null, 2);
    }

    function renderTable() {
      const events = parseEvents();
      table.innerHTML = "";

      // Header row
      const hrow = document.createElement("div");
      hrow.style.cssText = "display:grid;grid-template-columns:28px 1fr 200px 1fr;gap:6px;padding:4px 8px;";
      hrow.innerHTML = `
        <span style="font-size:10px;color:#555;">#</span>
        <span style="font-size:10px;color:#555;text-transform:uppercase;">Event</span>
        <span style="font-size:10px;color:#555;text-transform:uppercase;">Date &amp; Time</span>
        <span style="font-size:10px;color:#555;text-transform:uppercase;">City</span>
      `;
      table.appendChild(hrow);

      const eventsWithRefs = events.map(ev => Object.assign({}, ev));

      eventsWithRefs.forEach(function(ev, i) {
        const row = document.createElement("div");
        row.style.cssText = `
          display:grid;grid-template-columns:28px 1fr 200px 1fr;gap:6px;
          padding:5px 8px;background:#0d0d1a;border:1px solid #1e1e30;
          border-radius:5px;align-items:center;
        `;

        const numEl = document.createElement("span");
        numEl.style.cssText = "font-size:10px;color:#444;text-align:center;";
        numEl.textContent = i + 1;

        const eventEl = document.createElement("span");
        eventEl.style.cssText = "font-size:12px;color:#c8cfe0;";
        eventEl.textContent = ev.event || "";

        const inputEl = document.createElement("input");
        inputEl.type = "datetime-local";
        inputEl.value = toInputValue(ev.date || "");
        inputEl.style.cssText = `
          background:#1a1a2e;border:1px solid #333;border-radius:4px;
          color:#d4d9ee;font-size:11px;padding:3px 6px;width:100%;
          box-sizing:border-box;font-family:monospace;
        `;

        const cityEl = document.createElement("span");
        cityEl.style.cssText = "font-size:11px;color:#555;";
        cityEl.textContent = ev.city || "";

        ev._inputEl = inputEl;
        eventsWithRefs[i]._inputEl = inputEl;

        inputEl.addEventListener("change", function() {
          validateAndSync(eventsWithRefs);
        });

        row.appendChild(numEl);
        row.appendChild(eventEl);
        row.appendChild(inputEl);
        row.appendChild(cityEl);
        table.appendChild(row);
      });

      // Initial sync to catch any existing errors
      validateAndSync(eventsWithRefs);
    }

    renderTable();

    document.getElementById("timeline-refresh-btn").addEventListener("click", renderTable);

    // Re-render when allEvents textarea is externally updated (e.g. after Advance)
    const observer = new MutationObserver(renderTable);
    observer.observe(jsonField, { attributes: true, childList: false, subtree: false });

    // Also re-render after stage advance populates the field
    const origAdvanceBtn = document.getElementById("ai-advance-btn");
    if (origAdvanceBtn) {
      origAdvanceBtn.addEventListener("click", function() {
        setTimeout(renderTable, 1500);
      });
    }
  }

  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 6 — Recent Event Editor
  // Replaces recentEvent JSON textarea with 4 labeled inputs
  // ══════════════════════════════════════════════════════════════════════════
  function setupRecentEventEditor() {
    const jsonField = document.querySelector("#id_recentEvent");
    if (!jsonField) return;

    const wrapper = document.createElement("div");
    wrapper.style.cssText = "margin-top:10px;padding:12px 14px;background:#0d0d1a;border:1px solid #1e1e30;border-radius:6px;";

    wrapper.innerHTML = `
      <div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">
        Recent Event Editor
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Event</label>
          <input id="re-event" type="text" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Location</label>
          <input id="re-location" type="text" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Timestamp</label>
          <input id="re-timestamp" type="datetime-local" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Status (optional override)</label>
          <input id="re-status" type="text" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div style="grid-column:1/-1;">
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Description</label>
          <textarea id="re-description" rows="2" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;resize:vertical;"></textarea>
        </div>
      </div>
      <div style="margin-top:6px;font-size:10px;color:#444;">Auto-saves to JSON field on every keystroke</div>
    `;

    jsonField.insertAdjacentElement("afterend", wrapper);

    // Parse existing JSON into inputs
    function loadFromJson() {
      try {
        const data = JSON.parse(jsonField.value) || {};
        document.getElementById("re-event").value       = data.event       || data.status || "";
        document.getElementById("re-location").value    = data.location    || "";
        document.getElementById("re-description").value = data.description || "";
        document.getElementById("re-status").value      = data.status      || "";
        // Convert timestamp to datetime-local
        const tsInput = document.getElementById("re-timestamp");
        if (data.timestamp) {
          tsInput.value = toInputValue(data.timestamp);
        }
      } catch(e) {}
    }

    // Write inputs back to JSON field
    function syncToJson() {
      const eventVal = document.getElementById("re-event").value.trim();
      const tsRaw    = document.getElementById("re-timestamp").value;
      const tsFormatted = tsRaw ? fromInputValue(tsRaw) : "";
      const obj = {
        event:       eventVal,
        location:    document.getElementById("re-location").value.trim(),
        description: document.getElementById("re-description").value.trim(),
        timestamp:   tsFormatted,
      };
      const statusVal = document.getElementById("re-status").value.trim();
      if (statusVal) obj.status = statusVal;
      jsonField.value = JSON.stringify(obj, null, 2);
    }

    // toInputValue and fromInputValue already defined in setupTimelineEditor scope
    // Redefine locally here so this section is self-contained
    function toInputValue(dateStr) {
      try {
        const m = dateStr.match(/(\d{4}-\d{2}-\d{2}) at (\d+):(\d+) (AM|PM)/);
        if (!m) return "";
        let h = parseInt(m[2]);
        const min = m[3];
        const ampm = m[4];
        if (ampm === "PM" && h !== 12) h += 12;
        if (ampm === "AM" && h === 12) h = 0;
        return `${m[1]}T${String(h).padStart(2,"0")}:${min}`;
      } catch(e) { return ""; }
    }

    function fromInputValue(val) {
      try {
        const [datePart, timePart] = val.split("T");
        let [h, min] = timePart.split(":").map(Number);
        const ampm = h >= 12 ? "PM" : "AM";
        if (h > 12) h -= 12;
        if (h === 0) h = 12;
        return `${datePart} at ${h}:${String(min).padStart(2,"0")} ${ampm}`;
      } catch(e) { return val; }
    }

    loadFromJson();

    ["re-event","re-location","re-description","re-status","re-timestamp"].forEach(function(id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("input", syncToJson);
      if (el) el.addEventListener("change", syncToJson);
    });

    // Re-load when Advance Stage populates the field
    const advBtn = document.getElementById("ai-advance-btn");
    if (advBtn) {
      advBtn.addEventListener("click", function() {
        setTimeout(loadFromJson, 1500);
      });
    }
  }


  // ══════════════════════════════════════════════════════════════════════════
  // SECTION 7 — Shipment Details Editor
  // Replaces shipmentDetails JSON textarea with 5 labeled inputs
  // ══════════════════════════════════════════════════════════════════════════
  function setupShipmentDetailsEditor() {
    const jsonField = document.querySelector("#id_shipmentDetails");
    if (!jsonField) return;

    const wrapper = document.createElement("div");
    wrapper.style.cssText = "margin-top:10px;padding:12px 14px;background:#0d0d1a;border:1px solid #1e1e30;border-radius:6px;";

    wrapper.innerHTML = `
      <div style="font-size:11px;font-weight:700;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;">
        Shipment Details Editor
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;">
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Service</label>
          <select id="sd-service" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;">
            <option value="International Priority">International Priority</option>
            <option value="Ground">Ground</option>
            <option value="Express">Express</option>
          </select>
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Weight</label>
          <input id="sd-weight" type="text" placeholder="4.3 lbs" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Dimensions</label>
          <input id="sd-dimensions" type="text" placeholder='12" x 10" x 4"' style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Origin ZIP</label>
          <input id="sd-originzip" type="text" placeholder="85043" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
        <div>
          <label style="font-size:10px;color:#555;text-transform:uppercase;display:block;margin-bottom:3px;">Destination ZIP</label>
          <input id="sd-destzip" type="text" placeholder="28002" style="width:100%;box-sizing:border-box;background:#1a1a2e;border:1px solid #333;border-radius:4px;color:#d4d9ee;font-size:12px;padding:5px 8px;font-family:monospace;">
        </div>
      </div>
      <div style="margin-top:6px;font-size:10px;color:#444;">Auto-saves to JSON field on every keystroke</div>
    `;

    jsonField.insertAdjacentElement("afterend", wrapper);

    function loadFromJson() {
      try {
        const data = JSON.parse(jsonField.value) || {};
        const svc = document.getElementById("sd-service");
        if (svc) svc.value = data.service || "International Priority";
        document.getElementById("sd-weight").value     = data.weight     || "";
        document.getElementById("sd-dimensions").value = data.dimensions || '12" x 10" x 4"';
        document.getElementById("sd-originzip").value  = data.originZip  || "85043";
        document.getElementById("sd-destzip").value    = data.destinationZip || "";
      } catch(e) {}
    }

    function syncToJson() {
      const obj = {
        service:        document.getElementById("sd-service").value,
        weight:         document.getElementById("sd-weight").value.trim(),
        dimensions:     document.getElementById("sd-dimensions").value.trim(),
        originZip:      document.getElementById("sd-originzip").value.trim(),
        destinationZip: document.getElementById("sd-destzip").value.trim(),
      };
      jsonField.value = JSON.stringify(obj, null, 2);
    }

    loadFromJson();

    ["sd-service","sd-weight","sd-dimensions","sd-originzip","sd-destzip"].forEach(function(id) {
      const el = document.getElementById(id);
      if (el) el.addEventListener("input", syncToJson);
      if (el) el.addEventListener("change", syncToJson);
    });
  }


  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  function init() {
    setupAddressParser();
    setupGenerateButton();
    setupAdvanceButton();
    buildStagePanelContainer();
    setupTimelineEditor();
    setupRecentEventEditor();
    setupShipmentDetailsEditor();
  }
})();