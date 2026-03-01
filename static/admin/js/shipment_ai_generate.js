/**
 * static/admin/js/shipment_ai_generate.js
 *
 * Handles:
 *  1. Address parser — paste full address, auto-fills city/country/zip
 *  2. AI Generate Shipment Data button
 *  3. Advance to Next Stage button (with catch-up support)
 */

(function () {
    "use strict";

    // ─── CSRF ────────────────────────────────────────────────────────────────
    function getCookie(name) {
        let val = null;
        document.cookie.split(';').forEach(function (c) {
            c = c.trim();
            if (c.startsWith(name + '=')) val = decodeURIComponent(c.slice(name.length + 1));
        });
        return val;
    }

    // ─── FORM FIELD HELPERS ──────────────────────────────────────────────────
    function setField(id, value) {
        var el = document.getElementById(id);
        if (el && value !== undefined && value !== null) el.value = value;
    }

    function getField(id) {
        var el = document.getElementById(id);
        return el ? el.value.trim() : '';
    }

    function setJSON(id, obj) {
        var el = document.getElementById(id);
        if (el && obj !== undefined && obj !== null) {
            el.value = JSON.stringify(obj, null, 2);
        }
    }

    function getJSON(id) {
        var el = document.getElementById(id);
        if (!el || !el.value.trim()) return null;
        try { return JSON.parse(el.value); } catch (e) { return null; }
    }

    // ─── POPULATE ALL FIELDS FROM API RESPONSE ───────────────────────────────
    function populateFields(data) {
        setField('id_trackingId',      data.trackingId     || '');
        setField('id_status',          data.status         || '');
        setField('id_destination',     data.destination    || data.trackingId && '' || '');
        setField('id_expectedDate',    data.expectedDate   || '');
        setField('id_progressPercent', data.progressPercent != null ? data.progressPercent : '');

        // destination might come back as separate city+country — build it
        if (data.destination) {
            setField('id_destination', data.destination);
        } else if (data.destinationCity && data.destinationCountry) {
            setField('id_destination', data.destinationCity + ', ' + data.destinationCountry);
        }

        setJSON('id_progressLabels',  data.progressLabels);
        setJSON('id_recentEvent',     data.recentEvent);
        setJSON('id_allEvents',       data.allEvents);
        setJSON('id_shipmentDetails', data.shipmentDetails);

        // Fill destination zip into shipmentDetails if we have it from address parser
        var zip = getField('ai-dest-zip');
        if (zip) {
            var sd = getJSON('id_shipmentDetails') || {};
            sd.destinationZip = zip;
            setJSON('id_shipmentDetails', sd);
        }
    }

    // ─── GENERIC API CALLER ──────────────────────────────────────────────────
    function callAPI(url, payload, statusEl, onSuccess) {
        statusEl.style.color = '#888';
        statusEl.textContent = '⏳ Working...';

        fetch(url, {
            method:  'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken':  getCookie('csrftoken'),
            },
            body: JSON.stringify(payload),
        })
        .then(function (r) { return r.json(); })
        .then(function (resp) {
            if (resp.success) {
                onSuccess(resp);
            } else {
                statusEl.style.color = '#c00';
                statusEl.textContent = '✗ Error: ' + (resp.error || 'Unknown error');
            }
        })
        .catch(function (err) {
            statusEl.style.color = '#c00';
            statusEl.textContent = '✗ Network error: ' + err.message;
        });
    }

    // ─── ADDRESS PARSER ──────────────────────────────────────────────────────
    /**
     * Parses a pasted full address into city, country, and zip.
     *
     * Handles formats like:
     *   15 Adeola Odeku Street, Victoria Island, Lagos, 101233, Nigeria
     *   Calle Gran Vía 45, Madrid, 28013, Spain
     *   123 Main St, Toronto, ON M5V 3A8, Canada
     *   10 Downing Street, London, SW1A 2AA, United Kingdom
     */
    function parseAddress(raw) {
        var result = { city: '', country: '', zip: '' };
        if (!raw || !raw.trim()) return result;

        var parts = raw.split(',').map(function (p) { return p.trim(); }).filter(Boolean);
        if (parts.length < 2) return result;

        // Country is always the last part
        result.country = parts[parts.length - 1];

        // Find zip: look for a part that is mostly digits or standard postal code pattern
        var zipPattern = /^[A-Z0-9]{3,10}(\s[A-Z0-9]{3,6})?$/i;
        var zipIdx = -1;
        for (var i = parts.length - 2; i >= 1; i--) {
            var clean = parts[i].replace(/\s+/g, ' ').trim();
            // Pure digits
            if (/^\d{4,10}$/.test(clean)) { zipIdx = i; break; }
            // Postal code mixed (e.g. M5V 3A8, SW1A 2AA)
            if (zipPattern.test(clean) && clean.length <= 10) { zipIdx = i; break; }
            // Part that contains digits alongside letters (e.g. "ON M5V 3A8")
            var zipMatch = clean.match(/\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}|\d{4,6})\b/i);
            if (zipMatch) { result.zip = zipMatch[1]; zipIdx = i; break; }
        }

        if (zipIdx !== -1 && !result.zip) {
            result.zip = parts[zipIdx];
        }

        // City: the part just before the zip (or 2nd to last before country if no zip)
        var citySearchEnd = zipIdx !== -1 ? zipIdx : parts.length - 1;
        // Walk backwards from before zip to find the city
        // City is usually the last meaningful non-street part
        for (var j = citySearchEnd - 1; j >= 1; j--) {
            var candidate = parts[j];
            // Skip state/province codes (short 2-3 char all caps like "ON", "CA", "NY")
            if (/^[A-Z]{2,3}$/.test(candidate)) continue;
            // Skip if it looks like a street (contains digits at start)
            if (/^\d/.test(candidate)) continue;
            result.city = candidate;
            break;
        }

        // Fallback: if city still empty, use part before country (or before zip)
        if (!result.city) {
            var fallbackIdx = zipIdx !== -1 ? zipIdx - 1 : parts.length - 2;
            if (fallbackIdx >= 0) result.city = parts[fallbackIdx];
        }

        return result;
    }

    // ─── INIT ON DOM READY ───────────────────────────────────────────────────
    document.addEventListener('DOMContentLoaded', function () {

        // ── Address parser input ─────────────────────────────────────────────
        var addrInput   = document.getElementById('ai-full-address');
        var cityInput   = document.getElementById('ai-dest-city');
        var countryInput = document.getElementById('ai-dest-country');
        var zipInput    = document.getElementById('ai-dest-zip');
        var addrStatus  = document.getElementById('ai-addr-status');

        if (addrInput) {
            addrInput.addEventListener('input', function () {
                var raw    = addrInput.value;
                var parsed = parseAddress(raw);

                if (parsed.city)    { cityInput.value    = parsed.city;    }
                if (parsed.country) { countryInput.value = parsed.country; }
                if (zipInput && parsed.zip) { zipInput.value = parsed.zip; }

                if (addrStatus) {
                    if (parsed.city && parsed.country) {
                        addrStatus.style.color = '#1a7f5a';
                        addrStatus.textContent = '✓ City: ' + parsed.city +
                            '  Country: ' + parsed.country +
                            (parsed.zip ? '  ZIP: ' + parsed.zip : '');
                    } else {
                        addrStatus.style.color = '#888';
                        addrStatus.textContent = 'Keep typing...';
                    }
                }
            });
        }

        // ── AI Generate button ───────────────────────────────────────────────
        var generateBtn = document.getElementById('ai-generate-btn');
        var generateStatus = document.getElementById('ai-status');

        if (generateBtn) {
            generateBtn.addEventListener('click', function () {
                var city    = cityInput  ? cityInput.value.trim()    : '';
                var country = countryInput ? countryInput.value.trim() : '';

                if (!city || !country) {
                    generateStatus.style.color = '#c00';
                    generateStatus.textContent = '✗ Enter destination city and country first.';
                    return;
                }

                callAPI(
                    '/api/admin/ai-generate-shipment/',
                    { destination_city: city, destination_country: country },
                    generateStatus,
                    function (resp) {
                        var data = resp.data;
                        // Set destination field manually since generate doesn't know it
                        data.destination = city + ', ' + country;
                        populateFields(data);
                        generateStatus.style.color = '#1a7f5a';
                        generateStatus.textContent = '✓ All fields populated. Review then save.';
                    }
                );
            });
        }

        // ── Advance to Next Stage button ─────────────────────────────────────
        var advanceBtn    = document.getElementById('ai-advance-btn');
        var advanceStatus = document.getElementById('ai-advance-status');

        if (advanceBtn) {
            advanceBtn.addEventListener('click', function () {
                var trackingId = getField('id_trackingId');
                if (!trackingId) {
                    advanceStatus.style.color = '#c00';
                    advanceStatus.textContent = '✗ Save the shipment first before advancing.';
                    return;
                }

                var currentStatus = getField('id_status');
                if (currentStatus === 'Delivered') {
                    advanceStatus.style.color = '#c00';
                    advanceStatus.textContent = '✗ Shipment is already delivered.';
                    return;
                }

                // Build current_data from all form fields
                var current_data = {
                    trackingId:      trackingId,
                    status:          currentStatus,
                    destination:     getField('id_destination'),
                    expectedDate:    getField('id_expectedDate'),
                    progressPercent: parseInt(getField('id_progressPercent')) || 15,
                    progressLabels:  getJSON('id_progressLabels'),
                    recentEvent:     getJSON('id_recentEvent'),
                    allEvents:       getJSON('id_allEvents') || [],
                    shipmentDetails: getJSON('id_shipmentDetails'),
                };

                callAPI(
                    '/api/admin/ai-advance-stage/',
                    { current_data: current_data },
                    advanceStatus,
                    function (resp) {
                        var data = resp.data;
                        populateFields(data);
                        advanceStatus.style.color = '#1a7f5a';

                        var msg = '✓ Advanced to: ' + data.status;
                        if (resp.stages_filled && resp.stages_filled > 1) {
                            msg = '✓ Caught up ' + resp.stages_filled + ' missed stages → Now: ' + data.status;
                        }
                        if (resp.message) msg = '✓ ' + resp.message + ' → ' + data.status;
                        advanceStatus.textContent = msg + '. Review then save.';
                    }
                );
            });
        }

    });

})();