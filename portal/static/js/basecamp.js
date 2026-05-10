/* ============================================================
   SARPack 2.0 — basecamp.js
   BASECAMP module: map, radio log, deployments,
   segments, patients, SOS, drone feed.
   ============================================================ */

'use strict';

// ── BASECAMP state ────────────────────────────────────────────
const BC = {
  map:          null,
  mapDark:      false,
  myMarkers:    {},    // personnel_id → leaflet marker
  segLayers:    [],
  markerLayers: [],
  lkpMarker:    null,
  radioEntries: [],
  deployments:  [],
  activeTab:    'overview',
};

// ── Tile layers ───────────────────────────────────────────────
const TILES = {
  light: L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom: 17,
    attribution: '© OpenTopoMap (CC-BY-SA)',
    subdomains: 'abc',
  }),
  dark: L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    {
      maxZoom: 19,
      attribution: '© CARTO',
      subdomains: 'abcd',
    }
  ),
};

// Segment colors
const SEG_COLORS = {
  unassigned: '#4a4a4a',
  assigned:   '#f26522',
  cleared:    '#2dbd6e',
  suspended:  '#e05252',
};

// ============================================================
//  LOAD BASECAMP
// ============================================================

async function loadBasecamp() {
  const body = document.getElementById('basecamp-body');
  if (!body) return;

  body.innerHTML = renderBasecampShell();

  // Init map after DOM settles
  setTimeout(() => {
    initBasecampMap();
    loadBasecampData();
  }, 100);
}

function renderBasecampShell() {
  return `
  <div style="display:grid;grid-template-columns:1fr 340px;grid-template-rows:auto 1fr;gap:0;height:100%;overflow:hidden">

    <!-- Left: Map -->
    <div style="position:relative;grid-row:1/3">
      <div id="bc-map" style="width:100%;height:100%;min-height:400px"></div>

      <!-- Map controls overlay -->
      <div style="position:absolute;top:10px;left:10px;z-index:400;display:flex;flex-direction:column;gap:6px">
        <button class="btn btn-ghost btn-sm" onclick="toggleMapTheme()"
                style="background:var(--surface);border-color:var(--border-bright)">
          ◑ Map Theme
        </button>
        <button class="btn btn-ghost btn-sm" onclick="centerOnLKP()"
                id="bc-lkp-btn" style="display:none;background:var(--surface);border-color:var(--red)">
          📍 Go to LKP
        </button>
        <button class="btn btn-ghost btn-sm" onclick="openSetLKPModal()"
                style="background:var(--surface);border-color:var(--border-bright)">
          + Set LKP
        </button>
        <button class="btn btn-ghost btn-sm" onclick="openAddMarkerModal()"
                style="background:var(--surface);border-color:var(--border-bright)">
          + Add Marker
        </button>
      </div>

      <!-- Drone feed button (shown when stream available) -->
      <div id="bc-drone-btn" style="display:none;position:absolute;top:10px;right:10px;z-index:400">
        <button class="btn btn-primary btn-sm" onclick="openDroneFeedFromBC()">
          ▶ Drone Feed
        </button>
      </div>
    </div>

    <!-- Right: Side panels -->
    <div style="display:flex;flex-direction:column;border-left:1px solid var(--border);overflow:hidden">

      <!-- Stats bar -->
      <div style="display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--border);flex-shrink:0">
        <div class="stat-card" style="border:none;border-right:1px solid var(--border);padding:12px">
          <div class="stat-label">Deployed</div>
          <div class="stat-value" id="bc-stat-deployed" style="font-size:28px">—</div>
        </div>
        <div class="stat-card" style="border:none;border-right:1px solid var(--border);padding:12px">
          <div class="stat-label">Segments</div>
          <div class="stat-value" id="bc-stat-segments" style="font-size:28px">—</div>
        </div>
        <div class="stat-card" style="border:none;border-right:1px solid var(--border);padding:12px">
          <div class="stat-label">Cleared</div>
          <div class="stat-value ok" id="bc-stat-cleared" style="font-size:28px">—</div>
        </div>
        <div class="stat-card" style="border:none;padding:12px">
          <div class="stat-label">Missed</div>
          <div class="stat-value alert" id="bc-stat-missed" style="font-size:28px">—</div>
        </div>
      </div>

      <!-- Side tab bar -->
      <div style="display:flex;border-bottom:1px solid var(--border);flex-shrink:0">
        <button class="tab-btn active" style="font-size:11px;padding:0 12px;flex:1"
                data-bc-tab="radio" onclick="switchBCTab('radio')">Radio</button>
        <button class="tab-btn" style="font-size:11px;padding:0 12px;flex:1"
                data-bc-tab="deployed" onclick="switchBCTab('deployed')">Deployed</button>
        <button class="tab-btn" style="font-size:11px;padding:0 12px;flex:1"
                data-bc-tab="segments" onclick="switchBCTab('segments')">Segments</button>
        <button class="tab-btn" style="font-size:11px;padding:0 12px;flex:1"
                data-bc-tab="patients" onclick="switchBCTab('patients')">Patients</button>
      </div>

      <!-- Side panel body -->
      <div style="flex:1;overflow-y:auto" id="bc-side-body">
        <div class="loading-state"><div class="loading-spinner"></div></div>
      </div>

      <!-- Radio input -->
      <div id="bc-radio-input" style="border-top:1px solid var(--border);padding:10px;flex-shrink:0">
        <div style="display:flex;gap:6px">
          <input type="text" id="bc-radio-msg" placeholder="Log radio entry..."
                 style="flex:1;font-size:12px;padding:6px 10px"
                 onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendRadioEntry()}"/>
          <input type="text" id="bc-radio-chan" placeholder="Chan"
                 style="width:70px;font-size:12px;padding:6px 8px;font-family:var(--font-mono)"/>
          <button class="btn btn-primary btn-sm" onclick="sendRadioEntry()">Send</button>
        </div>
      </div>
    </div>

  </div>

  <!-- Modals -->
  ${renderSetLKPModal()}
  ${renderAddMarkerModal()}
  ${renderCheckinModal()}
  ${renderSegmentModal()}
  `;
}

// ============================================================
//  MAP INIT
// ============================================================

function initBasecampMap() {
  if (BC.map) return;

  const mapEl = document.getElementById('bc-map');
  if (!mapEl) return;

  BC.map = L.map('bc-map', {
    center:           [40.71, -76.20],
    zoom:             12,
    zoomControl:      false,
    attributionControl: true,
  });

  L.control.zoom({ position: 'bottomleft' }).addTo(BC.map);
  TILES.light.addTo(BC.map);

  // Center on incident coordinates if available
  if (SP.incident?.latitude && SP.incident?.longitude) {
    BC.map.setView([SP.incident.latitude, SP.incident.longitude], 13);
  }

  // Map click for context actions
  BC.map.on('click', onMapClick);
}

function toggleMapTheme() {
  if (!BC.map) return;
  BC.mapDark = !BC.mapDark;

  if (BC.mapDark) {
    BC.map.removeLayer(TILES.light);
    TILES.dark.addTo(BC.map);
  } else {
    BC.map.removeLayer(TILES.dark);
    TILES.light.addTo(BC.map);
  }
}

function onMapClick(e) {
  // Future: context menu for dropping markers
}

// ============================================================
//  DATA LOADING
// ============================================================

async function loadBasecampData() {
  if (!SP.incident) {
    renderNoIncident();
    return;
  }

  try {
    const [inc, deployments, radio, segments] = await Promise.all([
      apiFetch('GET', `/api/incidents/${SP.incident.id}`),
      apiFetch('GET', `/api/deployments/${SP.incident.id}?status=active`),
      apiFetch('GET', `/api/radio/${SP.incident.id}?limit=100`),
      apiFetch('GET', `/api/deployments/${SP.incident.id}/segments`),
    ]);

    BC.deployments   = deployments.deployments || [];
    BC.radioEntries  = radio.entries || [];

    // Update stats
    updateBCStats(inc, radio);

    // Render map layers
    renderSegmentsOnMap(inc.segments || []);
    renderMarkersOnMap(inc.markers || []);
    if (inc.lkp_lat && inc.lkp_lng) {
      renderLKP(inc.lkp_lat, inc.lkp_lng, inc.lkp_notes);
    }

    // Load GPS positions
    loadGPSPositions();

    // Render side panel
    switchBCTab(BC.activeTab);

  } catch (e) {
    console.error('BASECAMP load error:', e);
    toast('Error loading BASECAMP data', 'error');
  }
}

function renderNoIncident() {
  document.getElementById('bc-side-body').innerHTML = `
    <div class="empty-state">
      <span class="empty-icon">⬡</span>
      <div class="empty-title">No Incident Selected</div>
      <div class="empty-sub">Select an active incident from the topbar</div>
      <button class="btn btn-primary" style="margin-top:16px"
              onclick="openNewIncidentModal()">+ Create Incident</button>
    </div>`;
}

function updateBCStats(inc, radio) {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val ?? '—';
  };
  set('bc-stat-deployed', inc.deployed_count);
  set('bc-stat-segments', inc.segment_count);
  set('bc-stat-cleared',  inc.cleared_count);
  set('bc-stat-missed',   radio.missed_count || 0);
}

// ============================================================
//  MAP RENDERING
// ============================================================

function renderSegmentsOnMap(segments) {
  if (!BC.map) return;
  BC.segLayers.forEach(l => BC.map.removeLayer(l));
  BC.segLayers = [];

  segments.forEach(seg => {
    if (!seg.boundary_coords) return;
    try {
      const coords = typeof seg.boundary_coords === 'string'
        ? JSON.parse(seg.boundary_coords)
        : seg.boundary_coords;
      const color  = SEG_COLORS[seg.status] || SEG_COLORS.unassigned;

      const poly = L.polygon(coords, {
        color, fillColor: color, fillOpacity: 0.12, weight: 2,
      }).addTo(BC.map);

      poly.bindPopup(`
        <div style="font-family:var(--font-mono);font-size:12px">
          <strong>${seg.segment_id}</strong> — ${seg.status}<br/>
          ${seg.area_name || ''}<br/>
          POD: ${seg.pod != null ? seg.pod + '%' : '—'}
        </div>`);

      const center = poly.getBounds().getCenter();
      const label  = L.marker(center, {
        icon: L.divIcon({
          className: '',
          html: `<div style="font-size:13px;font-weight:700;color:${color};
                             text-shadow:0 0 4px #000,0 0 4px #000;
                             font-family:'Rajdhani',sans-serif;letter-spacing:1px">
                   ${seg.segment_id}
                 </div>`,
          iconAnchor: [12, 8],
        }),
      }).addTo(BC.map);

      BC.segLayers.push(poly, label);
    } catch {}
  });
}

function renderMarkersOnMap(markers) {
  if (!BC.map) return;
  BC.markerLayers.forEach(l => BC.map.removeLayer(l));
  BC.markerLayers = [];

  const icons = {
    lz:      '🚁',
    dz:      '🎯',
    poi:     '📌',
    hazard:  '⚠',
    camp:    '⛺',
    staging: '🚐',
  };

  markers.forEach(m => {
    const icon  = icons[m.marker_type] || '📌';
    const color = m.marker_type === 'lz' ? '#2dbd6e'
                : m.marker_type === 'dz' ? '#f26522'
                : m.marker_type === 'hazard' ? '#e05252'
                : '#8a8a8a';

    const marker = L.marker([m.latitude, m.longitude], {
      icon: L.divIcon({
        className: '',
        html: `<div style="font-size:20px;filter:drop-shadow(0 0 3px #000)">${icon}</div>
               <div style="font-family:'Share Tech Mono',monospace;font-size:9px;
                           color:${color};text-shadow:0 0 3px #000;white-space:nowrap;
                           text-align:center;margin-top:-2px">
                 ${(m.label || m.marker_type).toUpperCase()}
               </div>`,
        iconAnchor: [12, 10],
      }),
    }).addTo(BC.map);

    marker.bindPopup(`
      <div style="font-family:var(--font-mono);font-size:12px">
        <strong>${m.label || m.marker_type.toUpperCase()}</strong><br/>
        ${m.notes || ''}<br/>
        <small>${m.latitude.toFixed(5)}, ${m.longitude.toFixed(5)}</small>
        <br/><button onclick="removeMapMarker('${m.id}')"
                     style="margin-top:4px;font-size:11px;color:var(--red);
                            background:none;border:none;cursor:pointer;padding:0">
          Remove
        </button>
      </div>`);

    BC.markerLayers.push(marker);
  });
}

function renderLKP(lat, lng, notes) {
  if (!BC.map) return;
  if (BC.lkpMarker) BC.map.removeLayer(BC.lkpMarker);

  BC.lkpMarker = L.marker([lat, lng], {
    icon: L.divIcon({
      className: '',
      html: `<div style="background:#e05252;color:#fff;
                         font-family:'Share Tech Mono',monospace;
                         font-size:10px;font-weight:700;
                         padding:3px 8px;
                         box-shadow:0 2px 8px rgba(0,0,0,0.6);
                         border:1px solid rgba(255,255,255,0.3);
                         white-space:nowrap">
               📍 LKP
             </div>`,
      iconAnchor: [0, 0],
    }),
  }).addTo(BC.map);

  BC.lkpMarker.bindPopup(`
    <div style="font-family:var(--font-mono);font-size:12px">
      <strong>Last Known Position</strong><br/>
      ${lat.toFixed(5)}, ${lng.toFixed(5)}<br/>
      ${notes || ''}
      <br/><button onclick="clearLKP()"
                   style="margin-top:4px;font-size:11px;color:var(--red);
                          background:none;border:none;cursor:pointer;padding:0">
        Clear LKP
      </button>
    </div>`);

  document.getElementById('bc-lkp-btn').style.display = 'block';
}

function centerOnLKP() {
  if (BC.lkpMarker && BC.map) {
    BC.map.setView(BC.lkpMarker.getLatLng(), 15);
    BC.lkpMarker.openPopup();
  }
}

async function loadGPSPositions() {
  if (!SP.incident || !BC.map) return;
  try {
    const data = await apiFetch('GET',
      `/api/radio/gps/${SP.incident.id}/positions`);
    renderOperatorDots(data);
  } catch {}
}

function renderOperatorDots(positions) {
  positions.forEach(pos => {
    const id  = pos.personnel_id;
    const cs  = pos.call_sign || pos.last_name || '?';
    const lat = pos.latitude;
    const lng = pos.longitude;

    const icon = L.divIcon({
      className: '',
      html: `<div style="width:12px;height:12px;background:var(--orange);
                         border:2px solid #000;border-radius:50%;
                         box-shadow:0 0 6px rgba(242,101,34,0.7)"></div>
             <div style="font-family:'Share Tech Mono',monospace;font-size:9px;
                         color:var(--orange);text-shadow:0 0 3px #000;
                         white-space:nowrap;margin-top:1px;text-align:center">
               ${cs}
             </div>`,
      iconAnchor: [6, 6],
    });

    if (BC.myMarkers[id]) {
      BC.myMarkers[id].setLatLng([lat, lng]);
    } else {
      BC.myMarkers[id] = L.marker([lat, lng], { icon })
        .addTo(BC.map)
        .bindPopup(`<div style="font-family:var(--font-mono);font-size:12px">
          <strong>${cs}</strong><br/>
          ${lat.toFixed(5)}, ${lng.toFixed(5)}<br/>
          <small>${pos.recorded_at?.slice(0, 16).replace('T', ' ')}</small>
        </div>`);
    }
  });
}

// Navigate map to coordinates (called by SOS banner click)
window.navigateMapTo = function(lat, lng, zoom = 15) {
  if (!BC.map) return;
  BC.map.setView([lat, lng], zoom);

  // Flash marker
  const flash = L.circleMarker([lat, lng], {
    radius: 20, color: '#e05252', fillColor: '#e05252',
    fillOpacity: 0.3, weight: 3,
  }).addTo(BC.map);
  setTimeout(() => BC.map.removeLayer(flash), 3000);
};

// ============================================================
//  SIDE PANEL TABS
// ============================================================

function switchBCTab(tab) {
  BC.activeTab = tab;
  document.querySelectorAll('[data-bc-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.bcTab === tab);
  });

  const radioInput = document.getElementById('bc-radio-input');
  if (radioInput) {
    radioInput.style.display = tab === 'radio' ? 'block' : 'none';
  }

  switch (tab) {
    case 'radio':    renderRadioLog();    break;
    case 'deployed': renderDeployed();   break;
    case 'segments': renderSegments();   break;
    case 'patients': renderPatients();   break;
  }
}

// ── Radio log ─────────────────────────────────────────────────
function renderRadioLog() {
  const body = document.getElementById('bc-side-body');
  if (!body) return;

  if (!BC.radioEntries.length) {
    body.innerHTML = `<div class="empty-state">
      <span class="empty-icon">◎</span>
      <div class="empty-title">No Radio Entries</div>
    </div>`;
    return;
  }

  body.innerHTML = BC.radioEntries.map(e => {
    const time = (e.logged_at || '').slice(11, 16);
    const cs   = e.call_sign || e.first_name || 'BASE';
    const cls  = e.is_missed ? 'radio-entry missed' : 'radio-entry';
    const prefix = e.is_missed ? '⚠ MISSED — ' : '';
    return `<div class="${cls}">
      <div class="radio-entry-time">${time}</div>
      <div class="radio-entry-callsign">${cs}</div>
      <div class="radio-entry-msg">${prefix}${e.message}</div>
      ${e.channel ? `<div class="radio-entry-channel">${e.channel}</div>` : ''}
    </div>`;
  }).join('');
}

async function sendRadioEntry() {
  const msgEl  = document.getElementById('bc-radio-msg');
  const chanEl = document.getElementById('bc-radio-chan');
  const msg    = msgEl?.value.trim();

  if (!msg || !SP.incident) return;

  try {
    const entry = await apiFetch('POST', `/api/radio/${SP.incident.id}`, {
      message: msg,
      channel: chanEl?.value.trim() || null,
      source:  'basecamp',
    });

    BC.radioEntries.unshift(entry);
    renderRadioLog();
    msgEl.value = '';
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Deployed personnel ────────────────────────────────────────
function renderDeployed() {
  const body = document.getElementById('bc-side-body');
  if (!body) return;

  if (!BC.deployments.length) {
    body.innerHTML = `<div class="empty-state">
      <span class="empty-icon">◈</span>
      <div class="empty-title">No Personnel Deployed</div>
      <button class="btn btn-primary" style="margin-top:16px"
              onclick="openCheckinModal()">+ Check In</button>
    </div>`;
    return;
  }

  body.innerHTML = `
    <div style="padding:8px;border-bottom:1px solid var(--border)">
      <button class="btn btn-primary btn-sm w-full" onclick="openCheckinModal()">
        + Check In Personnel
      </button>
    </div>
    ${BC.deployments.map(d => `
      <div style="display:flex;align-items:center;gap:10px;
                  padding:10px 14px;border-bottom:1px solid var(--border);
                  transition:background 0.15s"
           onmouseover="this.style.background='var(--surface-hover)'"
           onmouseout="this.style.background=''">
        <div style="flex:1">
          <div style="font-family:var(--font-display);font-size:14px;
                      font-weight:600;color:var(--white)">
            ${d.call_sign || d.first_name + ' ' + d.last_name}
          </div>
          <div style="font-family:var(--font-mono);font-size:10px;
                      color:var(--text-muted);margin-top:2px">
            ${d.role} · ${d.division || 'Unassigned'}
          </div>
          <div style="display:flex;gap:4px;margin-top:4px;flex-wrap:wrap">
            ${(d.cert_types || []).map(c =>
              `<span class="badge badge-muted">${c}</span>`
            ).join('')}
          </div>
        </div>
        <button class="btn btn-ghost btn-sm"
                onclick="checkoutPersonnel('${d.id}')"
                style="font-size:10px;padding:4px 8px">
          Out
        </button>
      </div>`).join('')}`;
}

async function checkoutPersonnel(deploymentId) {
  if (!SP.incident) return;
  if (!confirm('Check this operator out of the incident?')) return;
  try {
    await apiFetch('POST',
      `/api/deployments/${SP.incident.id}/checkout/${deploymentId}`);
    BC.deployments = BC.deployments.filter(d => d.id !== deploymentId);
    renderDeployed();
    toast('Operator checked out', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Search segments ───────────────────────────────────────────
function renderSegments() {
  const body = document.getElementById('bc-side-body');
  if (!body || !SP.incident) return;

  apiFetch('GET', `/api/deployments/${SP.incident.id}/segments`)
    .then(segs => {
      if (!segs.length) {
        body.innerHTML = `<div class="empty-state">
          <span class="empty-icon">◇</span>
          <div class="empty-title">No Segments</div>
          <button class="btn btn-primary" style="margin-top:16px"
                  onclick="openSegmentModal()">+ Add Segment</button>
        </div>`;
        return;
      }

      body.innerHTML = `
        <div style="padding:8px;border-bottom:1px solid var(--border)">
          <button class="btn btn-primary btn-sm w-full"
                  onclick="openSegmentModal()">+ Add Segment</button>
        </div>
        ${segs.map(s => {
          const color = SEG_COLORS[s.status] || SEG_COLORS.unassigned;
          return `<div style="display:flex;align-items:center;gap:10px;
                              padding:10px 14px;border-bottom:1px solid var(--border);
                              border-left:3px solid ${color}">
            <div style="flex:1">
              <div style="display:flex;align-items:center;gap:8px">
                <span style="font-family:var(--font-display);font-size:15px;
                             font-weight:700;color:var(--white)">${s.segment_id}</span>
                <span class="badge" style="color:${color};border-color:${color}">
                  ${s.status}
                </span>
              </div>
              <div style="font-family:var(--font-mono);font-size:10px;
                          color:var(--text-muted);margin-top:2px">
                ${s.area_name || ''}
                ${s.pod != null ? ` · POD ${s.pod}%` : ''}
                ${s.assigned_to ? ` · ${s.assigned_to}` : ''}
              </div>
            </div>
            <select style="width:90px;font-size:11px;padding:4px 6px"
                    onchange="updateSegmentStatus('${s.id}', this.value)">
              <option value="unassigned" ${s.status==='unassigned'?'selected':''}>Unassigned</option>
              <option value="assigned"   ${s.status==='assigned'  ?'selected':''}>Assigned</option>
              <option value="cleared"    ${s.status==='cleared'   ?'selected':''}>Cleared</option>
              <option value="suspended"  ${s.status==='suspended' ?'selected':''}>Suspended</option>
            </select>
          </div>`;
        }).join('')}`;
    })
    .catch(() => {});
}

async function updateSegmentStatus(segId, status) {
  if (!SP.incident) return;
  try {
    await apiFetch('PATCH',
      `/api/deployments/${SP.incident.id}/segments/${segId}`,
      { status });
    loadBasecampData();
    toast(`Segment updated: ${status}`, 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

// ── Patients ──────────────────────────────────────────────────
function renderPatients() {
  const body = document.getElementById('bc-side-body');
  if (!body || !SP.incident) return;

  apiFetch('GET', `/api/patients/incident/${SP.incident.id}/summary`)
    .then(data => {
      const pts = data.patients || [];
      if (!pts.length) {
        body.innerHTML = `<div class="empty-state">
          <span class="empty-icon">✚</span>
          <div class="empty-title">No Patient Reports</div>
        </div>`;
        return;
      }

      const sevColor = {
        minor: 'var(--green)', moderate: 'var(--amber)',
        serious: 'var(--orange)', critical: 'var(--red)',
      };

      body.innerHTML = pts.map(p => `
        <div style="padding:12px 14px;border-bottom:1px solid var(--border);
                    border-left:3px solid ${sevColor[p.severity] || 'var(--gray)'}">
          <div style="display:flex;align-items:center;justify-content:space-between">
            <div style="font-family:var(--font-display);font-size:14px;
                        font-weight:600;color:var(--white)">
              ${p.patient_name || 'Unknown Patient'}
            </div>
            <span class="badge" style="color:${sevColor[p.severity]||'var(--gray)'};
                                       border-color:${sevColor[p.severity]||'var(--gray)'}">
              ${p.severity || 'Unknown'}
            </span>
          </div>
          <div style="font-family:var(--font-mono);font-size:10px;
                      color:var(--text-muted);margin-top:3px">
            ${p.chief_complaint || '—'} · ${p.loc || '—'}
          </div>
          <div style="font-family:var(--font-mono);font-size:10px;
                      color:var(--text-muted);margin-top:2px">
            ${(p.assessed_at || '').slice(0, 16).replace('T', ' ')}
            ${p.reporter_callsign ? ' · ' + p.reporter_callsign : ''}
          </div>
        </div>`).join('');
    })
    .catch(() => {});
}

// ============================================================
//  LKP MANAGEMENT
// ============================================================

function renderSetLKPModal() {
  return `
  <div class="modal-overlay" id="bc-modal-lkp">
    <div class="modal" style="max-width:400px">
      <div class="modal-header">
        <div class="modal-title">Set LKP</div>
        <button class="modal-close" onclick="closeModal('bc-modal-lkp')">✕</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label class="form-label required">Latitude</label>
          <input type="number" id="lkp-lat" step="0.00001" placeholder="e.g. 40.64230"/>
        </div>
        <div class="form-group">
          <label class="form-label required">Longitude</label>
          <input type="number" id="lkp-lng" step="0.00001" placeholder="e.g. -75.99460"/>
        </div>
        <div class="form-group">
          <label class="form-label">Notes</label>
          <input type="text" id="lkp-notes" placeholder="e.g. North trail junction"/>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" onclick="closeModal('bc-modal-lkp')">Cancel</button>
        <button class="btn btn-primary" onclick="setLKP()">Set LKP</button>
      </div>
    </div>
  </div>`;
}

function openSetLKPModal() { openModal('bc-modal-lkp'); }

async function setLKP() {
  if (!SP.incident) return;
  const lat   = parseFloat(document.getElementById('lkp-lat').value);
  const lng   = parseFloat(document.getElementById('lkp-lng').value);
  const notes = document.getElementById('lkp-notes').value.trim();

  if (isNaN(lat) || isNaN(lng)) { toast('Valid coordinates required', 'error'); return; }

  try {
    await apiFetch('POST', `/api/incidents/${SP.incident.id}/lkp`,
      { latitude: lat, longitude: lng, notes });
    renderLKP(lat, lng, notes);
    closeModal('bc-modal-lkp');
    toast('LKP set', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function clearLKP() {
  if (!SP.incident || !confirm('Clear the LKP?')) return;
  try {
    await apiFetch('DELETE', `/api/incidents/${SP.incident.id}/lkp`);
    if (BC.lkpMarker) { BC.map.removeLayer(BC.lkpMarker); BC.lkpMarker = null; }
    document.getElementById('bc-lkp-btn').style.display = 'none';
    toast('LKP cleared', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

async function removeMapMarker(markerId) {
  if (!SP.incident) return;
  try {
    await apiFetch('DELETE',
      `/api/incidents/${SP.incident.id}/markers/${markerId}`);
    loadBasecampData();
    toast('Marker removed', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
//  ADD MARKER MODAL
// ============================================================

function renderAddMarkerModal() {
  return `
  <div class="modal-overlay" id="bc-modal-marker">
    <div class="modal" style="max-width:420px">
      <div class="modal-header">
        <div class="modal-title">Add Map Marker</div>
        <button class="modal-close" onclick="closeModal('bc-modal-marker')">✕</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label class="form-label required">Marker Type</label>
          <select id="marker-type">
            <option value="lz">🚁 Landing Zone (LZ)</option>
            <option value="dz">🎯 Drop Zone (DZ)</option>
            <option value="poi">📌 Point of Interest</option>
            <option value="hazard">⚠ Hazard</option>
            <option value="camp">⛺ Camp / Base</option>
            <option value="staging">🚐 Staging Area</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Label</label>
          <input type="text" id="marker-label" placeholder="e.g. LZ-Alpha"/>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label class="form-label required">Latitude</label>
            <input type="number" id="marker-lat" step="0.00001"/>
          </div>
          <div class="form-group">
            <label class="form-label required">Longitude</label>
            <input type="number" id="marker-lng" step="0.00001"/>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Notes</label>
          <input type="text" id="marker-notes" placeholder="Additional info..."/>
        </div>
        <div class="form-group" id="marker-dz-target-group" style="display:none">
          <label class="form-label">DZ Target Operator</label>
          <select id="marker-target-device">
            <option value="">Select operator...</option>
          </select>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" onclick="closeModal('bc-modal-marker')">Cancel</button>
        <button class="btn btn-primary" onclick="addMarker()">Add Marker</button>
      </div>
    </div>
  </div>`;
}

function openAddMarkerModal() {
  // Populate DZ target dropdown with deployed personnel
  const sel = document.getElementById('marker-target-device');
  if (sel) {
    sel.innerHTML = '<option value="">Select operator...</option>' +
      BC.deployments.map(d =>
        `<option value="${d.personnel_id}">
          ${d.call_sign || d.first_name + ' ' + d.last_name}
        </option>`
      ).join('');
  }

  document.getElementById('marker-type').onchange = function() {
    const dg = document.getElementById('marker-dz-target-group');
    if (dg) dg.style.display = this.value === 'dz' ? 'block' : 'none';
  };

  openModal('bc-modal-marker');
}

async function addMarker() {
  if (!SP.incident) return;
  const type   = document.getElementById('marker-type').value;
  const label  = document.getElementById('marker-label').value.trim();
  const lat    = parseFloat(document.getElementById('marker-lat').value);
  const lng    = parseFloat(document.getElementById('marker-lng').value);
  const notes  = document.getElementById('marker-notes').value.trim();
  const target = document.getElementById('marker-target-device')?.value;

  if (isNaN(lat) || isNaN(lng)) { toast('Valid coordinates required', 'error'); return; }

  try {
    await apiFetch('POST', `/api/incidents/${SP.incident.id}/markers`, {
      marker_type: type, label, latitude: lat, longitude: lng,
      notes, target_device: target || undefined,
    });
    closeModal('bc-modal-marker');
    loadBasecampData();
    toast('Marker added', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
//  CHECK-IN MODAL
// ============================================================

function renderCheckinModal() {
  return `
  <div class="modal-overlay" id="bc-modal-checkin">
    <div class="modal" style="max-width:460px">
      <div class="modal-header">
        <div class="modal-title">Check In Personnel</div>
        <button class="modal-close" onclick="closeModal('bc-modal-checkin')">✕</button>
      </div>
      <div class="modal-body">
        <div class="form-group">
          <label class="form-label required">Personnel</label>
          <select id="checkin-personnel-id">
            <option value="">Select personnel...</option>
          </select>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label class="form-label">Role</label>
            <select id="checkin-role">
              <option value="field_op">Field Operator</option>
              <option value="ic">Incident Commander</option>
              <option value="logistics">Logistics</option>
              <option value="medical">Medical</option>
              <option value="observer">Observer</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Division</label>
            <input type="text" id="checkin-division" placeholder="e.g. Alpha"/>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Team</label>
          <input type="text" id="checkin-team" placeholder="e.g. Alpha-1"/>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" onclick="closeModal('bc-modal-checkin')">Cancel</button>
        <button class="btn btn-primary" onclick="checkinPersonnel()">Check In</button>
      </div>
    </div>
  </div>`;
}

async function openCheckinModal() {
  // Load available personnel
  try {
    const data = await apiFetch('GET', '/api/personnel/?is_active=1&limit=200');
    const sel  = document.getElementById('checkin-personnel-id');
    const deployed = new Set(BC.deployments.map(d => d.personnel_id));
    sel.innerHTML  = '<option value="">Select personnel...</option>' +
      (data.personnel || [])
        .filter(p => !deployed.has(p.id))
        .map(p => `<option value="${p.id}">
          ${p.call_sign ? p.call_sign + ' — ' : ''}${p.first_name} ${p.last_name}
        </option>`).join('');
  } catch {}

  openModal('bc-modal-checkin');
}

async function checkinPersonnel() {
  if (!SP.incident) return;
  const pid  = document.getElementById('checkin-personnel-id').value;
  const role = document.getElementById('checkin-role').value;
  const div  = document.getElementById('checkin-division').value.trim();
  const team = document.getElementById('checkin-team').value.trim();

  if (!pid) { toast('Select a personnel member', 'error'); return; }

  try {
    await apiFetch('POST', `/api/deployments/${SP.incident.id}/checkin`, {
      personnel_id: pid, role,
      division: div || undefined,
      team:     team || undefined,
    });
    closeModal('bc-modal-checkin');
    loadBasecampData();
    toast('Personnel checked in', 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
//  SEGMENT MODAL
// ============================================================

function renderSegmentModal() {
  return `
  <div class="modal-overlay" id="bc-modal-segment">
    <div class="modal" style="max-width:420px">
      <div class="modal-header">
        <div class="modal-title">Add Search Segment</div>
        <button class="modal-close" onclick="closeModal('bc-modal-segment')">✕</button>
      </div>
      <div class="modal-body">
        <div class="grid-2">
          <div class="form-group">
            <label class="form-label required">Segment ID</label>
            <input type="text" id="seg-id" placeholder="e.g. A1"/>
          </div>
          <div class="form-group">
            <label class="form-label">Area Name</label>
            <input type="text" id="seg-area" placeholder="e.g. Alpha-1"/>
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">Description</label>
          <input type="text" id="seg-desc" placeholder="Brief description..."/>
        </div>
        <div class="grid-2">
          <div class="form-group">
            <label class="form-label">Assigned To</label>
            <input type="text" id="seg-assigned" placeholder="Division or team"/>
          </div>
          <div class="form-group">
            <label class="form-label">POD Target (%)</label>
            <input type="number" id="seg-pod" min="0" max="100" placeholder="e.g. 80"/>
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-ghost" onclick="closeModal('bc-modal-segment')">Cancel</button>
        <button class="btn btn-primary" onclick="createSegment()">Add Segment</button>
      </div>
    </div>
  </div>`;
}

function openSegmentModal() { openModal('bc-modal-segment'); }

async function createSegment() {
  if (!SP.incident) return;
  const sid      = document.getElementById('seg-id').value.trim();
  const area     = document.getElementById('seg-area').value.trim();
  const desc     = document.getElementById('seg-desc').value.trim();
  const assigned = document.getElementById('seg-assigned').value.trim();
  const pod      = parseFloat(document.getElementById('seg-pod').value);

  if (!sid) { toast('Segment ID is required', 'error'); return; }

  try {
    await apiFetch('POST', `/api/deployments/${SP.incident.id}/segments`, {
      segment_id:  sid,
      area_name:   area   || undefined,
      description: desc   || undefined,
      assigned_to: assigned || undefined,
      pod:         isNaN(pod) ? undefined : pod,
    });
    closeModal('bc-modal-segment');
    loadBasecampData();
    renderSegments();
    toast(`Segment ${sid} created`, 'success');
  } catch (e) { toast(e.message, 'error'); }
}

// ============================================================
//  SOCKET EVENT HANDLERS
// ============================================================

window.onPositionUpdate = function(data) {
  if (!BC.map || data.incident_id !== SP.incident?.id) return;
  const id  = data.personnel_id;
  const lat = data.latitude;
  const lng = data.longitude;

  if (BC.myMarkers[id]) {
    BC.myMarkers[id].setLatLng([lat, lng]);
  } else {
    loadGPSPositions();
  }
};

window.onLKPUpdated = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  renderLKP(data.lkp_lat, data.lkp_lng, data.lkp_notes);
};

window.onLKPCleared = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  if (BC.lkpMarker) { BC.map?.removeLayer(BC.lkpMarker); BC.lkpMarker = null; }
  document.getElementById('bc-lkp-btn').style.display = 'none';
};

window.onMarkerAdded = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  if (data.marker) renderMarkersOnMap([...BC.markerLayers, data.marker]);
};

window.onMarkerRemoved = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  loadBasecampData();
};

window.onRadioEntry = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  BC.radioEntries.unshift(data.entry);
  if (BC.activeTab === 'radio') renderRadioLog();
};

window.onMissedCheckin = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  BC.radioEntries.unshift(data.entry);
  if (BC.activeTab === 'radio') renderRadioLog();
  // Update missed stat
  const el = document.getElementById('bc-stat-missed');
  if (el) el.textContent = parseInt(el.textContent || 0) + 1;
};

window.onPersonnelCheckin = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  BC.deployments.push(data.deployment);
  if (BC.activeTab === 'deployed') renderDeployed();
  const el = document.getElementById('bc-stat-deployed');
  if (el) el.textContent = BC.deployments.length;
};

window.onPersonnelCheckout = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  BC.deployments = BC.deployments.filter(d => d.id !== data.deployment_id);
  if (BC.activeTab === 'deployed') renderDeployed();
};

window.onSegmentUpdated = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  loadBasecampData();
};

window.onPatientReported = function(data) {
  if (data.incident_id !== SP.incident?.id) return;
  if (BC.activeTab === 'patients') renderPatients();
};

window.onDZTarget = function(data) {
  // BASECAMP receives DZ targets sent TO specific operators
  // Show as a notification to the IC
  toast(`DZ target sent to operator`, 'info');
};

// Drone feed
function openDroneFeedFromBC() {
  const btn = document.getElementById('bc-drone-btn');
  const assetId = btn?.dataset.assetId;
  if (assetId) openDroneFeed(assetId);
}

window.onDroneStreamReady_BC = function(data) {
  const btn = document.getElementById('bc-drone-btn');
  if (btn) {
    btn.style.display    = 'block';
    btn.dataset.assetId  = data.asset_id;
  }
};