/**
 * SARPack TRAILHEAD — map.js
 * Leaflet map module. Shows operator position, team positions,
 * and assigned search segments on a topographic map.
 * Tiles are cached by the service worker for offline use.
 */

/**
 * Initialize the TRAILHEAD map.
 * @param {string} incidentId   - Active incident UUID
 * @param {number} incidentLat  - Incident base latitude
 * @param {number} incidentLng  - Incident base longitude
 * @param {function} getToken   - Returns current auth token
 * @returns {object} Map controller with updatePosition() method
 */
export function initMap(incidentId, incidentLat, incidentLng, getToken) {
  // Default center — Schuylkill County, PA if no incident location
  const centerLat = incidentLat || 40.71;
  const centerLng = incidentLng || -76.20;

  const map = L.map('map-container', {
    center:          [centerLat, centerLng],
    zoom:            13,
    zoomControl:     true,
    attributionControl: true,
  });

  // OpenTopoMap — best for wilderness SAR (topo contours, trails, elevation)
  L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom:     17,
    attribution: '© OpenTopoMap (CC-BY-SA)',
    subdomains:  'abc',
  }).addTo(map);

  // Markers
  let myMarker      = null;
  let incidentMarker = null;
  const teamMarkers  = {};
  const segmentLayers = L.featureGroup().addTo(map);

  // My position marker — orange dot
  const myIcon = L.divIcon({
    className: '',
    html: `<div style="
      width:14px;height:14px;
      background:#ff8c00;
      border:3px solid white;
      border-radius:50%;
      box-shadow:0 0 6px rgba(0,0,0,0.5);
    "></div>`,
    iconSize:   [14, 14],
    iconAnchor: [7, 7],
  });

  // Incident base marker — red pin
  if (incidentLat && incidentLng) {
    incidentMarker = L.marker([incidentLat, incidentLng], {
      icon: L.divIcon({
        className: '',
        html: `<div style="
          width:12px;height:12px;
          background:#ff4444;
          border:2px solid white;
          border-radius:2px;
          box-shadow:0 0 4px rgba(0,0,0,0.5);
        "></div>`,
        iconSize:   [12, 12],
        iconAnchor: [6, 6],
      }),
    }).addTo(map).bindPopup('Incident base');
  }

  // Load incident data (segments and team positions)
  loadIncidentData(incidentId, getToken, map, segmentLayers, teamMarkers);

  // Refresh team positions every 30 seconds
  setInterval(() => {
    loadIncidentData(incidentId, getToken, map, segmentLayers, teamMarkers);
  }, 30_000);

  // ---------------------------------------------------------------------------
  // Public controller
  // ---------------------------------------------------------------------------

  return {
    /**
     * Update the operator's own position marker on the map.
     * @param {number} lat
     * @param {number} lng
     */
    updatePosition(lat, lng) {
      if (myMarker) {
        myMarker.setLatLng([lat, lng]);
      } else {
        myMarker = L.marker([lat, lng], { icon: myIcon })
          .addTo(map)
          .bindPopup('Your position');
        map.setView([lat, lng], 14);
      }
    },

    /** Center map on operator's current position. */
    centerOnMe() {
      if (myMarker) {
        map.setView(myMarker.getLatLng(), 15);
      }
    },

    /** Center map on incident base. */
    centerOnBase() {
      if (incidentMarker) {
        map.setView(incidentMarker.getLatLng(), 13);
      }
    },

    /** Force refresh of team data. */
    refresh() {
      loadIncidentData(incidentId, getToken, map, segmentLayers, teamMarkers);
    },
  };
}


// ---------------------------------------------------------------------------
// Load incident data from server
// ---------------------------------------------------------------------------

async function loadIncidentData(incidentId, getToken, map, segmentLayers, teamMarkers) {
  try {
    const response = await fetch(`/api/operator/incident/${incidentId}`, {
      headers: { 'Authorization': `Bearer ${getToken()}` },
    });

    if (!response.ok) return;
    const data = await response.json();

    // Render search segments
    segmentLayers.clearLayers();
    for (const seg of data.segments || []) {
      if (!seg.boundary_coords) continue;
      try {
        const coords = JSON.parse(seg.boundary_coords);
        const color  = segmentColor(seg.status);
        const poly   = L.polygon(coords, {
          color:       color,
          fillColor:   color,
          fillOpacity: 0.15,
          weight:      2,
        });
        poly.bindPopup(
          `<b>Segment ${seg.segment_id}</b><br>` +
          `Status: ${seg.status}<br>` +
          `Team: ${seg.assigned_team || 'Unassigned'}`
        );
        segmentLayers.addLayer(poly);

        // Label
        const center = poly.getBounds().getCenter();
        L.marker(center, {
          icon: L.divIcon({
            className: '',
            html: `<div style="
              font-size:11px;font-weight:bold;
              color:${color};
              text-shadow:0 0 3px white,0 0 3px white;
            ">${seg.segment_id}</div>`,
          }),
        }).addTo(segmentLayers);
      } catch { /* invalid coords */ }
    }

    // Render team member positions
    for (const op of data.operators || []) {
      if (!op.lat || !op.lng) continue;

      const callSign = op.call_sign || `${op.first_name} ${op.last_name}`;
      const key      = callSign;

      const icon = L.divIcon({
        className: '',
        html: `<div style="
          background:#29aacc;
          color:white;
          font-size:9px;
          font-weight:bold;
          padding:2px 4px;
          border-radius:3px;
          white-space:nowrap;
          box-shadow:0 0 3px rgba(0,0,0,0.4);
        ">${callSign}</div>`,
        iconAnchor: [0, 0],
      });

      const popupContent =
        `<b>${op.first_name} ${op.last_name}</b><br>` +
        `${op.role} — ${op.division || ''}<br>` +
        `Last seen: ${op.recorded_at?.slice(11, 16) || 'unknown'}`;

      if (teamMarkers[key]) {
        teamMarkers[key].setLatLng([op.lat, op.lng]);
        teamMarkers[key].setPopupContent(popupContent);
      } else {
        teamMarkers[key] = L.marker([op.lat, op.lng], { icon })
          .addTo(map)
          .bindPopup(popupContent);
      }
    }
  } catch (err) {
    console.warn('[Map] Load incident data failed:', err);
  }
}


function segmentColor(status) {
  const colors = {
    unassigned: '#888888',
    assigned:   '#ff8c00',
    cleared:    '#00e676',
    suspended:  '#ff4444',
  };
  return colors[status] || '#888888';
}
