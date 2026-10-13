/* HydrantProof — the inspection sheet.
 *
 * One decision (the result) changes what the rest of the form means, so the
 * consequence is written out beside the control rather than left to be
 * discovered after submitting. The record POSTs to the real route; photos ride
 * it as attachments and a failed upload is stated, never hidden.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("inspection-form");
  if (!form || !HP) { return; }

  var statusEl = document.getElementById("submit-status");
  var actions = document.getElementById("form-actions");
  var successPanel = document.getElementById("success-panel");
  var submitButton = document.getElementById("submit-inspection");
  var photoInput = document.getElementById("photos");
  var photoList = document.getElementById("photo-list");
  var explainer = document.getElementById("result-explainer");
  var codesExplainer = document.getElementById("codes-explainer");
  var codeInputs = Array.prototype.slice.call(form.querySelectorAll('input[name="deficiency_codes"]'));

  var EXPLAIN = {
    OK: "Closes every open deficiency row on this hydrant in the same transaction, and starts the inspection "
      + "window over. Codes are ignored.",
    DEFICIENCY: "Opens one non-gating deficiency row per ticked code. The hydrant can still carry district "
      + "credit; the rows are tracked work.",
    FAIL: "Opens one gating deficiency row per ticked code. Until those rows are resolved — or a later OK "
      + "inspection closes them — this hydrant cannot carry district credit."
  };

  function currentResult() {
    var checked = form.querySelector('input[name="result"]:checked');
    return checked ? checked.value : "OK";
  }

  function paintResult() {
    var result = currentResult();
    if (explainer) { explainer.textContent = EXPLAIN[result] || ""; }
    var needsCodes = result !== "OK";
    codeInputs.forEach(function (input) {
      input.disabled = !needsCodes || !!(submitButton && submitButton.disabled);
    });
    // The refusal's other exit: "or record the result as OK". Choosing it
    // answers the refusal, so the picker's marks end here — not at submit.
    if (!needsCodes) { codeInputs.forEach(function (input) { HP.clearInvalid(input); }); }
    if (codesExplainer) {
      codesExplainer.textContent = needsCodes
        ? "Tick every code that applies — one deficiency row is opened per ticked code."
        : "Codes are only read when the result is DEFICIENCY or FAIL. Nothing will be opened for an OK.";
    }
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "result") { paintResult(); }
  });

  function renderPhotos() {
    if (!photoList || !photoInput) { return; }
    photoList.textContent = "";
    var files = Array.prototype.slice.call(photoInput.files || []);
    if (!files.length) {
      var none = document.createElement("span");
      none.className = "small";
      none.textContent = "No photos attached.";
      photoList.appendChild(none);
      return;
    }
    files.slice(0, 4).forEach(function (file) {
      var line = document.createElement("span");
      line.className = "small";
      line.textContent = file.name + " \u00B7 " + Math.round(file.size / 1024) + " KB";
      photoList.appendChild(line);
    });
    if (files.length > 4) {
      var extra = document.createElement("span");
      extra.className = "small";
      extra.textContent = "Only the first 4 photos are attached \u2014 the record holds at most 4.";
      photoList.appendChild(extra);
    }
  }
  if (photoInput) { photoInput.addEventListener("change", renderPhotos); renderPhotos(); }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (submitButton && submitButton.disabled) { return; }

    var result = currentResult();
    var codes = codeInputs.filter(function (input) { return input.checked; }).map(function (input) { return input.value; });
    if (result !== "OK" && !codes.length) {
      if (statusEl) {
        statusEl.textContent = "Tick at least one deficiency code, or record the result as OK.";
      }
      var first = codeInputs[0];
      // The refusal is on the whole picker ("at least one"), so every box
      // declares the state as one group — ticking ANY code clears every mark —
      // and focus lands on the first box, where the message is described from.
      HP.markInvalid(codeInputs, statusEl, true);
      if (first) { first.focus(); }
      return;
    }

    var payload = {
      inspected_on: document.getElementById("inspected_on").value,
      result: result,
      deficiency_codes: result === "OK" ? [] : codes,
      inspector_names: document.getElementById("inspector_names").value,
      notes: document.getElementById("notes").value
    };
    var files = Array.prototype.slice.call(photoInput ? photoInput.files : []).slice(0, 4);

    HP.post(form.getAttribute("action"), payload, {
      button: submitButton,
      form: form,
      statusEl: statusEl,
      busyLabel: "Recording\u2026"
    }).then(function (inspection) {
      var url = "/api/v1/hydrants/" + form.dataset.hydrantId + "/inspections/" + inspection.id + "/attachments/";
      return HP.uploadPhotos(url, files).then(function (photoResult) { done(inspection, photoResult); });
    }).catch(function (error) {
      HP.toast("The inspection was not recorded. " + error.message, "bad", { sticky: true });
      if (statusEl) { statusEl.textContent = error.message; }
    });
  });

  function done(inspection, photoResult) {
    if (actions) { actions.hidden = true; }
    if (!successPanel) { return; }
    successPanel.hidden = false;
    successPanel.textContent = "";

    var box = document.createElement("div");
    box.className = "banner " + (inspection.result === "OK" ? "banner--ok" : "banner--stale");
    var body = document.createElement("div");
    body.className = "banner__body";

    var strong = document.createElement("strong");
    strong.textContent = "Inspection #" + inspection.id + " recorded as " + inspection.result + ".";
    body.appendChild(strong);

    var detail = document.createElement("p");
    if (inspection.result === "OK") {
      detail.textContent = "Every open deficiency row on this hydrant was closed in the same transaction.";
    } else {
      detail.textContent = (inspection.deficiency_codes || []).length
        + " deficiency row(s) opened for " + (inspection.deficiency_codes || []).join(", ")
        + (inspection.result === "FAIL" ? " — gating district credit until resolved." : " — non-gating.");
    }
    body.appendChild(detail);

    if (photoResult && photoResult.failed.length) {
      var warn = document.createElement("p");
      warn.className = "small";
      warn.textContent = "The inspection is stored, but " + photoResult.failed.length + " photo(s) did not attach: "
        + photoResult.failed.join(", ") + ".";
      body.appendChild(warn);
    } else if (photoResult && photoResult.uploaded) {
      var ok = document.createElement("p");
      ok.className = "small";
      ok.textContent = photoResult.uploaded + " photo(s) attached.";
      body.appendChild(ok);
    }

    var links = document.createElement("p");
    links.className = "btn-row";
    var open = document.createElement("a");
    open.className = "btn btn--sm";
    open.href = "/hydrants/" + form.dataset.hydrantId + "/";
    open.textContent = "Open the hydrant ledger";
    links.appendChild(open);
    var again = document.createElement("a");
    again.className = "btn btn--secondary btn--sm";
    again.href = window.location.pathname;
    again.textContent = "Record another inspection";
    links.appendChild(again);
    body.appendChild(links);

    box.appendChild(body);
    successPanel.appendChild(box);
    successPanel.scrollIntoView({ behavior: "smooth", block: "center" });

    HP.toast("Inspection recorded as " + inspection.result + ".", inspection.result === "OK" ? "ok" : "warn");
    Array.prototype.slice.call(form.querySelectorAll("input, select, textarea, button")).forEach(function (el) {
      el.disabled = true;
    });
  }

  paintResult();
})();
