/* HydrantProof — the hydrant form.
 *
 * Create is a POST, edit is a PATCH (asset_tag is immutable and the API
 * refuses it in the body, so the field is disabled and never serialized).
 * The initial-status block only exists on create, and it mirrors the API's
 * own validation rule in words before the request is made.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("hydrant-form");
  if (!form || !HP) { return; }

  var status = document.getElementById("initial_status");
  var oosFields = document.getElementById("oos-fields");
  var retiredField = document.getElementById("retired-field");
  var expectedReturn = document.getElementById("expected_return_on");
  var indefinite = document.getElementById("indefinite");
  var statusHint = document.getElementById("status-hint");
  var submitButton = document.getElementById("submit-hydrant");
  var statusEl = document.getElementById("submit-status");
  var successPanel = document.getElementById("success-panel");
  var mode = form.dataset.mode;

  function paintStatus() {
    if (!status) { return; }
    var value = status.value;
    if (oosFields) { oosFields.hidden = value !== "OUT_OF_SERVICE"; }
    if (retiredField) { retiredField.hidden = value !== "RETIRED"; }
    if (statusHint) {
      statusHint.textContent = value === "OUT_OF_SERVICE"
        ? "An expected return date or an explicit indefinite flag is required. An expired date is flagged, "
          + "escalated and acknowledged — it is never auto-reverted."
        : value === "RETIRED"
          ? "A reason is required for a retired hydrant; retirement is a lifecycle event with its own audit row."
          : "The hydrant starts in service, with an attributed initial status event written in the same transaction.";
    }
  }
  if (status) { status.addEventListener("change", paintStatus); paintStatus(); }

  /* The return date and the indefinite flag are alternatives: checking the
     flag answers the refusal, so it clears the date's declared state. */
  if (indefinite) {
    indefinite.addEventListener("change", function () { HP.clearInvalid(expectedReturn); });
  }

  function fieldValue(id) {
    var el = document.getElementById(id);
    if (!el || el.disabled) { return undefined; }
    var value = el.value.trim();
    if (value === "") { return undefined; }
    return value;
  }

  function buildCreatePayload() {
    var payload = {
      asset_tag: fieldValue("asset_tag"),
      lat: Number(document.getElementById("lat").value),
      lon: Number(document.getElementById("lon").value),
      location_description: fieldValue("location_description") || "",
      manufacturer: fieldValue("manufacturer") || "",
      model: fieldValue("model") || "",
      ownership: fieldValue("ownership") || "",
      source_org: fieldValue("source_org") || "",
      notes: fieldValue("notes") || "",
      outlet_diameter_in: fieldValue("outlet_diameter_in") || "2.50",
      discharge_coefficient: fieldValue("discharge_coefficient") || "0.90"
    };
    var year = fieldValue("year_installed");
    if (year !== undefined) { payload.year_installed = Number(year); }
    var main = fieldValue("main_size_inches");
    if (main !== undefined) { payload.main_size_inches = main; }

    payload.initial_status = status ? status.value : "IN_SERVICE";
    if (payload.initial_status === "OUT_OF_SERVICE") {
      var expected = fieldValue("expected_return_on");
      if (expected !== undefined) { payload.expected_return_on = expected; }
      payload.indefinite = !!(indefinite && indefinite.checked);
      var effective = fieldValue("effective_on");
      if (effective !== undefined) { payload.effective_on = effective; }
    }
    if (payload.initial_status === "RETIRED") {
      payload.initial_status_reason = fieldValue("initial_status_reason") || "";
    }
    return payload;
  }

  function buildEditPayload() {
    var payload = {};
    ["location_description", "manufacturer", "model", "ownership", "source_org", "notes"].forEach(function (id) {
      var el = document.getElementById(id);
      if (!el || el.disabled) { return; }
      payload[id] = el.value;
    });
    var lat = fieldValue("lat");
    var lon = fieldValue("lon");
    if (lat !== undefined) { payload.lat = Number(lat); }
    if (lon !== undefined) { payload.lon = Number(lon); }
    var year = fieldValue("year_installed");
    payload.year_installed = year === undefined ? null : Number(year);
    var main = fieldValue("main_size_inches");
    payload.main_size_inches = main === undefined ? null : main;
    var outlet = fieldValue("outlet_diameter_in");
    if (outlet !== undefined) { payload.outlet_diameter_in = outlet; }
    var coefficient = fieldValue("discharge_coefficient");
    if (coefficient !== undefined) { payload.discharge_coefficient = coefficient; }
    return payload;
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (submitButton && submitButton.disabled) { return; }

    var lat = fieldValue("lat");
    var lon = fieldValue("lon");
    if (lat === undefined || lon === undefined) {
      if (statusEl) { statusEl.textContent = "A hydrant needs coordinates — click the map or type them."; }
      HP.markInvalid([document.getElementById("lat"), document.getElementById("lon")], statusEl);
      document.getElementById("lat").focus();
      return;
    }
    if (mode === "create" && !fieldValue("asset_tag")) {
      if (statusEl) { statusEl.textContent = "The asset tag is required."; }
      HP.markInvalid(document.getElementById("asset_tag"), statusEl);
      document.getElementById("asset_tag").focus();
      return;
    }
    if (mode === "create" && status && status.value === "OUT_OF_SERVICE"
        && fieldValue("expected_return_on") === undefined && !(indefinite && indefinite.checked)) {
      if (statusEl) {
        statusEl.textContent = "An out-of-service hydrant needs an expected return date, or the indefinite flag.";
      }
      if (expectedReturn) { HP.markInvalid(expectedReturn, statusEl); expectedReturn.focus(); }
      return;
    }

    var payload = mode === "create" ? buildCreatePayload() : buildEditPayload();
    var url = form.getAttribute("action");

    var request = mode === "create"
      ? HP.post(url, payload, { button: submitButton, form: form, statusEl: statusEl, busyLabel: "Adding\u2026" })
      : HP.patch(url, payload, { button: submitButton, form: form, statusEl: statusEl, busyLabel: "Saving\u2026" });

    request.then(function (hydrant) {
      var id = hydrant && hydrant.id ? hydrant.id : (payload.id || null);
      HP.toast(mode === "create"
        ? "Hydrant added — " + (payload.asset_tag || "") + "."
        : "Attributes saved.", "ok");
      if (successPanel) {
        successPanel.hidden = false;
        successPanel.textContent = "";
        var box = document.createElement("div");
        box.className = "banner banner--ok";
        var body = document.createElement("div");
        body.className = "banner__body";
        var strong = document.createElement("strong");
        strong.textContent = mode === "create" ? "Hydrant created." : "Attributes saved.";
        body.appendChild(strong);
        var detail = document.createElement("p");
        detail.textContent = mode === "create"
          ? "The row and its attributed initial status event were written in one transaction."
          : "Every changed field was written to the attribute history with its old value and the reason manual.";
        body.appendChild(detail);
        if (id) {
          var links = document.createElement("p");
          links.className = "btn-row";
          var open = document.createElement("a");
          open.className = "btn btn--sm";
          open.href = "/hydrants/" + id + "/";
          open.textContent = "Open the hydrant ledger";
          links.appendChild(open);
          var test = document.createElement("a");
          test.className = "btn btn--secondary btn--sm";
          test.href = "/hydrants/" + id + "/tests/new/";
          test.textContent = "Record its first flow test";
          links.appendChild(test);
          body.appendChild(links);
        }
        box.appendChild(body);
        successPanel.appendChild(box);
        successPanel.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      if (mode === "create") {
        Array.prototype.slice.call(form.querySelectorAll("input, select, textarea, button")).forEach(function (el) {
          el.disabled = true;
        });
      }
    }).catch(function (error) {
      HP.toast((mode === "create" ? "The hydrant was not created. " : "The attributes were not saved. ") + error.message,
        "bad", { sticky: true });
      if (statusEl) { statusEl.textContent = error.message; }
    });
  });
})();
