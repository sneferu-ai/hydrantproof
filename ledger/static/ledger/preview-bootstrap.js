/* Preview bootstrap (Sneferu accepted-product launcher).
 *
 * The launcher writes exactly one versioned record at
 *   localStorage["sneferu.preview.bootstrap.v1"]
 * shaped {username, role, session_token?}. This file consumes that record on
 * startup so a preview session presents the SAME identity the normal sign-in
 * flow presents — and nothing more.
 *
 * HONEST BOUNDARY: this build authenticates with a Django session cookie
 * (ledger/auth.py — SessionAuthenticationNoBrowsable + Basic on /api/v1/).
 * There is no token-exchange route, and inventing one would be a second
 * authentication path — the exact thing the contract forbids. So the record
 * is consumed as IDENTITY, never as a credential: it pre-fills the sign-in
 * form's username, it is shown to the operator so they know why the form is
 * filled, and it is reported to the page for display. It never authenticates,
 * never bypasses a login, and normal deployed sign-in is untouched when the
 * record is absent.
 *
 * No username is hard-coded here or anywhere else in this worktree.
 */
(function () {
  "use strict";

  var KEY = "sneferu.preview.bootstrap.v1";
  var record = null;

  try {
    var raw = window.localStorage.getItem(KEY);
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && typeof parsed.username === "string" && parsed.username) {
        record = {
          username: parsed.username,
          role: typeof parsed.role === "string" ? parsed.role : "",
          session_token: typeof parsed.session_token === "string" ? parsed.session_token : ""
        };
      }
    }
  } catch (error) {
    record = null;   // malformed or storage blocked: behave exactly as if absent
  }

  window.HydrantProofPreview = record;

  if (!record) { return; }

  var EXPLAINER = " — the launcher supplied this identity. The preview record"
    + " pre-fills the sign-in form and is never an authentication bypass;"
    + " this build authenticates with a Django session.";

  function prefill() {
    var input = document.querySelector("input[name=username]");
    if (input && !input.value) {
      input.value = record.username;
      input.dispatchEvent(new Event("input", { bubbles: true }));
    }

    var who = record.username + (record.role ? " (" + record.role + ")" : "");

    // The login page's inline note.
    var note = document.getElementById("preview-note");
    if (note) {
      note.hidden = false;
      note.textContent = "Preview session for " + who + EXPLAINER;
    }

    // The signed-in shell's banner (base.html ships it hidden).
    var banner = document.getElementById("preview-banner");
    var bannerText = document.getElementById("preview-banner-text");
    if (banner && bannerText) {
      bannerText.textContent = "Preview session for " + who + EXPLAINER;
      banner.hidden = false;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", prefill);
  } else {
    prefill();
  }
})();
