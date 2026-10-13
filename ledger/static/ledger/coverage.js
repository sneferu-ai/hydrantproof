/* HydrantProof — the coverage run.
 *
 * A run is asynchronous, so this page owns the whole job story: start, poll,
 * finish, fail. Every branch says what actually happened — a refused job names
 * the run that is in the way, a failed job names its reason, and a successful
 * job reloads so the numbers on screen are the frozen ones rather than an
 * optimistic guess.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("run-coverage-form");
  if (!form || !HP) { return; }

  var status = document.getElementById("run-status");
  var button = form.querySelector("button[type=submit]");
  var pollTimer = null;
  var POLL_MS = 2000;
  var MAX_ATTEMPTS = 150;   // five minutes of watching, then the page says so

  function say(message) { if (status) { status.textContent = message; } }

  function poll(url, attempt) {
    window.fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (state) {
        if (!state) { say("Lost contact with the job status endpoint — reload to see the outcome."); return; }
        if (state.status === "COMPLETE") {
          say("Run #" + state.id + " complete — reloading.");
          window.location.reload();
          return;
        }
        if (state.status === "FAILED") {
          say("Run #" + state.id + " failed: " + (state.failure_reason || "no reason recorded")
            + (state.remediation ? " — " + state.remediation : ""));
          HP.toast("Coverage run #" + state.id + " failed: " + (state.failure_reason || "no reason recorded"), "bad",
            { sticky: true });
          if (button) { HP.setBusy(button, false); }
          return;
        }
        say("Run #" + state.id + " " + String(state.status).toLowerCase() + "…");
        if (attempt < MAX_ATTEMPTS) {
          pollTimer = window.setTimeout(function () { poll(url, attempt + 1); }, POLL_MS);
        } else {
          // The panel note promises the page names what happened; a poll that
          // silently gave up would leave "running…" on screen forever.
          say("Run #" + state.id + " is still running after "
            + Math.round(MAX_ATTEMPTS * POLL_MS / 60000)
            + " minutes of watching — this page has stopped polling. The run itself is unaffected; reload to keep watching.");
        }
      })
      .catch(function () {
        if (attempt < MAX_ATTEMPTS) { pollTimer = window.setTimeout(function () { poll(url, attempt + 1); }, POLL_MS); }
      });
  }

  /* --- Scope: the §8 parcel_filter, built from the product's own controls ---
   * `null` is the whole district; a bounding box is `{bbox: [minlon, minlat,
   * maxlon, maxlat]}` exactly as the API documents it. A box that cannot be
   * sent is refused here, in words, before any request leaves the page.
   */
  var scopeInputs = document.querySelectorAll("input[name=coverage_scope]");
  var bboxFields = document.getElementById("bbox-fields");
  var bboxIds = ["bbox_min_lon", "bbox_min_lat", "bbox_max_lon", "bbox_max_lat"];

  function scopeMode() {
    var checked = document.querySelector("input[name=coverage_scope]:checked");
    return checked ? checked.value : "all";
  }

  function paintScope() {
    var bbox = scopeMode() === "bbox";
    if (bboxFields) { bboxFields.hidden = !bbox; }
    bboxIds.forEach(function (id) {
      var input = document.getElementById(id);
      if (input) { input.disabled = !bbox; }
    });
  }
  scopeInputs.forEach(function (input) { input.addEventListener("change", paintScope); });
  paintScope();

  function parcelFilter() {
    if (scopeMode() !== "bbox") { return { filter: null }; }
    var inputs = bboxIds.map(function (id) { return document.getElementById(id); });
    var values = inputs.map(function (input) { return input ? parseFloat(input.value) : NaN; });
    if (values.some(function (value) { return isNaN(value); })) {
      return { error: "A bounding-box run needs all four coordinates — west, south, east, north.",
               fields: inputs };
    }
    var minLon = values[0], minLat = values[1], maxLon = values[2], maxLat = values[3];
    if (minLon >= maxLon || minLat >= maxLat) {
      var swapped = [];
      if (minLon >= maxLon) { swapped.push(inputs[0], inputs[2]); }
      if (minLat >= maxLat) { swapped.push(inputs[1], inputs[3]); }
      return { error: "The box is empty: west must be less than east, and south less than north.",
               fields: swapped, group: true };
    }
    if (Math.abs(minLat) > 80 || Math.abs(maxLat) > 80
        || Math.abs(minLon) > 180 || Math.abs(maxLon) > 180) {
      var outOfRange = [];
      if (Math.abs(minLon) > 180) { outOfRange.push(inputs[0]); }
      if (Math.abs(minLat) > 80) { outOfRange.push(inputs[1]); }
      if (Math.abs(maxLon) > 180) { outOfRange.push(inputs[2]); }
      if (Math.abs(maxLat) > 80) { outOfRange.push(inputs[3]); }
      return { error: "Coordinates are out of range: latitudes within \u00b180, longitudes within \u00b1180.",
               fields: outOfRange };
    }
    return { filter: { bbox: [minLon, minLat, maxLon, maxLat] } };
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (button && button.disabled) { return; }
    if (pollTimer) { window.clearTimeout(pollTimer); }

    var scope = parcelFilter();
    if (scope.error) {
      say(scope.error);
      // The corners named by a swap are one refusal group (fixing either side
      // satisfies the message); the missing/out-of-range corners are marked
      // individually.
      HP.markInvalid(scope.fields, status, scope.group === true);
      if (scope.fields.length) { scope.fields[0].focus(); }
      return;
    }

    HP.post(form.getAttribute("action"), { parcel_filter: scope.filter }, {
      button: button,
      form: form,
      statusEl: status,
      busyLabel: "Starting\u2026"
    }).then(function (body) {
      if (!body || !body.poll_url) {
        say("The job started but returned no status URL — reload the page.");
        return;
      }
      say("Run #" + body.id + " running\u2026");
      poll(body.poll_url, 0);
    }).catch(function (error) {
      say(error.message);
      HP.toast("Coverage could not start. " + error.message, "bad", { sticky: true });
    });
  });

  /* A run started in another tab or by another operator: the server renders
     the form disabled, and this page watches that run to its end rather than
     sitting silent — the note under the form promises exactly that. The poll's
     COMPLETE branch reloads (the numbers below become the frozen ones); its
     FAILED branch names the reason and re-enables the form, because a failed
     run no longer holds the single-flight gate. */
  if (form.dataset.resumePoll) { poll(form.dataset.resumePoll, 0); }
})();
