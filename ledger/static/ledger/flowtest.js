/* HydrantProof — the flow-test sheet.
 *
 * The sheet is a grid, not a form dump: gauge readings, then one row per
 * outlet, and a result card that recomputes against the product's own
 * `POST /api/v1/compute-preview/` route (never a JavaScript re-implementation
 * of the hydraulics — the preview and the stored test must agree by
 * construction, not by coincidence).
 *
 * States the result card can be in, all rendered: idle, loading (skeleton on
 * the first call, a quiet busy state afterwards), error with a retry, and the
 * computed result. Submitting is a real POST to the real route; a failed photo
 * upload never hides the fact that the test itself was recorded.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("test-form");
  if (!form || !HP) { return; }

  var grid = document.getElementById("outlet-grid");
  var readout = document.getElementById("readout");
  var statusEl = document.getElementById("submit-status");
  var actions = document.getElementById("form-actions");
  var successPanel = document.getElementById("success-panel");
  var addButton = document.getElementById("add-outlet");
  var countEl = document.getElementById("outlet-count");
  var photoInput = document.getElementById("photos");
  var photoList = document.getElementById("photo-list");
  var submitButton = document.getElementById("submit-test");
  var MAX_OUTLETS = parseInt(form.dataset.maxOutlets || "4", 10);

  var lastPreview = null;
  var timer = null;
  var inFlight = false;
  var seq = 0;

  /* --- Outlet rows ------------------------------------------------------- */
  function rows() {
    return Array.prototype.slice.call(grid.querySelectorAll("[data-row]"));
  }

  function renumber() {
    var all = rows();
    all.forEach(function (row, index) {
      row.querySelector("[data-outlet-number]").textContent = String(index + 1);
      row.querySelectorAll("[data-outlet]").forEach(function (input) {
        var key = input.getAttribute("data-outlet");
        input.id = key + "-" + index;
        var field = input.closest(".field");
        var label = field ? field.querySelector("label") : null;
        if (label) { label.setAttribute("for", input.id); }
      });
      var remove = row.querySelector("[data-remove-outlet]");
      if (remove) { remove.hidden = all.length === 1; }
    });
    if (countEl) {
      countEl.textContent = all.length + " of " + MAX_OUTLETS + " outlet rows";
    }
    if (addButton) { addButton.disabled = all.length >= MAX_OUTLETS; }
  }

  function addRow() {
    var all = rows();
    if (all.length >= MAX_OUTLETS) { return; }
    var row = all[0].cloneNode(true);
    row.querySelectorAll("input").forEach(function (input) {
      var key = input.getAttribute("data-outlet");
      if (key === "diameter") { input.value = all[0].querySelector('[data-outlet="diameter"]').value; }
      if (key === "coefficient") { input.value = all[0].querySelector('[data-outlet="coefficient"]').value; }
      if (key === "pitot") { input.value = ""; }
      // A cloned row is a fresh answer: none of the source row's refusal
      // bookkeeping may survive onto it (aria-describedby would otherwise keep
      // naming an old message, and a stale group id would let this row clear
      // marks it never earned).
      input.removeAttribute("aria-invalid");
      input.removeAttribute("aria-describedby");
      delete input.dataset.hpDescribedby;
      delete input.dataset.hpErrorGroup;
    });
    var computed = row.querySelector("[data-computed]");
    if (computed) { computed.textContent = "\u2014"; }
    grid.appendChild(row);
    renumber();
    var pitot = row.querySelector('[data-outlet="pitot"]');
    if (pitot) { pitot.focus(); }
  }

  grid.addEventListener("click", function (event) {
    var remove = event.target.closest("[data-remove-outlet]");
    if (!remove) { return; }
    var row = remove.closest("[data-row]");
    if (row && rows().length > 1) { row.remove(); renumber(); schedule(); }
  });
  if (addButton) { addButton.addEventListener("click", function () { addRow(); schedule(); }); }

  /* --- Photo list -------------------------------------------------------- */
  function renderPhotos() {
    if (!photoList || !photoInput) { return; }
    photoList.textContent = "";
    var files = Array.prototype.slice.call(photoInput.files || []).slice(0, 4);
    if (!files.length) {
      var none = document.createElement("span");
      none.className = "small";
      none.textContent = "No photos attached.";
      photoList.appendChild(none);
      return;
    }
    files.forEach(function (file) {
      var line = document.createElement("span");
      line.className = "small";
      line.textContent = file.name + " \u00B7 " + Math.round(file.size / 1024) + " KB";
      photoList.appendChild(line);
    });
    if ((photoInput.files || []).length > 4) {
      var extra = document.createElement("span");
      extra.className = "small";
      extra.textContent = "Only the first 4 photos are attached \u2014 the record holds at most 4.";
      photoList.appendChild(extra);
    }
  }
  if (photoInput) { photoInput.addEventListener("change", renderPhotos); renderPhotos(); }

  /* --- Collect ----------------------------------------------------------- */
  function collect() {
    var staticPsi = document.getElementById("static_psi").value.trim();
    var residual = document.getElementById("residual_psi").value.trim();
    var outlets = [];
    rows().forEach(function (row) {
      var pitot = row.querySelector('[data-outlet="pitot"]').value.trim();
      if (pitot === "") { return; }
      outlets.push({
        outlet_diameter_in: row.querySelector('[data-outlet="diameter"]').value || "2.50",
        outlet_coefficient: row.querySelector('[data-outlet="coefficient"]').value || "0.90",
        pitot_psi: pitot
      });
    });
    var meter = document.getElementById("measured_total_gpm").value.trim();
    return {
      tested_on: document.getElementById("tested_on").value,
      static_psi: staticPsi,
      residual_psi: residual,
      measured_total_gpm: meter === "" ? null : meter,
      outlets: outlets,
      crew_names: document.getElementById("crew_names").value,
      notes: ""
    };
  }

  function readyToPreview(payload) {
    if (payload.static_psi === "" || payload.residual_psi === "") { return false; }
    return payload.outlets.length > 0 || payload.measured_total_gpm !== null;
  }

  /* --- Result card ------------------------------------------------------- */
  function line(label, value, unit) {
    var wrap = document.createElement("div");
    wrap.className = "readout__row";
    var k = document.createElement("span");
    k.className = "readout__label";
    k.textContent = label;
    var v = document.createElement("span");
    v.className = "num";
    v.textContent = value;
    if (unit) {
      var u = document.createElement("span");
      u.className = "num__unit";
      u.textContent = unit;
      v.appendChild(u);
    }
    wrap.appendChild(k);
    wrap.appendChild(v);
    return wrap;
  }

  function renderIdle() {
    readout.textContent = "";
    readout.className = "panel__body readout readout--empty";
    setPanelSkeleton(false);
    var title = document.createElement("p");
    title.className = "notice__title";
    title.textContent = "Waiting for readings.";
    var next = document.createElement("p");
    next.className = "small";
    next.textContent = "Enter the static and residual pressures and at least one pitot reading; the discharge, "
      + "the rated flow and the class appear here as you type.";
    readout.appendChild(title);
    readout.appendChild(next);
  }

  /* The panel's own loading state (DESIGN.md § 6): while the preview waits the
     card carries .panel--skeleton, so it holds its height and the result does
     not shift the sheet when it lands. */
  function setPanelSkeleton(on) {
    var card = readout.closest ? readout.closest(".panel") : null;
    if (card) { card.classList.toggle("panel--skeleton", on); }
  }

  function renderSkeleton() {
    readout.textContent = "";
    readout.className = "panel__body readout";
    setPanelSkeleton(true);
    ["skeleton--title", "skeleton--text", "skeleton--text skeleton--short"].forEach(function (cls) {
      var bar = document.createElement("span");
      bar.className = "skeleton " + cls;
      readout.appendChild(bar);
    });
  }

  function renderError(message, retry) {
    readout.textContent = "";
    readout.className = "panel__body readout";
    setPanelSkeleton(false);
    var box = document.createElement("div");
    box.className = "banner banner--stale";
    var body = document.createElement("div");
    body.className = "banner__body";
    var strong = document.createElement("strong");
    strong.textContent = "The preview could not be computed.";
    var detail = document.createElement("p");
    detail.className = "small";
    detail.textContent = message;
    body.appendChild(strong);
    body.appendChild(detail);
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn--secondary btn--sm";
    button.textContent = "Try again";
    button.addEventListener("click", retry);
    body.appendChild(button);
    box.appendChild(body);
    readout.appendChild(box);
  }

  function renderResult(payload) {
    readout.textContent = "";
    readout.className = "panel__body readout";
    setPanelSkeleton(false);

    var head = document.createElement("div");
    head.className = "readout__row";
    var bigWrap = document.createElement("div");
    var label = document.createElement("span");
    label.className = "readout__label";
    label.textContent = "Rated at 20 psi residual";
    var big = document.createElement("div");
    big.className = "readout__big";
    // The server's own convention (templatetags/ui.py::gpm): an excluded or
    // un-ratable number is an em dash, never a zero — "0 gpm" would be a lie
    // about a hydrant that simply cannot be rated from these readings.
    if (payload.rated_gpm_20 === null || payload.rated_gpm_20 === undefined) {
      big.textContent = "\u2014";
    } else {
      big.textContent = Number(payload.rated_gpm_20).toLocaleString(undefined, { maximumFractionDigits: 1 });
      var unit = document.createElement("span");
      unit.className = "readout__unit";
      unit.textContent = "gpm";
      big.appendChild(unit);
    }
    bigWrap.appendChild(label);
    bigWrap.appendChild(big);
    head.appendChild(bigWrap);

    var mark = document.createElement("span");
    mark.className = "class-mark class-mark--" + String(payload.capacity_class || "none").toLowerCase();
    mark.textContent = payload.capacity_class || "\u2014";
    mark.setAttribute("aria-label", "Capacity class " + (payload.capacity_class || "unclassified"));
    head.appendChild(mark);
    readout.appendChild(head);

    readout.appendChild(line("Total discharge Q_F", Number(payload.computed_total_gpm).toLocaleString(undefined, { maximumFractionDigits: 1 }), "gpm"));
    // The pitot sum is only a separate number when a meter total also exists
    // (it is the comparison for the meter reading); without a meter there is
    // nothing to compare and the row would be Q_F printed twice.
    if (payload.pitot_sum_gpm !== null && payload.pitot_sum_gpm !== undefined) {
      readout.appendChild(line("Pitot sum", Number(payload.pitot_sum_gpm).toLocaleString(undefined, { maximumFractionDigits: 1 }), "gpm"));
    }
    readout.appendChild(line("Extrapolation factor",
      payload.extrapolation_factor === null || payload.extrapolation_factor === undefined
        ? "\u2014" : String(Number(payload.extrapolation_factor).toFixed(3)),
      payload.extrapolation_factor === null || payload.extrapolation_factor === undefined ? "" : "\u00D7"));
    readout.appendChild(line("Formula", payload.formula_version, ""));

    // Per-outlet discharge lands in the grid's own computed column.
    var cells = rows().map(function (row) { return row.querySelector("[data-computed]"); });
    (payload.outlet_gpm || []).forEach(function (value, index) {
      if (!cells[index]) { return; }
      cells[index].textContent = Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 }) + " gpm";
    });

    var warnings = payload.warnings || [];
    if (warnings.length) {
      var warnBox = document.createElement("div");
      warnBox.className = "flag-list";
      warnings.forEach(function (warning) {
        var chip = document.createElement("span");
        chip.className = "flag flag--warn";
        chip.textContent = String(warning).replace(/_/g, " ").toLowerCase();
        warnBox.appendChild(chip);
      });
      readout.appendChild(warnBox);
    } else {
      var quiet = document.createElement("p");
      quiet.className = "small";
      quiet.textContent = "No warnings on these readings.";
      readout.appendChild(quiet);
    }
  }

  /* --- Preview loop ------------------------------------------------------ */
  function preview() {
    var payload = collect();
    if (!readyToPreview(payload)) { renderIdle(); return; }
    var ticket = ++seq;
    if (lastPreview === null) { renderSkeleton(); }
    inFlight = true;
    readout.setAttribute("aria-busy", "true");

    HP.post("/api/v1/compute-preview/", payload)
      .then(function (body) {
        if (ticket !== seq) { return; }
        lastPreview = body;
        renderResult(body);
      })
      .catch(function (error) {
        if (ticket !== seq) { return; }
        lastPreview = null;
        renderError(error.message, function () { preview(); });
      })
      .then(function () {
        inFlight = false;
        readout.removeAttribute("aria-busy");
      });
  }

  function schedule() {
    if (timer) { window.clearTimeout(timer); }
    timer = window.setTimeout(preview, 350);
  }

  form.addEventListener("input", function (event) {
    if (event.target.closest("#outlet-grid") || event.target.closest(".panel")) { schedule(); }
  });
  form.addEventListener("change", function (event) {
    if (event.target.closest("#outlet-grid")) { schedule(); }
  });

  /* --- Submit ------------------------------------------------------------ */
  function success(test, photoResult) {
    if (actions) { actions.hidden = true; }
    if (successPanel) {
      successPanel.hidden = false;
      successPanel.textContent = "";
      var box = document.createElement("div");
      box.className = "banner banner--ok";
      var body = document.createElement("div");
      body.className = "banner__body";
      var strong = document.createElement("strong");
      strong.textContent = "Recorded on test #" + test.id + ".";
      var detail = document.createElement("p");
      detail.textContent = "Q_F " + Number(test.computed_total_gpm).toLocaleString(undefined, { maximumFractionDigits: 1 })
        + " gpm · Q_R " + Number(test.rated_gpm_20).toLocaleString(undefined, { maximumFractionDigits: 1 })
        + " gpm · class " + (test.capacity_class || "\u2014") + " · formula " + test.formula_version + ".";
      body.appendChild(strong);
      body.appendChild(detail);
      if (photoResult && photoResult.failed.length) {
        var warn = document.createElement("p");
        warn.className = "small";
        warn.textContent = "The test is stored, but " + photoResult.failed.length + " photo(s) did not attach: "
          + photoResult.failed.join(", ") + ". Add them from the hydrant ledger.";
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
      again.textContent = "Record another test";
      links.appendChild(again);
      body.appendChild(links);
      box.appendChild(body);
      successPanel.appendChild(box);
      successPanel.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    HP.toast("Flow test recorded — Q_R " + Number(test.rated_gpm_20).toFixed(1) + " gpm, class "
      + (test.capacity_class || "\u2014") + ".", "ok");
    Array.prototype.slice.call(form.querySelectorAll("input, select, button")).forEach(function (el) {
      el.disabled = true;
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (submitButton && submitButton.disabled) { return; }

    var payload = collect();
    if (payload.static_psi === "" || payload.residual_psi === "") {
      if (statusEl) { statusEl.textContent = "Static and residual pressures are both required."; }
      HP.markInvalid([document.getElementById("static_psi"), document.getElementById("residual_psi")], statusEl);
      document.getElementById("static_psi").focus();
      return;
    }
    if (!payload.outlets.length && payload.measured_total_gpm === null) {
      if (statusEl) { statusEl.textContent = "Enter at least one pitot reading, or a meter total."; }
      // Either answer satisfies the message, so the pair is one refusal group:
      // filling the meter clears the pitot's mark too.
      HP.markInvalid([form.querySelector('[data-outlet="pitot"]'), document.getElementById("measured_total_gpm")],
        statusEl, true);
      var firstPitot = form.querySelector('[data-outlet="pitot"]');
      if (firstPitot) { firstPitot.focus(); }
      return;
    }

    var files = Array.prototype.slice.call(photoInput ? photoInput.files : []).slice(0, 4);
    HP.post(form.getAttribute("action"), payload, {
      button: submitButton,
      form: form,
      statusEl: statusEl,
      busyLabel: "Recording\u2026"
    }).then(function (test) {
      var url = "/api/v1/hydrants/" + form.dataset.hydrantId + "/tests/" + test.id + "/attachments/";
      return HP.uploadPhotos(url, files).then(function (result) { success(test, result); });
    }).catch(function (error) {
      HP.toast("The test was not recorded. " + error.message, "bad", { sticky: true });
      if (statusEl) { statusEl.textContent = error.message; }
    });
  });

  renumber();
  renderIdle();
})();
