/* HydrantProof — evidence-pack generation.
 *
 * Same job story as a coverage run: start, poll, finish or fail, and say which
 * one happened. A 409 names the job already running, a 400 states the refusal
 * reason verbatim, and a completed pack reloads the page so the digests shown
 * are the stored bytes.
 */
(function () {
  "use strict";

  var HP = window.HydrantProof;
  var form = document.getElementById("pack-generate-form");
  if (!form || !HP) { return; }

  var status = document.getElementById("pack-status");
  var button = form.querySelector("button[type=submit]");
  var pollTimer = null;
  var POLL_MS = 2000;
  var MAX_ATTEMPTS = 300;   // ten minutes of watching, then the page says so

  function say(message) { if (status) { status.textContent = message; } }

  function poll(url, attempt) {
    window.fetch(url, { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (state) {
        if (!state) { say("Lost contact with the job status endpoint — reload to see the outcome."); return; }
        if (state.status === "COMPLETE") {
          say("Pack #" + state.id + " complete — reloading.");
          window.location.reload();
          return;
        }
        if (state.status === "FAILED") {
          say("Pack #" + state.id + " failed: " + (state.failure_reason || "no reason recorded"));
          HP.toast("Evidence pack #" + state.id + " failed: " + (state.failure_reason || "no reason recorded"),
            "bad", { sticky: true });
          if (button) { HP.setBusy(button, false); }
          return;
        }
        say("Pack #" + state.id + " " + String(state.status).toLowerCase() + "…");
        if (attempt < MAX_ATTEMPTS) {
          pollTimer = window.setTimeout(function () { poll(url, attempt + 1); }, POLL_MS);
        } else {
          // The panel note promises the page names what happened; a poll that
          // silently gave up would leave "rendering…" on screen forever.
          say("Pack #" + state.id + " is still rendering after "
            + Math.round(MAX_ATTEMPTS * POLL_MS / 60000)
            + " minutes of watching — this page has stopped polling. The render itself is unaffected; reload to keep watching.");
        }
      })
      .catch(function () {
        if (attempt < MAX_ATTEMPTS) { pollTimer = window.setTimeout(function () { poll(url, attempt + 1); }, POLL_MS); }
      });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (button && button.disabled) { return; }
    if (pollTimer) { window.clearTimeout(pollTimer); }

    var runId = document.getElementById("pack-run-id").value;
    var payload = { include_attachments: document.getElementById("pack-include-attachments").checked };
    if (runId) { payload.run_id = parseInt(runId, 10); }

    HP.post(form.getAttribute("action"), payload, {
      button: button,
      form: form,
      statusEl: status,
      busyLabel: "Starting\u2026"
    }).then(function (body) {
      if (!body || !body.poll_url) {
        say("The job started but returned no status URL — reload the page.");
        return;
      }
      say("Pack #" + body.id + " rendering\u2026");
      poll(body.poll_url, 0);
    }).catch(function (error) {
      say(error.message);
      HP.toast("The pack could not be generated. " + error.message, "bad", { sticky: true });
    });
  });

  /* A render started elsewhere: the page watches it to its end instead of
     sitting disabled and silent. COMPLETE reloads so the digests on screen are
     the stored bytes; FAILED names the reason and re-enables the form. */
  if (form.dataset.resumePoll) { poll(form.dataset.resumePoll, 0); }
})();
