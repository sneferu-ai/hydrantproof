/* HydrantProof /field/ — online capture, IndexedDB queue, GPS + accuracy,
   photo attachment, and the queued-readings CSV export.
   A queued test never 403s on a stale CSRF token: sync re-fetches /field/
   first (network-first) and, on a 302-to-login, renders the inline re-login
   form; after login the page holds a fresh CSRF token and syncs. */
(function () {
  "use strict";

  // The shared helpers (app.js, always loaded by base.html before this file).
  // Until round 4 this binding was missing entirely, so the refusal paths
  // threw ReferenceError AFTER the message was said — the door's marks never
  // landed. The walk now exercises the door, so the class is pinned.
  var HP = window.HydrantProof;

  var DB_NAME = "hydrantproof-field";
  var STORE = "queue";
  var statusEl = document.getElementById("status");
  var gpsReadout = document.getElementById("gps-readout");
  var captured = { gps_lat: null, gps_lon: null, gps_accuracy_m: null };
  var sessionToken = "";

  function say(message) { if (statusEl) { statusEl.textContent = message; } }

  function csrfToken() {
    // The freshest token the page holds: the one from the last /field/ load
    // (re-fetched before every sync), falling back to the rendered form.
    if (sessionToken) { return sessionToken; }
    var input = document.querySelector("#capture-form input[name=csrfmiddlewaretoken]");
    return input ? input.value : "";
  }

  function tokenFromHtml(html) {
    var match = /name="csrfmiddlewaretoken"[^>]*value="([^"]+)"/.exec(html);
    return match ? match[1] : "";
  }

  function uuid4() {
    if (window.crypto && crypto.randomUUID) { return crypto.randomUUID(); }
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0, v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function openDb() {
    return new Promise(function (resolve, reject) {
      var request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = function () {
        request.result.createObjectStore(STORE, { keyPath: "client_uuid" });
      };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error); };
    });
  }

  function queuePut(entry) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).put(entry);
        tx.oncomplete = function () { resolve(entry); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function queueAll() {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readonly");
        var request = tx.objectStore(STORE).getAll();
        request.onsuccess = function () { resolve(request.result || []); };
        request.onerror = function () { reject(request.error); };
      });
    });
  }

  function queueDelete(clientUuid) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, "readwrite");
        tx.objectStore(STORE).delete(clientUuid);
        tx.oncomplete = resolve;
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }

  function selectedHydrant() {
    var select = document.getElementById("hydrant");
    if (!select || !select.value) { return null; }
    var option = select.options[select.selectedIndex];
    var tag = option ? (option.getAttribute("data-asset-tag") || option.textContent) : "";
    return { id: select.value, asset_tag: (tag || "").trim() };
  }

  function collect() {
    var outlets = [];
    for (var index = 0; index < 4; index += 1) {
      var d = document.querySelector('[data-outlet="diameter"][data-index="' + index + '"]');
      var c = document.querySelector('[data-outlet="coefficient"][data-index="' + index + '"]');
      var p = document.querySelector('[data-outlet="pitot"][data-index="' + index + '"]');
      if (p && p.value !== "") {
        outlets.push({
          outlet_diameter_in: d && d.value ? d.value : "2.50",
          outlet_coefficient: c && c.value ? c.value : "0.90",
          pitot_psi: p.value,
          outlet_label: "outlet-" + (index + 1)
        });
      }
    }
    var meter = document.getElementById("measured_total_gpm").value;
    var photoInput = document.getElementById("photo");
    var hydrant = selectedHydrant();
    return {
      client_uuid: uuid4(),
      captured_at: new Date().toISOString(),
      hydrant: hydrant ? hydrant.id : "",
      asset_tag: hydrant ? hydrant.asset_tag : "",
      payload: {
        tested_on: document.getElementById("tested_on").value,
        static_psi: document.getElementById("static_psi").value,
        residual_psi: document.getElementById("residual_psi").value,
        measured_total_gpm: meter === "" ? null : meter,
        outlets: outlets,
        notes: ""
      },
      gps: captured,
      // The File itself: IndexedDB clones it, so the offline queue carries the
      // photo and its client capture timestamp survives in captured_at.
      photo: photoInput && photoInput.files && photoInput.files.length ? photoInput.files[0] : null
    };
  }

  function sessionExpiredError() {
    return new Error("session expired — sign in again, then sync");
  }

  function uploadPhoto(entry, test) {
    if (!entry.photo || !test || !test.id) { return Promise.resolve(null); }
    var form = new FormData();
    form.append("file", entry.photo, entry.photo.name || "field-photo.jpg");
    if (entry.gps && entry.gps.gps_accuracy_m != null) {
      form.append("gps_accuracy_m", String(entry.gps.gps_accuracy_m));
    }
    if (entry.captured_at) { form.append("captured_at", entry.captured_at); }
    return fetch("/api/v1/hydrants/" + entry.hydrant + "/tests/" + test.id + "/attachments/", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken() },
      credentials: "same-origin",
      body: form
    }).then(function (response) {
      if (response.status === 403 || response.status === 401) { throw sessionExpiredError(); }
      if (!response.ok) {
        return response.text().then(function (raw) {
          throw new Error("photo upload failed (" + response.status + "): " + raw.slice(0, 200));
        });
      }
      return response.json();
    });
  }

  function submitOnline(entry) {
    var body = JSON.parse(JSON.stringify(entry.payload));
    body.client_uuid = entry.client_uuid;
    return fetch("/api/v1/hydrants/" + entry.hydrant + "/tests/", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken() },
      credentials: "same-origin",
      body: JSON.stringify(body)
    }).then(function (response) {
      if (response.status === 409) {
        return response.json().then(function (data) {
          var conflict = new Error(data.hint || "client_uuid_conflict");
          conflict.status = 409;
          throw conflict;
        });
      }
      if (response.status === 403 || response.status === 401) { throw sessionExpiredError(); }
      if (!response.ok) {
        // HP.readError carries .status and renders a JSON refusal as
        // "HTTP 400 — field: message", never a raw JSON dump in the status line.
        return HP.readError(response).then(function (error) { throw error; });
      }
      return response.json();
    }).then(function (test) {
      // The test row exists (201, or the idempotent 200 replay); the photo
      // rides it as an attachment with the client capture metadata.
      return uploadPhoto(entry, test).then(function () { return test; });
    });
  }

  function refreshQueue() {
    // The header row is the server's own (thead, scope="col" intact); the
    // queue rebuilds its body only, so the markup the page shipped is never
    // paraphrased from JavaScript.
    var body = document.getElementById("queue-body");
    if (!body) { return; }
    queueAll().then(function (rows) {
      body.textContent = "";
      rows.forEach(function (row) {
        var tr = document.createElement("tr");
        [row.asset_tag || row.hydrant, row.payload.tested_on, row.captured_at,
         row.gps && row.gps.gps_accuracy_m != null ? row.gps.gps_accuracy_m + " m" : "—"]
          .forEach(function (value) {
            var td = document.createElement("td");
            td.textContent = String(value);  // textContent only: never bind data as HTML
            tr.appendChild(td);
          });
        body.appendChild(tr);
      });
      if (!rows.length) {
        var empty = document.createElement("tr");
        var cell = document.createElement("td");
        cell.colSpan = 4;
        cell.className = "small";
        // The empty state names the next action, not the absence.
        cell.textContent = "Queue is empty — a reading saved with 'Queue for later' waits here for a connection.";
        empty.appendChild(cell);
        body.appendChild(empty);
      }
    });
  }

  // -------------------------------------------------------------------------
  // Session / CSRF story (§11): before sync, fetch /field/ network-first; a
  // 302-to-login renders the inline re-login form; after login the page holds
  // a fresh CSRF token and syncs — a queued test never 403s on a stale token.
  // -------------------------------------------------------------------------
  function isLoginUrl(url) {
    try { return /\/login\/?$/.test(new URL(url, window.location.href).pathname); }
    catch (error) { return false; }
  }

  function fetchFieldPage() {
    return fetch("/field/", { credentials: "same-origin" }).then(function (response) {
      if (response.redirected && isLoginUrl(response.url)) { return { loggedIn: false }; }
      if (!response.ok) { throw new Error("session check failed (" + response.status + ")"); }
      return response.text().then(function (html) {
        var token = tokenFromHtml(html);
        if (token) { sessionToken = token; }
        return { loggedIn: true };
      });
    });
  }

  function showRelogin(message) {
    var panel = document.getElementById("relogin");
    if (panel) { panel.hidden = false; }
    var note = document.getElementById("relogin-note");
    if (note && message) { note.textContent = message; }
  }

  function hideRelogin() {
    var panel = document.getElementById("relogin");
    if (panel) { panel.hidden = true; }
  }

  function ensureSession() {
    return fetchFieldPage().then(function (session) {
      if (!session.loggedIn) {
        showRelogin("Session expired — sign in to sync the queue.");
        return null;
      }
      hideRelogin();
      return session;
    });
  }

  function relogin() {
    var user = document.getElementById("relogin-username");
    var password = document.getElementById("relogin-password");
    var body = new URLSearchParams();
    return fetch("/login/", { credentials: "same-origin" })
      .then(function (response) { return response.text(); })
      .then(function (html) {
        body.append("username", user ? user.value : "");
        body.append("password", password ? password.value : "");
        body.append("csrfmiddlewaretoken", tokenFromHtml(html));
        return fetch("/login/", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: body.toString()
        });
      })
      .then(function () { return fetchFieldPage(); })
      .then(function (session) {
        if (session.loggedIn) {
          hideRelogin();
          say("Signed in — syncing the queue.");
          return syncQueue();
        }
        showRelogin("Sign-in failed — check the username and password.");
      })
      .catch(function (error) { showRelogin("Sign-in failed: " + error.message); });
  }

  function syncQueue() {
    return ensureSession().then(function (session) {
      if (!session) { return null; }
      return queueAll().then(function (rows) {
        return rows.reduce(function (chain, row) {
          return chain.then(function () {
            return submitOnline(row).then(function () {
              return queueDelete(row.client_uuid);
            }).catch(function (error) {
              if (String(error.message).indexOf("session expired") === 0) {
                showRelogin("Session expired — sign in to sync the queue.");
                say("Sync paused: " + error.message);
              } else {
                say("Queued item kept: " + error.message);
              }
            });
          });
        }, Promise.resolve());
      }).then(refreshQueue);
    }).catch(function (error) {
      say("Sync paused: " + error.message);
      return null;
    });
  }

  // CSV export in the historical-import column format (§10).
  function csvCell(value) {
    var text = value == null ? "" : String(value);
    if (/^[=+\-@\t\r]/.test(text)) { text = "'" + text; }
    if (/[",\r\n]/.test(text)) { text = '"' + text.replace(/"/g, '""') + '"'; }
    return text;
  }

  function exportCsv() {
    queueAll().then(function (rows) {
      var header = ["asset_tag", "tested_on", "static_psi", "residual_psi", "pitot_readings",
                    "measured_total_gpm", "crew_names", "notes", "client_uuid"];
      var lines = [header.join(",")];
      rows.forEach(function (row) {
        var readings = (row.payload.outlets || []).map(function (outlet) {
          return [outlet.outlet_diameter_in, outlet.outlet_coefficient, outlet.pitot_psi].join(":");
        }).join(";");
        var noteBits = [];
        if (row.gps && row.gps.gps_lat != null) {
          noteBits.push("gps=" + row.gps.gps_lat + "," + row.gps.gps_lon
                        + " acc=" + (row.gps.gps_accuracy_m == null ? "?" : row.gps.gps_accuracy_m) + "m");
        }
        noteBits.push("captured_at=" + row.captured_at);
        if (row.payload.notes) { noteBits.push(row.payload.notes); }
        lines.push([
          csvCell(row.asset_tag || ""), csvCell(row.payload.tested_on),
          csvCell(row.payload.static_psi), csvCell(row.payload.residual_psi),
          csvCell(readings), csvCell(row.payload.measured_total_gpm || ""),
          csvCell(""), csvCell(noteBits.join(" | ")), csvCell(row.client_uuid)
        ].join(","));
      });
      var blob = new Blob([lines.join("\r\n") + "\r\n"], { type: "text/csv" });
      var link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "queued-readings.csv";
      link.click();
      say("Exported " + rows.length + " queued reading(s) — photos are not included in the CSV.");
    });
  }

  document.getElementById("gps").addEventListener("click", function () {
    if (!navigator.geolocation) { say("This browser exposes no geolocation."); return; }
    navigator.geolocation.getCurrentPosition(function (position) {
      captured = {
        gps_lat: position.coords.latitude,
        gps_lon: position.coords.longitude,
        gps_accuracy_m: position.coords.accuracy
      };
      gpsReadout.textContent = "Position " + captured.gps_lat.toFixed(6) + ", "
        + captured.gps_lon.toFixed(6) + " (±" + Math.round(captured.gps_accuracy_m) + " m)";
    }, function (error) { say("Geolocation failed: " + error.message); });
  });

  /* --- The entry door ------------------------------------------------------
   * The queue is a delivery mechanism, not a hiding place for a record the
   * engine must refuse: a reading queued without its date, its pressures, or
   * any flow figure can NEVER sync — it would fail every retry forever — and
   * "Queued." said over it is a lie about its future. The door therefore
   * applies the office sheet's own rules before anything is stored or sent:
   * a hydrant, the reading date, both pressures (the inputs carry the
   * engine's numeric bounds), and at least one pitot reading or a meter
   * total. A refusal marks the failing controls beside the field, in the
   * product's own words — gloves on, no guessing.
   */
  function validateEntry(entry) {
    if (!entry.hydrant) {
      return { message: "Select a hydrant first.", fields: [document.getElementById("hydrant")] };
    }
    if (!entry.payload.tested_on) {
      return { message: "Pick the date the readings were taken.", fields: [document.getElementById("tested_on")] };
    }
    if (entry.payload.static_psi === "" || entry.payload.residual_psi === "") {
      return { message: "Static and residual pressures are both required.",
               fields: [document.getElementById("static_psi"), document.getElementById("residual_psi")] };
    }
    // The inputs declare the engine's bounds (min/max). The sweep covers
    // exactly what will be sent: the pressures and meter, plus the three
    // cells of every outlet row that has a pitot reading (a row without one
    // is not sent, so its cells are not judged). A filled cell outside the
    // bounds is refused here rather than at the first sync.
    var numeric = ["static_psi", "residual_psi", "measured_total_gpm"].map(function (id) {
      return document.getElementById(id);
    });
    for (var index = 0; index < 4; index += 1) {
      var rowPitot = document.querySelector('[data-outlet="pitot"][data-index="' + index + '"]');
      if (rowPitot && rowPitot.value !== "") {
        numeric.push(
          rowPitot,
          document.querySelector('[data-outlet="diameter"][data-index="' + index + '"]'),
          document.querySelector('[data-outlet="coefficient"][data-index="' + index + '"]')
        );
      }
    }
    var outOfRange = numeric.filter(function (input) {
      return input && input.value !== "" && !input.checkValidity();
    });
    if (outOfRange.length) {
      return { message: "A reading is outside the range the ledger accepts — check the marked cells.",
               fields: outOfRange };
    }
    if (!entry.payload.outlets.length && entry.payload.measured_total_gpm === null) {
      // Either answer satisfies the rule, so the pitot and the meter are one
      // refusal group: filling either clears both marks.
      return { message: "Enter at least one pitot reading, or a meter total.",
               fields: [document.querySelector('[data-outlet="pitot"][data-index="0"]'),
                        document.getElementById("measured_total_gpm")],
               group: true };
    }
    return null;
  }

  function refuseEntry(refusal) {
    say(refusal.message);
    HP.markInvalid(refusal.fields, statusEl, refusal.group === true);
    var first = refusal.fields.filter(Boolean)[0];
    if (first && first.focus) { first.focus(); }
  }

  document.getElementById("submit-online").addEventListener("click", function () {
    var entry = collect();
    var refusal = validateEntry(entry);
    if (refusal) { refuseEntry(refusal); return; }
    var button = this;
    HP.setBusy(button, true, "Submitting…");
    submitOnline(entry).then(function (test) {
      say((entry.photo ? "Submitted — photo uploaded" : "Submitted")
        + " — recorded as test #" + (test && test.id ? test.id : "?") + ".");
    }).catch(function (error) {
      if (error.status === 400 || error.status === 409) {
        // A refusal, not a connectivity failure: the reading AS ENTERED can
        // never be accepted, so it is NOT queued — a queued copy would fail
        // every sync forever. Fix the marked fields and submit again.
        say("Not queued — the server refused this reading: " + error.message);
        HP.markInvalidFromBody(document.getElementById("capture-form"), error, statusEl);
        return;
      }
      say("Online submit failed (" + error.message + ") — queueing instead.");
      if (String(error.message).indexOf("session expired") === 0) {
        showRelogin("Session expired — sign in to sync the queue.");
      }
      return queuePut(entry).then(refreshQueue);
    }).then(function () {
      HP.setBusy(button, false);
    });
  });

  document.getElementById("queue-offline").addEventListener("click", function () {
    var entry = collect();
    var refusal = validateEntry(entry);
    if (refusal) { refuseEntry(refusal); return; }
    var button = this;
    HP.setBusy(button, true, "Queueing…");
    queuePut(entry).then(function () {
      say(entry.photo ? "Queued with photo." : "Queued.");
      refreshQueue();
    }).catch(function (error) {
      // A failed write must never be reported as a queued one.
      say("NOT queued — this browser refused the local write"
        + (error && error.message ? ": " + error.message : "")
        + ". Export or submit while online instead.");
    }).then(function () {
      HP.setBusy(button, false);
    });
  });

  var reloginSubmit = document.getElementById("relogin-submit");
  if (reloginSubmit) { reloginSubmit.addEventListener("click", relogin); }

  document.getElementById("export-csv").addEventListener("click", exportCsv);
  window.addEventListener("online", syncQueue);

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () { /* offline shell is best-effort */ });
  }
  refreshQueue();
  if (navigator.onLine) { syncQueue(); }
})();
