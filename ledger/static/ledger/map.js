/* HydrantProof map: vendored Leaflet, CSS-grid graticule, class-coloured markers.
   Every colour is read from the token layer at runtime (see pinmap.js) — no
   colour literal lives in JavaScript. No property is ever bound as HTML:
   labels go through textContent only.

   The credit ring draws the district's OWN credit radius — the same number the
   caption under the map states. The view supplies it in metres on
   `data-credit-radius-m` (converted from the district setting through
   geo.FEET_PER_METER, the product's one feet-per-metre constant); a missing
   value draws no ring, because a guessed ring would caption a number the
   drawing does not use. */
(function () {
  "use strict";
  var el = document.getElementById("map");
  if (!el || typeof L === "undefined") { return; }

  function token(name) {
    return (getComputedStyle(document.documentElement).getPropertyValue(name) || "").trim();
  }

  var accent = token("--accent");
  var markerFill = token("--class-aa");
  var overlayEdge = token("--fg-tertiary");

  var lat = parseFloat(el.dataset.lat || "0");
  var lon = parseFloat(el.dataset.lon || "0");
  var tileUrl = el.dataset.tile || "";
  var creditRadiusM = parseFloat(el.dataset.creditRadiusM || "");
  var map = L.map(el, { center: [lat, lon], zoom: 14, attributionControl: true });
  if (tileUrl) {
    L.tileLayer(tileUrl, { maxZoom: 19 }).addTo(map);
  } else {
    // Offline default: the graticule is the token layer's own background.
    el.classList.add("map--graticule");
    var notice = document.createElement("div");
    notice.className = "map-notice";
    notice.textContent = "No basemap configured (MAP_TILE_URL is empty) — graticule and overlays only.";
    el.parentNode.appendChild(notice);
  }
  L.circleMarker([lat, lon], {
    radius: 8,
    color: accent,
    fillColor: markerFill,
    fillOpacity: 0.9,
    // The semantic class lets the stylesheet own the stroke (see app.css):
    // the colour then follows a theme flip instead of being the value this
    // script captured at load. The option above is the no-stylesheet fallback.
    className: "hydrant-marker"
  })
    .addTo(map)
    .bindTooltip(String(el.dataset.label || ""), { direction: "top" });
  if (!isNaN(creditRadiusM) && creditRadiusM > 0) {
    L.circle([lat, lon], {
      radius: creditRadiusM,
      color: accent,
      weight: 1,
      fill: false,
      className: "credit-ring"
    }).addTo(map);
  }
  L.control.scale().addTo(map);

  // The active district overlay (uploaded GeoJSON or the seeded sample):
  // properties render through textContent-built DOM nodes — never as HTML.
  fetch("/api/v1/overlays/active/", { credentials: "same-origin" })
    .then(function (response) { return response.ok ? response.json() : null; })
    .then(function (document_) {
      if (!document_ || !document_.features) { return; }
      L.geoJSON(document_, {
        style: { color: overlayEdge, weight: 2, fillOpacity: 0.08 },
        pointToLayer: function (feature, latlng) {
          return L.circleMarker(latlng, { radius: 5, color: overlayEdge, fillOpacity: 0.5 });
        },
        onEachFeature: function (feature, layer) {
          var props = (feature && feature.properties) || {};
          var box = document.createElement("div");
          Object.keys(props).forEach(function (key) {
            var line = document.createElement("div");
            line.textContent = key + ": " + String(props[key]);
            box.appendChild(line);
          });
          if (box.childNodes.length) { layer.bindTooltip(box); }
        }
      }).addTo(map);
    })
    .catch(function () { /* no overlay or offline — the graticule stands */ });
})();
