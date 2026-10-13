/* HydrantProof — the pin-drop map on the hydrant and parcel forms.
 *
 * Three ways in, one source of truth: the latitude/longitude inputs. Clicking
 * the map or using the device position writes those inputs; typing in them
 * moves the marker. Nothing is submitted from this file.
 *
 * The element's data attributes configure the wiring so one map serves both
 * forms; every default is the hydrant form's own id, so that page's behaviour
 * is unchanged:
 *
 *   data-lat-input / data-lon-input   ids of the coordinate inputs (lat/lon)
 *   data-readout / data-button        ids of the geolocation readout + button
 *   data-ref-lat / data-ref-lon       a fixed reference marker (the parcel
 *                                     centroid beside its structure override)
 *
 * The controller is exposed as `el._pinmap` ({ clear }) so the owning form can
 * remove the pin when the operator chooses "no override".
 *
 * Browser geolocation is client-asserted and unavailable on a plain-HTTP
 * origin, so its absence is stated in words rather than silently ignored.
 */
(function () {
  "use strict";

  var el = document.getElementById("pin-map");
  if (!el || typeof L === "undefined") { return; }

  var latInput = document.getElementById(el.dataset.latInput || "lat");
  var lonInput = document.getElementById(el.dataset.lonInput || "lon");
  var readout = document.getElementById(el.dataset.readout || "geo-readout");
  var button = document.getElementById(el.dataset.button || "use-location");
  if (!latInput || !lonInput) { return; }

  // Tokens come from the stylesheet and nowhere else (see map.js).
  function token(name) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim();
  }

  var neutral = token("--status-neutral-edge") || token("--border-strong");

  var startLat = parseFloat(latInput.value) || 39.5;
  var startLon = parseFloat(lonInput.value) || -98.35;
  var hasPin = latInput.value !== "" && lonInput.value !== "";
  var tileUrl = el.dataset.tile || "";

  var refLat = parseFloat(el.dataset.refLat || "");
  var refLon = parseFloat(el.dataset.refLon || "");
  var hasRef = el.dataset.refLat !== undefined && el.dataset.refLat !== "" && !isNaN(refLat) && !isNaN(refLon);

  var center = hasPin ? [startLat, startLon] : hasRef ? [refLat, refLon] : [startLat, startLon];
  var map = L.map(el, { center: center, zoom: hasPin || hasRef ? 15 : 4, attributionControl: true });

  if (tileUrl) {
    L.tileLayer(tileUrl, { maxZoom: 19 }).addTo(map);
  } else {
    el.classList.add("map--graticule");
    var notice = document.createElement("div");
    notice.className = "map-notice";
    notice.textContent = "No basemap configured (MAP_TILE_URL is empty). Click anywhere on the graticule to place "
      + "the pin, or type coordinates below.";
    el.parentNode.appendChild(notice);
  }

  var marker = null;

  function place(lat, lon, focus) {
    if (marker) {
      marker.setLatLng([lat, lon]);
    } else {
      marker = L.marker([lat, lon], { draggable: true, autoPan: true }).addTo(map);
      marker.on("dragend", function () {
        var point = marker.getLatLng();
        write(point.lat, point.lng);
      });
    }
    if (focus) { map.setView([lat, lon], Math.max(map.getZoom(), 15)); }
  }

  function clear() {
    if (marker) {
      marker.remove();
      marker = null;
    }
  }

  function write(lat, lon) {
    latInput.value = Number(lat).toFixed(7);
    lonInput.value = Number(lon).toFixed(7);
    latInput.dispatchEvent(new Event("change", { bubbles: true }));
    lonInput.dispatchEvent(new Event("change", { bubbles: true }));
  }

  el._pinmap = { clear: clear };

  if (hasPin) { place(startLat, startLon, false); }

  if (hasRef) {
    // A hollow circle that never intercepts a click: the map's own click is
    // what places the pin, so the reference point is drawn, not wired. The
    // owning form's copy says what the circle is. Its hue is the cascade's
    // (the class, then the stylesheet) so a theme flip repaints it; the color
    // option is the no-stylesheet fallback.
    L.circleMarker([refLat, refLon], {
      radius: 6,
      color: neutral,
      weight: 2,
      fill: false,
      interactive: false,
      className: "ref-marker"
    }).addTo(map);
  }

  map.on("click", function (event) {
    if (latInput.disabled || lonInput.disabled) { return; }
    place(event.latlng.lat, event.latlng.lng, false);
    write(event.latlng.lat, event.latlng.lng);
  });

  function syncFromInputs() {
    var lat = parseFloat(latInput.value);
    var lon = parseFloat(lonInput.value);
    if (isNaN(lat) || isNaN(lon)) { return; }
    place(lat, lon, false);
  }
  latInput.addEventListener("change", syncFromInputs);
  lonInput.addEventListener("change", syncFromInputs);

  if (button) {
    button.addEventListener("click", function () {
      if (!navigator.geolocation) {
        if (readout) { readout.textContent = "This browser exposes no geolocation — type the coordinates instead."; }
        return;
      }
      if (readout) {
        // A fresh attempt owns the readout: any caution from the last fix ends here.
        readout.classList.remove("warn");
        readout.textContent = "Waiting for a position\u2026";
      }
      navigator.geolocation.getCurrentPosition(function (position) {
        var accuracy = Math.round(position.coords.accuracy);
        place(position.coords.latitude, position.coords.longitude, true);
        write(position.coords.latitude, position.coords.longitude);
        if (readout) {
          readout.textContent = "Device position \u00B1" + accuracy + " m, client-asserted. "
            + "Drag the pin or type coordinates if the fix is off.";
          // The caution is a class, never an inline style: the cascade (and a
          // theme flip) still owns the colour, and a later good fix clears it.
          readout.classList.toggle("warn", accuracy > 30);
        }
      }, function (error) {
        if (readout) { readout.textContent = "Geolocation refused: " + error.message + "."; }
      }, { enableHighAccuracy: true, timeout: 12000 });
    });
  }
})();
