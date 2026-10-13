/* HydrantProof — the service-status door on the hydrant ledger.
 *
 * The select offers only the transitions the state machine allows from the
 * current status (the server renders the option list), and the form mirrors
 * the API's own rules in words BEFORE the request is made: an out-of-service
 * entry needs the expected-return date or the indefinite flag, and a
 * reason-requiring transition (the option carries data-reason-required)
 * refuses to leave without one. The API is still the authority — a refusal
 * lands beside the submit button, field-named.
 *
 * The reversal form beside it is declarative (data-async): reason + the
 * current event's id, posted to the same route.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("status-event-form");
  if (!form || !HP) { return; }

  var select = document.getElementById("status_to");
  var oosFields = document.getElementById("status-oos-fields");
  var expectedReturn = document.getElementById("status_expected_return_on");
  var indefinite = document.getElementById("status_indefinite");
  var reason = document.getElementById("status_reason");
  var reasonReq = document.getElementById("status-reason-req");
  var reasonHint = document.getElementById("status-reason-hint");
  var targetHint = document.getElementById("status-target-hint");
  var effective = document.getElementById("status_effective_on");
  var submitButton = document.getElementById("submit-status-event");
  var statusEl = document.getElementById("status-event-status");

  var TARGET_HINTS = {
    OUT_OF_SERVICE: "Out of service needs an expected return date or the indefinite flag. Past the date the "
      + "hydrant is flagged and escalated — never auto-reverted.",
    IN_SERVICE: "Back in service: the hydrant can carry district credit again once its tests and inspections "
      + "are inside their windows.",
    RETIRED: "Retired ends the lifecycle forward — a retired hydrant is out of every readiness count and "
      + "every worksheet."
  };

  function selectedOption() {
    return select && select.options && select.selectedIndex >= 0
      ? select.options[select.selectedIndex]
      : null;
  }

  function reasonIsRequired() {
    var option = selectedOption();
    return !!(option && option.getAttribute("data-reason-required") === "true");
  }

  function paint() {
    if (!select) { return; }
    var target = select.value;
    if (oosFields) { oosFields.hidden = target !== "OUT_OF_SERVICE"; }
    if (targetHint) { targetHint.textContent = TARGET_HINTS[target] || ""; }
    var required = reasonIsRequired();
    if (reasonReq) { reasonReq.textContent = required ? "required" : "optional"; }
    if (reasonHint) {
      reasonHint.textContent = required
        ? "This transition requires a reason — it is written into the event row with your name."
        : "Written into the event row with your name. Some transitions refuse to save without one.";
    }
  }
  if (select) { select.addEventListener("change", paint); paint(); }

  function refuse(message, focusEl) {
    if (statusEl) { statusEl.textContent = message; }
    if (focusEl) { HP.markInvalid(focusEl, statusEl); }
    if (focusEl && !focusEl.disabled) { focusEl.focus(); }
  }

  /* The date and the indefinite flag are alternatives: checking the flag
     answers the refusal, so it clears the date's declared state. */
  if (indefinite) {
    indefinite.addEventListener("change", function () { HP.clearInvalid(expectedReturn); });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (submitButton && submitButton.disabled) { return; }
    if (!select) { return; }

    var target = select.value;
    var reasonText = reason && !reason.disabled ? reason.value.trim() : "";
    if (reasonIsRequired() && !reasonText) {
      refuse("This transition requires a reason — write what changed at the hydrant.", reason);
      return;
    }
    if (target === "OUT_OF_SERVICE") {
      var expectedValue = expectedReturn && !expectedReturn.disabled ? expectedReturn.value : "";
      var isIndefinite = !!(indefinite && !indefinite.disabled && indefinite.checked);
      if (!expectedValue && !isIndefinite) {
        refuse("An out-of-service transition needs an expected return date, or the indefinite flag.", expectedReturn);
        return;
      }
      if (expectedValue && isIndefinite) {
        refuse("Give the expected return date or the indefinite flag — not both.", expectedReturn);
        return;
      }
    }

    var payload = { to_status: target, reason: reasonText };
    if (target === "OUT_OF_SERVICE") {
      if (expectedReturn && !expectedReturn.disabled && expectedReturn.value) {
        payload.expected_return_on = expectedReturn.value;
      }
      payload.indefinite = !!(indefinite && !indefinite.disabled && indefinite.checked);
    }
    if (effective && !effective.disabled && effective.value) {
      payload.effective_on = effective.value;
    }

    HP.post(form.getAttribute("action"), payload, {
      button: submitButton,
      form: form,
      statusEl: statusEl,
      busyLabel: "Recording…"
    }).then(function () {
      if (statusEl) { statusEl.textContent = "Status event recorded — reloading the ledger."; }
      window.location.reload();
    }).catch(function (error) {
      HP.toast("The status event was not recorded. " + error.message, "bad", { sticky: true });
      if (statusEl) { statusEl.textContent = error.message; }
    });
  });
})();
