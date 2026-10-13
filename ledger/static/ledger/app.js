/* HydrantProof — the shell's behaviour layer.
 *
 * Four things live here and nothing else:
 *   1. the theme toggle (a persisted operator choice, applied pre-paint in
 *      base.html and toggled here),
 *   2. toasts (a real, dismissible status region),
 *   3. one confirm dialog with focus management,
 *   4. `HydrantProof.post()` — the single async-action helper every page uses,
 *      so loading / success / failure states are identical everywhere and no
 *      button can ever fake success.
 *
 * No data is ever bound as HTML: every insertion below uses textContent.
 */
(function () {
  "use strict";

  var HP = {};
  window.HydrantProof = HP;

  function csrfToken() {
    var input = document.querySelector("input[name=csrfmiddlewaretoken]");
    if (input && input.value) { return input.value; }
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }
  HP.csrfToken = csrfToken;

  /* --- Theme ------------------------------------------------------------- */
  var root = document.documentElement;
  var toggle = document.getElementById("theme-toggle");
  var toggleLabel = document.getElementById("theme-toggle-label");

  function effectiveTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit === "light" || explicit === "dark") { return explicit; }
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintToggle() {
    if (!toggle) { return; }
    var dark = effectiveTheme() === "dark";
    toggle.setAttribute("aria-pressed", dark ? "true" : "false");
    if (toggleLabel) { toggleLabel.textContent = dark ? "Light theme" : "Dark theme"; }
  }
  if (toggle) {
    paintToggle();
    toggle.addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { window.localStorage.setItem("hydrantproof.theme", next); } catch (error) { /* session-only */ }
      paintToggle();
    });
  }

  /* --- Toasts ------------------------------------------------------------ */
  var stack = document.getElementById("toast-stack");

  HP.toast = function (message, kind, options) {
    if (!stack) { return null; }
    var settings = options || {};
    var el = document.createElement("div");
    el.className = "toast toast--" + (kind || "info");
    var text = document.createElement("span");
    text.textContent = String(message);
    el.appendChild(text);
    if (settings.actionLabel && typeof settings.onAction === "function") {
      var action = document.createElement("button");
      action.type = "button";
      action.className = "btn btn--ghost btn--sm";
      action.textContent = settings.actionLabel;
      action.addEventListener("click", function () { settings.onAction(); dismiss(); });
      el.appendChild(action);
    }
    var close = document.createElement("button");
    close.type = "button";
    close.className = "toast__close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "\u00D7";
    close.addEventListener("click", dismiss);
    el.appendChild(close);
    stack.appendChild(el);
    function dismiss() { if (el.parentNode) { el.parentNode.removeChild(el); } }
    if (settings.sticky !== true) { window.setTimeout(dismiss, 8000); }
    return el;
  };

  /* --- Confirm dialog ---------------------------------------------------- */
  var modal = document.getElementById("confirm-modal");
  var modalTitle = document.getElementById("confirm-modal-title");
  var modalBody = document.getElementById("confirm-modal-body");
  var modalAccept = document.getElementById("confirm-modal-accept");
  var lastFocused = null;

  function openModal(options) {
    return new Promise(function (resolve) {
      if (!modal) { resolve(window.confirm(options.body || "Confirm?")); return; }
      lastFocused = document.activeElement;
      modalTitle.textContent = options.title || "Confirm";
      modalBody.textContent = options.body || "";
      modalAccept.textContent = options.acceptLabel || "Confirm";
      modalAccept.className = "btn" + (options.danger ? " btn--danger" : "");
      modal.hidden = false;
      document.body.style.overflow = "hidden";
      modalAccept.focus();

      function finish(result) {
        modal.hidden = true;
        document.body.style.overflow = "";
        modalAccept.removeEventListener("click", onAccept);
        modal.removeEventListener("click", onDismissClick);
        document.removeEventListener("keydown", onKey);
        if (lastFocused && lastFocused.focus) { lastFocused.focus(); }
        resolve(result);
      }
      function onAccept() { finish(true); }
      function onDismissClick(event) {
        if (event.target.closest("[data-modal-dismiss]")) { finish(false); }
      }
      function onKey(event) {
        if (event.key === "Escape") { finish(false); return; }
        if (event.key !== "Tab") { return; }
        var focusables = modal.querySelectorAll("button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])");
        if (!focusables.length) { return; }
        var first = focusables[0];
        var last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
      }
      modalAccept.addEventListener("click", onAccept);
      modal.addEventListener("click", onDismissClick);
      document.addEventListener("keydown", onKey);
    });
  }
  HP.confirm = openModal;

  /* --- The one async-action helper --------------------------------------- */
  function setBusy(button, busy, busyLabel) {
    if (!button) { return; }
    if (busy) {
      button.dataset.idleLabel = button.textContent;
      button.setAttribute("aria-busy", "true");
      button.disabled = true;
      button.textContent = busyLabel || "Working\u2026";
    } else {
      button.removeAttribute("aria-busy");
      button.disabled = false;
      if (button.dataset.idleLabel) { button.textContent = button.dataset.idleLabel; }
    }
  }
  HP.setBusy = setBusy;

  function readError(response) {
    return response.text().then(function (raw) {
      var detail = raw;
      try {
        var parsed = JSON.parse(raw);
        if (parsed && typeof parsed === "object") {
          var parts = [];
          Object.keys(parsed).forEach(function (key) {
            var value = parsed[key];
            parts.push(key + ": " + (Array.isArray(value) ? value.join("; ") : String(value)));
          });
          detail = parts.length ? parts.join(" \u00B7 ") : raw;
        }
      } catch (error) { /* not JSON: keep the raw text */ }
      var message = "HTTP " + response.status + (detail ? " \u2014 " + String(detail).slice(0, 400) : "");
      var error = new Error(message);
      error.status = response.status;
      error.body = raw;
      return error;
    });
  }
  HP.readError = readError;

  /* --- Error association --------------------------------------------------
   * A custom refusal is announced in a role="status" line; the failing
   * control must also declare its own failed state (aria-invalid) and name
   * the message that explains it (aria-describedby). Every refuse/error path
   * goes through these helpers, so a screen reader hears the field, not only
   * the sentence beside it. The status span gets a stable id if the template
   * did not give it one, and a pre-existing aria-describedby (a hint) is
   * preserved and restored.
   *
   * Group semantics: `markInvalid([a, b], line, true)` marks a refusal where
   * ANY of the listed controls satisfies the message ("a date OR the
   * indefinite flag"); editing any of them clears the whole group. Without
   * the flag each listed control is marked on its own and cleared by its own
   * edit — the shape for several independently-required controls.
   */
  var statusSeq = 0;
  var errorGroupSeq = 0;

  function describeError(field, statusEl, group) {
    if (!field || !field.setAttribute) { return; }
    if (field.getAttribute("aria-invalid") !== "true") {
      field.dataset.hpDescribedby = field.getAttribute("aria-describedby") || "";
      field.setAttribute("aria-invalid", "true");
    }
    if (group) { field.dataset.hpErrorGroup = group; }
    if (!statusEl) { return; }
    if (!statusEl.id) {
      statusSeq += 1;
      statusEl.id = "hp-error-" + statusSeq;
    }
    field.setAttribute("aria-describedby",
      (field.dataset.hpDescribedby ? field.dataset.hpDescribedby + " " : "") + statusEl.id);
  }

  function undescribe(field) {
    if (!field || !field.getAttribute || field.getAttribute("aria-invalid") !== "true") { return; }
    field.removeAttribute("aria-invalid");
    if (field.dataset.hpDescribedby) {
      field.setAttribute("aria-describedby", field.dataset.hpDescribedby);
    } else {
      field.removeAttribute("aria-describedby");
    }
    delete field.dataset.hpDescribedby;
    delete field.dataset.hpErrorGroup;
  }

  /** Mark one field, several independent fields, or a group of alternatives. */
  HP.markInvalid = function (fields, statusEl, asGroup) {
    var list = (Array.isArray(fields) ? fields : [fields]).filter(Boolean);
    if (!list.length) { return; }
    var group = "";
    if (asGroup && list.length > 1) {
      errorGroupSeq += 1;
      group = "hp-error-" + errorGroupSeq;
    }
    list.forEach(function (field) { describeError(field, statusEl, group); });
  };

  /** Clear the refused state on a scope (a form) or on one field. */
  HP.clearInvalid = function (scope) {
    if (!scope) { return; }
    var root = scope;
    if (root.getAttribute && root.getAttribute("aria-invalid") === "true") {
      undescribe(root);
      return;
    }
    if (!root.querySelectorAll) { return; }
    Array.prototype.slice.call(root.querySelectorAll('[aria-invalid="true"]')).forEach(undescribe);
  };

  /* The refusal ends when the operator edits the field — or, for a group of
   * alternatives, when they edit any member of it. */
  document.addEventListener("input", function (event) {
    var field = event.target;
    if (!field || !field.getAttribute) { return; }
    var group = field.dataset.hpErrorGroup;
    if (group) {
      Array.prototype.slice.call(
        document.querySelectorAll('[data-hp-error-group="' + group + '"]')
      ).forEach(undescribe);
      return;
    }
    undescribe(field);
  });

  /**
   * An API refusal names the fields it refused; mark them. A non-JSON body
   * leaves the message in the status line only — never a silent field.
   */
  HP.markInvalidFromBody = function (form, error, statusEl) {
    if (!form || !form.elements || !error || !error.body) { return; }
    var parsed = null;
    try { parsed = JSON.parse(error.body); } catch (parseError) { return; }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) { return; }
    Object.keys(parsed).forEach(function (name) {
      var found = form.elements[name];
      if (!found) { return; }
      if (typeof found.length === "number" && !found.tagName) {
        HP.markInvalid(Array.prototype.slice.call(found), statusEl);
      } else {
        describeError(found, statusEl);
      }
    });
  };

  /**
   * POST a JSON body to a real route. Resolves with the parsed body (or null
   * for 204). Never reports success on a non-2xx response.
   *
   * options: {button, statusEl, busyLabel, onStart, onDone, headers}
   */
  HP.send = function (method, url, body, options) {
    var settings = options || {};
    setBusy(settings.button, true, settings.busyLabel);
    if (settings.statusEl) { settings.statusEl.textContent = settings.busyLabel || "Working\u2026"; }
    if (typeof settings.onStart === "function") { settings.onStart(); }

    var headers = { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), "Accept": "application/json" };
    if (settings.headers) { Object.keys(settings.headers).forEach(function (k) { headers[k] = settings.headers[k]; }); }

    return window.fetch(url, {
      method: method,
      credentials: "same-origin",
      headers: headers,
      body: JSON.stringify(body || {})
    }).then(function (response) {
      if (response.status === 204) { return null; }
      if (!response.ok) { return readError(response).then(function (error) { throw error; }); }
      var type = response.headers.get("content-type") || "";
      return type.indexOf("json") === -1 ? response.text() : response.json();
    }).then(function (payload) {
      setBusy(settings.button, false);
      if (settings.statusEl) { settings.statusEl.textContent = ""; }
      if (settings.form) { HP.clearInvalid(settings.form); }
      if (typeof settings.onDone === "function") { settings.onDone(payload); }
      return payload;
    }).catch(function (error) {
      setBusy(settings.button, false);
      if (settings.form) { HP.markInvalidFromBody(settings.form, error, settings.statusEl); }
      if (settings.statusEl) { settings.statusEl.textContent = error.message; }
      throw error;
    });
  };

  HP.post = function (url, body, options) { return HP.send("POST", url, body, options); };
  HP.patch = function (url, body, options) { return HP.send("PATCH", url, body, options); };

  /**
   * Attach up to four photos to a record that already exists. A failed upload
   * is reported, never hidden: the record is stored either way, and the caller
   * decides how loudly to say so. Shared by every capture surface so the
   * attachment contract lives in exactly one place.
   */
  HP.uploadPhotos = function (url, files) {
    var results = { uploaded: 0, failed: [] };
    var list = Array.prototype.slice.call(files || []).slice(0, 4);
    return list.reduce(function (chain, file) {
      return chain.then(function () {
        var body = new FormData();
        body.append("file", file, file.name);
        body.append("captured_at", new Date().toISOString());
        return window.fetch(url, {
          method: "POST",
          credentials: "same-origin",
          headers: { "X-CSRFToken": csrfToken() },
          body: body
        }).then(function (response) {
          if (response.ok) { results.uploaded += 1; }
          else { results.failed.push(file.name + " (HTTP " + response.status + ")"); }
        }).catch(function () { results.failed.push(file.name + " (network)"); });
      });
    }, Promise.resolve()).then(function () { return results; });
  };

  /* --- Declarative wiring: data-confirm + data-post ----------------------- */
  document.addEventListener("click", function (event) {
    var trigger = event.target.closest("[data-confirm]");
    if (!trigger) { return; }
    if (trigger.dataset.confirmed === "true") { trigger.dataset.confirmed = "false"; return; }
    event.preventDefault();
    event.stopPropagation();
    openModal({
      title: trigger.dataset.confirmTitle || "Confirm",
      body: trigger.dataset.confirm,
      acceptLabel: trigger.dataset.confirmAccept || "Confirm",
      danger: trigger.dataset.confirmDanger === "true"
    }).then(function (accepted) {
      if (!accepted) { return; }
      if (trigger.form && typeof trigger.form.requestSubmit === "function") {
        trigger.dataset.confirmed = "true";
        trigger.form.requestSubmit(trigger);
      } else if (trigger.tagName === "A") {
        window.location.assign(trigger.getAttribute("href"));
      } else {
        trigger.dataset.confirmed = "true";
        trigger.click();
      }
    });
  }, true);

  /* --- Declarative async forms -------------------------------------------
   * Every in-page mutation is a real <form> posting to a real route, so it
   * still works (as a plain navigation) with JavaScript off; with JavaScript
   * on, this interceptor keeps the operator on the page and reports the
   * outcome in the form's own status element.
   *
   *   data-async              opt in
   *   data-async-done         none | mark | remove | reload  (default none)
   *   data-async-ok           toast text on success
   *   data-async-busy         button label while in flight
   */
  document.addEventListener("submit", function (event) {
    var form = event.target.closest("form[data-async]");
    if (!form) { return; }
    event.preventDefault();
    if (typeof form.checkValidity === "function" && !form.checkValidity()) {
      form.reportValidity();   // the field-level message, next to the field
      return;
    }

    var submitter = event.submitter || form.querySelector("button[type=submit], input[type=submit]");
    var status = form.querySelector("[data-async-status]");
    var url = form.getAttribute("action");
    var body = new FormData(form);
    body.append("csrfmiddlewaretoken", csrfToken());

    setBusy(submitter, true, form.dataset.asyncBusy || "Working\u2026");
    if (status) { status.textContent = form.dataset.asyncBusy || "Working\u2026"; }

    window.fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": csrfToken(), "Accept": "application/json" },
      body: body
    }).then(function (response) {
      if (!response.ok) { return readError(response).then(function (error) { throw error; }); }
      return response.status === 204 ? null : response.text();
    }).then(function () {
      setBusy(submitter, false);
      if (status) { status.textContent = ""; }
      HP.clearInvalid(form);
      if (form.dataset.asyncOk) { HP.toast(form.dataset.asyncOk, "ok"); }
      var done = form.dataset.asyncDone || "none";
      if (done === "mark") {
        form.dataset.confirmed = "false";
        var cell = form.closest("[data-async-cell]") || form;
        cell.textContent = "";
        var mark = document.createElement("span");
        mark.className = "small";
        mark.textContent = form.dataset.asyncOk || "Done.";
        cell.appendChild(mark);
        // A state badge in the same row must not keep asserting the old state.
        if (form.dataset.asyncBadge) {
          var rowForBadge = form.closest("tr");
          var old = rowForBadge && rowForBadge.querySelector(form.dataset.asyncBadge);
          if (old) {
            var badge = document.createElement("span");
            badge.className = "badge badge--watch";
            var dot = document.createElement("span");
            dot.className = "dot dot--watch";
            dot.setAttribute("aria-hidden", "true");
            badge.appendChild(dot);
            badge.appendChild(document.createTextNode(form.dataset.asyncBadgeText || "Done"));
            old.replaceWith(badge);
          }
        }
      } else if (done === "remove") {
        var row = form.closest("tr");
        if (row) {
          var tbody = row.parentNode;
          row.remove();
          // A queue that just emptied must say so, in the same words the
          // server-rendered empty state uses — never a headless table.
          if (tbody && form.dataset.asyncEmpty && !tbody.querySelector("tr")) {
            var head = tbody.closest("table") && tbody.closest("table").querySelector("thead tr");
            var emptyRow = document.createElement("tr");
            var emptyCell = document.createElement("td");
            emptyCell.className = "small";
            if (head) { emptyCell.colSpan = head.children.length || 1; }
            emptyCell.textContent = form.dataset.asyncEmpty;
            emptyRow.appendChild(emptyCell);
            tbody.appendChild(emptyRow);
          }
          // The panel's count badge must move with the row, or it lies.
          if (form.dataset.asyncCount) {
            var scope = form.closest(".panel") || document;
            var badge = scope.querySelector(form.dataset.asyncCount);
            var count = badge && parseInt(badge.textContent, 10);
            if (badge && !isNaN(count) && count > 0) { badge.textContent = String(count - 1); }
          }
        }
      } else if (done === "reload") {
        window.location.reload();
      }
    }).catch(function (error) {
      setBusy(submitter, false);
      HP.markInvalidFromBody(form, error, status);
      if (status) { status.textContent = error.message; }
      HP.toast(error.message, "bad", { sticky: true });
    });
  });

  /* --- Roving tabindex for the readiness board ---------------------------
   * A board of 240 hydrants must not be 240 tab stops. The container is one
   * stop; arrow keys walk the pips, Home/End jump to the ends. Every pip is
   * still a real link and still reachable by screen readers.
   *
   * Up/Down step by a whole ROW, not by one square: the board wraps, so the
   * column count is measured from the laid-out rows (the first row's item
   * count) unless the server pinned it via data-roving-columns. Measured per
   * keypress, because a resize re-wraps the board.
   */
  document.querySelectorAll("[data-roving]").forEach(function (container) {
    var items = Array.prototype.slice.call(container.querySelectorAll("a, button"));
    if (!items.length) { return; }
    var start = items.findIndex(function (item) { return item.getAttribute("aria-current") === "true"; });
    if (start < 0) { start = 0; }
    items.forEach(function (item, index) { item.tabIndex = index === start ? 0 : -1; });

    function columnCount() {
      var pinned = parseInt(container.dataset.rovingColumns || "0", 10);
      if (pinned > 0) { return pinned; }
      if (items.length < 2) { return 1; }
      var top = items[0].offsetTop;
      var count = 1;
      for (var i = 1; i < items.length; i += 1) {
        if (items[i].offsetTop !== top) { break; }
        count += 1;
      }
      return count;
    }

    function focusAt(index) {
      var next = (index + items.length) % items.length;
      items.forEach(function (item, i) { item.tabIndex = i === next ? 0 : -1; });
      items[next].focus();
    }
    container.addEventListener("keydown", function (event) {
      var current = items.indexOf(document.activeElement);
      if (current < 0) { return; }
      var step = columnCount();
      if (event.key === "ArrowRight") { focusAt(current + 1); }
      else if (event.key === "ArrowLeft") { focusAt(current - 1); }
      else if (event.key === "ArrowDown") { focusAt(current + step); }
      else if (event.key === "ArrowUp") { focusAt(current - step); }
      else if (event.key === "Home") { focusAt(0); }
      else if (event.key === "End") { focusAt(items.length - 1); }
      else { return; }
      event.preventDefault();
    });
  });

  /* --- The readiness board's readout --------------------------------------
   * The board's copy promises the asset tag on hover and on focus; this keeps
   * that promise for a sighted eye (a pip's aria-label keeps it for assistive
   * technology, which is why the readout itself is aria-hidden). The idle hint
   * is whatever the server rendered, captured once.
   */
  document.querySelectorAll("[data-board-readout]").forEach(function (readout) {
    var board = readout.parentElement && readout.parentElement.querySelector(".board");
    if (!board) { return; }
    var hint = readout.textContent;
    function nameSquare(target) {
      var pip = target && target.closest ? target.closest(".board__pip") : null;
      var tag = pip ? pip.getAttribute("data-tag") : "";
      if (!tag) { return; }
      readout.textContent = tag;
      readout.classList.add("board__readout--active");
    }
    function idle(event) {
      if (event && event.relatedTarget && board.contains(event.relatedTarget)) { return; }
      var active = document.activeElement;
      if (active && active !== document.body && board.contains(active)) {
        nameSquare(active);   // the ring is still on the board; keep naming it
        return;
      }
      readout.textContent = hint;
      readout.classList.remove("board__readout--active");
    }
    board.addEventListener("pointerover", function (event) { nameSquare(event.target); });
    board.addEventListener("pointerleave", function () { idle(); });
    board.addEventListener("focusin", function (event) { nameSquare(event.target); });
    board.addEventListener("focusout", function (event) { idle(event); });
  });

  /* --- Flash region: server-rendered errors get a toast too --------------- */
  document.querySelectorAll("[data-toast]").forEach(function (node) {
    HP.toast(node.dataset.toast, node.dataset.toastKind || "info", { sticky: node.dataset.toastSticky === "true" });
    node.remove();
  });
})();
