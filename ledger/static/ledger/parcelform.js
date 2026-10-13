/* HydrantProof — the parcel form.
 *
 * One screen edits what the district owns on a parcel: the address, the
 * structure, the protected flag, the centroid correction, and the structure
 * point override. Everything leaves as one PATCH to the parcel's real API
 * route; the structure coordinates go together or not at all, because that is
 * the rule the API enforces and the form mirrors it in words before the
 * request is made.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("parcel-form");
  if (!form || !HP) { return; }

  var submitButton = document.getElementById("submit-parcel");
  var statusEl = document.getElementById("submit-status");
  var successPanel = document.getElementById("success-panel");
  var noOverride = document.getElementById("no_override");
  var structureLat = document.getElementById("structure_lat");
  var structureLon = document.getElementById("structure_lon");
  var mapEl = document.getElementById("pin-map");
  var readOnly = !!(submitButton && submitButton.disabled);

  function paintOverride() {
    if (!noOverride) { return; }
    var suppressed = noOverride.checked || readOnly;
    structureLat.disabled = suppressed;
    structureLon.disabled = suppressed;
    if (noOverride.checked) {
      structureLat.value = "";
      structureLon.value = "";
      if (mapEl && mapEl._pinmap) { mapEl._pinmap.clear(); }
    }
  }
  if (noOverride) { noOverride.addEventListener("change", paintOverride); paintOverride(); }

  function fieldValue(id) {
    var el = document.getElementById(id);
    if (!el || el.disabled) { return undefined; }
    var value = el.value.trim();
    if (value === "") { return undefined; }
    return value;
  }

  function refuse(message, focusId) {
    if (statusEl) { statusEl.textContent = message; }
    var el = document.getElementById(focusId);
    if (el) { HP.markInvalid(el, statusEl); }
    if (el && !el.disabled) { el.focus(); }
  }

  function coordinate(id, label, minimum, maximum) {
    var raw = fieldValue(id);
    if (raw === undefined) { return { error: label + " is required." }; }
    var value = Number(raw);
    if (isNaN(value)) { return { error: label + " must be a number." }; }
    if (value < minimum || value > maximum) {
      return { error: label + " must be between " + minimum + " and " + maximum + "." };
    }
    return { value: value };
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (submitButton && submitButton.disabled) { return; }

    var lat = coordinate("lat", "The centroid latitude", -80, 80);
    if (lat.error) { refuse(lat.error, "lat"); return; }
    var lon = coordinate("lon", "The centroid longitude", -180, 180);
    if (lon.error) { refuse(lon.error, "lon"); return; }

    var payload = {
      address: fieldValue("address") || "",
      structure_type: fieldValue("structure_type") || "",
      is_protected_structure: !!document.getElementById("is_protected_structure").checked,
      lat: lat.value,
      lon: lon.value
    };

    if (noOverride && noOverride.checked) {
      payload.structure_lat = null;
      payload.structure_lon = null;
    } else {
      var sLat = coordinate("structure_lat", "The structure latitude", -80, 80);
      if (sLat.error) { refuse(sLat.error + " Tick the no-override box to measure from the centroid.", "structure_lat"); return; }
      var sLon = coordinate("structure_lon", "The structure longitude", -180, 180);
      if (sLon.error) { refuse(sLon.error + " Tick the no-override box to measure from the centroid.", "structure_lon"); return; }
      payload.structure_lat = sLat.value;
      payload.structure_lon = sLon.value;
    }

    HP.patch(form.getAttribute("action"), payload, {
      button: submitButton,
      form: form,
      statusEl: statusEl,
      busyLabel: "Saving\u2026"
    }).then(function (parcel) {
      HP.toast("Parcel " + (parcel.parcel_id || "") + " saved.", "ok");
      if (!successPanel) { return; }
      successPanel.hidden = false;
      successPanel.textContent = "";
      var box = document.createElement("div");
      box.className = "banner banner--ok";
      var body = document.createElement("div");
      body.className = "banner__body";
      var strong = document.createElement("strong");
      strong.textContent = "Parcel saved.";
      body.appendChild(strong);
      var detail = document.createElement("p");
      detail.textContent = payload.structure_lat === null
        ? "The override is cleared; coverage measures this parcel from the assessor centroid. Every changed "
          + "field was written to the parcel attribute history."
        : "Every changed field was written to the parcel attribute history with its old value.";
      body.appendChild(detail);
      var note = document.createElement("p");
      note.className = "small";
      note.textContent = "The worksheet still shows the previous frozen run — recompute coverage for the new "
        + "point to take effect.";
      body.appendChild(note);
      var links = document.createElement("p");
      links.className = "btn-row";
      var back = document.createElement("a");
      back.className = "btn btn--sm";
      back.href = "/parcels/" + form.dataset.parcelId + "/";
      back.textContent = "Back to the parcel";
      links.appendChild(back);
      var recompute = document.createElement("a");
      recompute.className = "btn btn--secondary btn--sm";
      recompute.href = "/coverage/";
      recompute.textContent = "Recompute coverage";
      links.appendChild(recompute);
      body.appendChild(links);
      box.appendChild(body);
      successPanel.appendChild(box);
      successPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    }).catch(function (error) {
      HP.toast("The parcel was not saved. " + error.message, "bad", { sticky: true });
      if (statusEl) { statusEl.textContent = error.message; }
    });
  });
})();
