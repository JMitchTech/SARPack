/**
 * SARPack BASECAMP — basecamp.js
 * Full application controller. Handles auth, screen routing,
 * real-time SocketIO updates, and all API interactions.
 */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  token:      null,
  user:       null,
  incidentId: null,
  incidents:  [],
  screen:     'overview',
  map:        null,
  markers:    {},       // {personnel_id: L.marker}
  segments:   {},       // {segment_id: L.polygon}
  socket:     null,
  deployments: [],
  refreshTimer: null,
};

const PORT = window.location.port || 6000;
const API  = '';   // same origin

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  // Check for existing session
  const saved = sessionStorage.getItem('sarpack_token');
  if (saved) {
    state.token = saved;
    const ok = await verifyToken();
    if (ok) {
      hideLogin();
      await boot();
      return;
    }
  }
  showLogin();
});

async function boot() {
  startClock();
  await loadUser();
  await loadIncidents();
  connectSocket();
  startAutoRefresh();
  await refreshOverview();
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------

function startClock() {
  function tick() {
    const now = new Date();
    document.getElementById('op-clock').textContent =
      now.toLocaleTimeString('en-US', { hour12: false });
  }
  tick();
  setInterval(tick, 1000);
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

async function handleLogin() {
  const username = document.getElementById('login-username').value.trim();
  const password = document.getElementById('login-password').value;
  const errEl    = document.getElementById('login-error');
  const btn      = document.getElementById('login-btn');

  errEl.textContent = '';
  if (!username || !password) {
    errEl.textContent = 'Username and password are required.';
    return;
  }

  btn.disabled    = true;
  btn.textContent = 'Signing in...';

  try {
    const r = await fetch(`${API}/api/users/login`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ username, password }),
    });
    const data = await r.json();
    if (!r.ok) {
      errEl.textContent = data.error || 'Login failed.';
      btn.disabled = false; btn.textContent = 'Sign in';
      return;
    }
    state.token = data.token;
    state.user  = data;
    sessionStorage.setItem('sarpack_token', data.token);
    hideLogin();
    await boot();
  } catch {
    errEl.textContent = 'Connection error.';
    btn.disabled = false; btn.textContent = 'Sign in';
  }
}

window.handleLogin = handleLogin;

async function handleLogout() {
  try { await api('POST', '/api/users/logout'); } catch {}
  sessionStorage.removeItem('sarpack_token');
  state.token = null;
  location.reload();
}
window.handleLogout = handleLogout;

async function verifyToken() {
  try {
    const r = await fetch(`${API}/api/users/me`, {
      headers: { 'Authorization': `Bearer ${state.token}` },
    });
    if (r.ok) {
      state.user = await r.json();
      return true;
    }
  } catch {}
  return false;
}

function showLogin()  { document.getElementById('login-overlay').style.display = 'flex'; }
function hideLogin()  { document.getElementById('login-overlay').style.display = 'none'; }

async function loadUser() {
  if (!state.user) return;
  document.getElementById('user-name').textContent = state.user.username || '—';
  document.getElementById('user-role').textContent = state.user.role    || '—';
}

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = {
    method,
    headers: {
      'Authorization': `Bearer ${state.token}`,
      'Content-Type':  'application/json',
    },
  };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(`${API}${path}`, opts);
  return r;
}

// ---------------------------------------------------------------------------
// Incident management
// ---------------------------------------------------------------------------

async function loadIncidents() {
  try {
    const r    = await api('GET', '/api/incidents/?status=active');
    const data = await r.json();
    state.incidents = data;

    const dropdown = document.getElementById('incident-dropdown');
    dropdown.innerHTML = '';

    if (!data.length) {
      document.getElementById('current-incident-label').textContent = 'No active incidents';
      dropdown.innerHTML = '<div class="incident-dropdown-item" style="color:var(--gray-400)">No active incidents</div>';
      return;
    }

    data.forEach(inc => {
      const el = document.createElement('div');
      el.className = 'incident-dropdown-item';
      el.innerHTML = `
        <span>${inc.incident_name}</span>
        <span style="font-size:11px;color:var(--gray-400);font-family:var(--font-mono)">${inc.incident_number}</span>
      `;
      el.onclick = () => selectIncident(inc.id, inc.incident_name, inc.incident_number);
      dropdown.appendChild(el);
    });

    // Auto-select first
    selectIncident(data[0].id, data[0].incident_name, data[0].incident_number);
  } catch (e) {
    console.error('Load incidents failed:', e);
  }
}

function selectIncident(id, name, number) {
  state.incidentId = id;
  document.getElementById('current-incident-label').textContent = `${number} — ${name}`;
  closeIncidentDropdown();
  refreshOverview();
}

// Incident dropdown toggle
document.getElementById('incident-selector').addEventListener('click', e => {
  document.getElementById('incident-selector').classList.toggle('open');
});
document.addEventListener('click', e => {
  if (!e.target.closest('#incident-selector')) closeIncidentDropdown();
});
function closeIncidentDropdown() {
  document.getElementById('incident-selector').classList.remove('open');
}

async function showNewIncidentModal() {
  document.getElementById('modal-new-incident').style.display = 'flex';
}
window.showNewIncidentModal = showNewIncidentModal;

async function submitNewIncident() {
  const name   = document.getElementById('new-incident-name').value.trim();
  const type   = document.getElementById('new-incident-type').value;
  const county = document.getElementById('new-incident-county').value.trim();
  const lat    = parseFloat(document.getElementById('new-incident-lat').value) || null;
  const lng    = parseFloat(document.getElementById('new-incident-lng').value) || null;
  const notes  = document.getElementById('new-incident-notes').value.trim();
  const errEl  = document.getElementById('new-incident-error');

  errEl.textContent = '';
  if (!name) { errEl.textContent = 'Incident name is required.'; return; }

  const r = await api('POST', '/api/incidents/', { incident_name: name, incident_type: type, county, state: 'PA', lat, lng, notes });
  const data = await r.json();
  if (!r.ok) { errEl.textContent = data.error || 'Failed to create incident.'; return; }

  closeModal('modal-new-incident');
  await loadIncidents();
  selectIncident(data.id, name, data.incident_number);
}
window.submitNewIncident = submitNewIncident;

// ---------------------------------------------------------------------------
// Screen routing
// ---------------------------------------------------------------------------

function switchScreen(name) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));

  document.getElementById(`screen-${name}`).classList.add('active');
  document.querySelector(`[data-screen="${name}"]`)?.classList.add('active');
  state.screen = name;

  if (name === 'overview')     refreshOverview();
  if (name === 'map')          initMap();
  if (name === 'deployments')  loadDeployments();
  if (name === 'radio')        loadRadioLog();
  if (name === 'logbook')      { /* wait for user to click compile */ }
}
window.switchScreen = switchScreen;

// ---------------------------------------------------------------------------
// Overview
// ---------------------------------------------------------------------------

async function refreshOverview() {
  if (!state.incidentId) return;
  try {
    const [statsR, oncallR, radioR, segR] = await Promise.all([
      api('GET', `/api/dashboard/incident/${state.incidentId}`),
      api('GET', '/api/dashboard/oncall'),
      api('GET', `/api/radio/${state.incidentId}?limit=8`),
      api('GET', `/api/map/${state.incidentId}/segments`),
    ]);

    const stats  = await statsR.json();
    const oncall = await oncallR.json();
    const radio  = await radioR.json();
    const segs   = await segR.json();

    // Stat cards
    document.getElementById('stat-deployed').textContent = stats.deployments?.active ?? '—';
    document.getElementById('stat-segments').textContent = stats.segments?.total     ?? '—';
    document.getElementById('stat-cleared').textContent  = stats.segments?.cleared   ?? '—';
    document.getElementById('stat-missed').textContent   = stats.radio?.missed_checkins ?? '—';
    document.getElementById('stat-radio').textContent    = stats.radio?.total_entries   ?? '—';

    // Alert card color
    const missed = stats.radio?.missed_checkins ?? 0;
    const missedCard = document.getElementById('stat-missed-card');
    missedCard.className = missed > 0 ? 'stat-card stat-card-alert' : 'stat-card';

    // On-call roster
    renderOncall(oncall);

    // Radio preview
    renderRadioPreview(radio);

    // Segments
    renderSegments(segs);

  } catch (e) { console.error('refreshOverview error:', e); }
}
window.refreshOverview = refreshOverview;

function renderOncall(list) {
  const el = document.getElementById('oncall-list');
  document.getElementById('oncall-count').textContent = list.length;
  if (!list.length) { el.innerHTML = '<div class="empty-state">No operators on-call right now.</div>'; return; }
  el.innerHTML = list.map(op => `
    <div class="oncall-row">
      <div>
        <span class="oncall-name">${op.first_name} ${op.last_name}</span>
        <span class="oncall-call-sign">${op.call_sign || ''}</span>
      </div>
      <div>
        ${op.currently_deployed_to
          ? '<span class="oncall-deployed">Deployed</span>'
          : `<span class="oncall-shift">${op.shift_name || ''}</span>`}
      </div>
    </div>
  `).join('');
}

function renderRadioPreview(entries) {
  const el = document.getElementById('overview-radio-list');
  if (!entries.length) { el.innerHTML = '<div class="empty-state">No radio entries yet.</div>'; return; }
  el.innerHTML = entries.slice(0, 6).map(e => `
    <div class="radio-entry ${e.is_missed_checkin ? 'radio-entry-missed' : ''}">
      <span class="radio-time">${fmtTime(e.logged_at)}</span>
      <span class="radio-callsign">${e.call_sign || '—'}</span>
      <span class="radio-msg">${e.message}</span>
      ${e.is_missed_checkin ? '<span class="radio-missed-flag">MISSED</span>' : ''}
    </div>
  `).join('');
}

function renderSegments(segs) {
  const el = document.getElementById('segment-list');
  if (!segs.length) { el.innerHTML = '<div class="empty-state">No segments defined.</div>'; return; }
  el.innerHTML = segs.map(s => `
    <div class="segment-row">
      <span class="segment-id">${s.segment_id}</span>
      <span class="segment-team">${s.assigned_team || 'Unassigned'}</span>
      <span class="segment-pod">POD ${Math.round((s.probability_of_detection || 0) * 100)}%</span>
      <span class="seg-status-pill seg-${s.status}">${s.status}</span>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------

function initMap() {
  if (state.map) { refreshMap(); return; }

  state.map = L.map('map', { center: [40.71, -76.20], zoom: 12 });

  L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
    maxZoom: 17,
    attribution: '© OpenTopoMap (CC-BY-SA)',
    subdomains: 'abc',
  }).addTo(state.map);

  refreshMap();
}

async function refreshMap() {
  if (!state.map || !state.incidentId) return;

  try {
    const [posR, segR] = await Promise.all([
      api('GET', `/api/map/${state.incidentId}/positions`),
      api('GET', `/api/map/${state.incidentId}/segments`),
    ]);
    const positions = await posR.json();
    const segments  = await segR.json();

    updateMapPositions(positions);
    updateMapSegments(segments);
    updateMapSidebar(positions, segments);
  } catch (e) { console.error('refreshMap error:', e); }
}
window.refreshMap = refreshMap;

function updateMapPositions(positions) {
  const operatorIcon = (callSign) => L.divIcon({
    className: '',
    html: `<div style="background:#2d5a27;color:#fff;font-size:9px;font-weight:600;padding:2px 5px;border-radius:3px;white-space:nowrap;box-shadow:0 1px 3px rgba(0,0,0,0.3)">${callSign || '?'}</div>`,
    iconAnchor: [0, 0],
  });

  positions.forEach(pos => {
    if (!pos.lat || !pos.lng) return;
    const key = pos.personnel_id;
    if (state.markers[key]) {
      state.markers[key].setLatLng([pos.lat, pos.lng]);
    } else {
      state.markers[key] = L.marker([pos.lat, pos.lng], {
        icon: operatorIcon(pos.call_sign),
      }).addTo(state.map).bindPopup(
        `<b>${pos.first_name} ${pos.last_name}</b><br>${pos.call_sign}<br>Last seen: ${fmtTime(pos.recorded_at)}`
      );
    }
  });
}

const SEG_COLORS = { unassigned: '#888', assigned: '#d97706', cleared: '#2d5a27', suspended: '#b91c1c' };

function updateMapSegments(segments) {
  // Remove old segment layers
  Object.values(state.segments).forEach(l => state.map.removeLayer(l));
  state.segments = {};

  segments.forEach(seg => {
    if (!seg.boundary_coords) return;
    try {
      const coords = JSON.parse(seg.boundary_coords);
      const color  = SEG_COLORS[seg.status] || '#888';
      const poly = L.polygon(coords, { color, fillColor: color, fillOpacity: 0.15, weight: 2 })
        .addTo(state.map)
        .bindPopup(`<b>Segment ${seg.segment_id}</b><br>Team: ${seg.assigned_team || 'Unassigned'}<br>Status: ${seg.status}`);

      const center = poly.getBounds().getCenter();
      L.marker(center, { icon: L.divIcon({
        className: '',
        html: `<div style="font-size:11px;font-weight:700;color:${color};text-shadow:0 0 3px #fff,0 0 3px #fff">${seg.segment_id}</div>`,
      }) }).addTo(state.map);

      state.segments[seg.segment_id] = poly;
    } catch {}
  });
}

function updateMapSidebar(positions, segments) {
  const opEl = document.getElementById('map-operator-list');
  opEl.innerHTML = positions.map(p => `
    <div class="map-op-row" onclick="if(window.sarpackMap)sarpackMap.flyTo([${p.lat},${p.lng}],16)">
      <div class="map-op-dot ${p.lat ? '' : 'no-gps'}"></div>
      <div>
        <div class="map-op-name">${p.first_name} ${p.last_name}</div>
        <div class="map-op-cs">${p.call_sign || ''}</div>
      </div>
    </div>
  `).join('') || '<div style="padding:8px;font-size:12px;color:var(--gray-400)">No positions yet</div>';

  const segEl = document.getElementById('map-segment-list');
  segEl.innerHTML = segments.map(s => `
    <div class="map-op-row">
      <div class="map-op-dot" style="background:${SEG_COLORS[s.status]||'#888'}"></div>
      <div>
        <div class="map-op-name">${s.segment_id}</div>
        <div class="map-op-cs">${s.assigned_team || 'Unassigned'} · ${s.status}</div>
      </div>
    </div>
  `).join('') || '<div style="padding:8px;font-size:12px;color:var(--gray-400)">No segments</div>';
}

// ---------------------------------------------------------------------------
// Deployments
// ---------------------------------------------------------------------------

let _allDeployments = [];
let _activeFilter   = 'active';

async function loadDeployments() {
  if (!state.incidentId) return;
  try {
    const r = await api('GET', `/api/deployments/${state.incidentId}`);
    _allDeployments = await r.json();
    renderDeployments(_allDeployments);
  } catch (e) { console.error('loadDeployments error:', e); }
}

function renderDeployments(list) {
  const tbody = document.getElementById('deployments-tbody');
  const filtered = _activeFilter === 'all' ? list : list.filter(d => d.status === _activeFilter);

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-cell">No ${_activeFilter} deployments.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(d => `
    <tr>
      <td style="font-weight:500">${d.first_name} ${d.last_name}</td>
      <td><span style="font-family:var(--font-mono);font-size:12px">${d.call_sign || '—'}</span></td>
      <td>${d.role || '—'}</td>
      <td>${d.division || '—'}</td>
      <td>${d.team || '—'}</td>
      <td style="font-family:var(--font-mono);font-size:12px">${fmtDateTime(d.checked_in_at)}</td>
      <td><span class="status-pill status-${d.status.replace('_','-')}">${d.status.replace('_',' ')}</span></td>
      <td>${d.status === 'active'
        ? `<button class="btn-checkout" onclick="checkout('${d.deployment_id}','${d.first_name} ${d.last_name}')">Check out</button>`
        : ''
      }</td>
    </tr>
  `).join('');
}

function filterDeployments(filter, btn) {
  _activeFilter = filter;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderDeployments(_allDeployments);
}
window.filterDeployments = filterDeployments;

function searchDeployments() {
  const q = document.getElementById('deploy-search').value.toLowerCase();
  if (!q) { renderDeployments(_allDeployments); return; }
  renderDeployments(_allDeployments.filter(d =>
    `${d.first_name} ${d.last_name}`.toLowerCase().includes(q) ||
    (d.call_sign || '').toLowerCase().includes(q)
  ));
}
window.searchDeployments = searchDeployments;

async function checkout(deploymentId, name) {
  if (!confirm(`Check out ${name}?`)) return;
  const r = await api('POST', `/api/deployments/${state.incidentId}/checkout/${deploymentId}`);
  if (r.ok) loadDeployments();
  else alert('Checkout failed.');
}
window.checkout = checkout;

async function showCheckinModal() {
  const select = document.getElementById('checkin-personnel');
  select.innerHTML = '<option value="">Select operator...</option>';
  try {
    const r    = await api('GET', '/api/personnel/');
    const list = await r.json();
    list.forEach(p => {
      const opt = document.createElement('option');
      opt.value       = p.id;
      opt.textContent = `${p.first_name} ${p.last_name}${p.call_sign ? ' (' + p.call_sign + ')' : ''}`;
      select.appendChild(opt);
    });
  } catch {}
  document.getElementById('modal-checkin').style.display = 'flex';
}
window.showCheckinModal = showCheckinModal;

async function submitCheckin() {
  const pid    = document.getElementById('checkin-personnel').value;
  const role   = document.getElementById('checkin-role').value.trim();
  const div    = document.getElementById('checkin-division').value.trim();
  const team   = document.getElementById('checkin-team').value.trim();
  const errEl  = document.getElementById('checkin-error');
  errEl.textContent = '';

  if (!pid)  { errEl.textContent = 'Select an operator.'; return; }
  if (!role) { errEl.textContent = 'Role is required.';   return; }

  const r    = await api('POST', `/api/deployments/${state.incidentId}/checkin`, {
    personnel_id: pid, role, division: div, team,
  });
  const data = await r.json();
  if (!r.ok) { errEl.textContent = data.error || 'Check-in failed.'; return; }

  closeModal('modal-checkin');
  loadDeployments();
}
window.submitCheckin = submitCheckin;

// ---------------------------------------------------------------------------
// Radio log
// ---------------------------------------------------------------------------

async function loadRadioLog() {
  if (!state.incidentId) return;
  try {
    const [logR, summaryR] = await Promise.all([
      api('GET', `/api/radio/${state.incidentId}?limit=100`),
      api('GET', `/api/radio/${state.incidentId}/summary`),
    ]);
    const entries = await logR.json();
    const summary = await summaryR.json();

    document.getElementById('radio-total').textContent  = summary.total_entries   ?? 0;
    document.getElementById('radio-missed').textContent = summary.missed_checkins ?? 0;
    document.getElementById('radio-last').textContent   = summary.last_activity ? fmtDateTime(summary.last_activity) : '—';

    const el = document.getElementById('radio-log-list');
    if (!entries.length) { el.innerHTML = '<div class="empty-state">No radio entries for this incident.</div>'; return; }

    el.innerHTML = entries.map(e => `
      <div class="radio-entry ${e.is_missed_checkin ? 'radio-entry-missed' : ''}">
        <span class="radio-time">${fmtTime(e.logged_at)}</span>
        <span class="radio-callsign">${e.call_sign || '—'}</span>
        ${e.channel ? `<span class="radio-channel">${e.channel}</span>` : ''}
        <span class="radio-msg">${e.message}</span>
        ${e.is_missed_checkin ? '<span class="radio-missed-flag">⚑ MISSED</span>' : ''}
      </div>
    `).join('');
  } catch (e) { console.error('loadRadioLog error:', e); }
}

async function showRadioEntryModal() {
  const select = document.getElementById('radio-personnel');
  select.innerHTML = '<option value="">Unknown / general</option>';
  try {
    const r    = await api('GET', `/api/deployments/${state.incidentId}?status=active`);
    const list = await r.json();
    list.forEach(d => {
      const opt = document.createElement('option');
      opt.value       = d.personnel_id;
      opt.textContent = `${d.first_name} ${d.last_name}${d.call_sign ? ' (' + d.call_sign + ')' : ''}`;
      select.appendChild(opt);
    });
  } catch {}
  document.getElementById('modal-radio').style.display = 'flex';
}
window.showRadioEntryModal = showRadioEntryModal;

async function submitRadioEntry() {
  const pid     = document.getElementById('radio-personnel').value;
  const channel = document.getElementById('radio-channel').value.trim();
  const message = document.getElementById('radio-message').value.trim();
  const errEl   = document.getElementById('radio-error');
  errEl.textContent = '';

  if (!message) { errEl.textContent = 'Message is required.'; return; }

  const r    = await api('POST', `/api/radio/${state.incidentId}`, {
    personnel_id: pid || null, channel, message,
  });
  const data = await r.json();
  if (!r.ok) { errEl.textContent = data.error || 'Failed to log entry.'; return; }

  closeModal('modal-radio');
  document.getElementById('radio-message').value = '';
  loadRadioLog();
}
window.submitRadioEntry = submitRadioEntry;

async function flagMissedCheckin() {
  const select = document.getElementById('radio-personnel');
  if (!select.options.length) await showRadioEntryModal();
  const pid = prompt('Enter the personnel ID to flag as missed check-in, or use the Log Entry modal.');
  if (!pid) return;
  const r = await api('POST', `/api/radio/${state.incidentId}/missed`, { personnel_id: pid });
  if (r.ok) loadRadioLog();
}
window.flagMissedCheckin = flagMissedCheckin;

// ---------------------------------------------------------------------------
// Logbook
// ---------------------------------------------------------------------------

let _compiledForms = null;
let _currentNarrativeForm = null;

async function compileForms() {
  if (!state.incidentId) { alert('Select an incident first.'); return; }
  const btn = document.getElementById('btn-compile');
  btn.disabled = true; btn.textContent = 'Compiling...';

  try {
    const r    = await api('GET', `/api/forms/${state.incidentId}/compile`);
    const data = await r.json();
    if (!r.ok) { alert(data.error || 'Compilation failed.'); return; }

    _compiledForms = data;
    renderValidationBanner(data.validation);
    renderFormsGrid(data.compiled, data.validation);

    const ready = data.validation.ready_to_sign;
    document.getElementById('btn-sign').disabled = !ready;
    document.getElementById('export-row').style.display = 'none';
  } catch (e) {
    console.error('compileForms error:', e);
  } finally {
    btn.disabled = false; btn.textContent = '↻ Compile forms';
  }
}
window.compileForms = compileForms;

function renderValidationBanner(validation) {
  const banner = document.getElementById('validation-banner');
  const summary = document.getElementById('val-summary');
  banner.style.display = 'block';

  const { red_count, yellow_count, green_count } = validation.summary;
  summary.innerHTML = `
    <span style="font-weight:600;font-size:13px">${validation.ready_to_sign ? '✓ Ready to sign' : 'Not ready to sign'}</span>
    ${red_count    ? `<span class="val-chip val-red">✕ ${red_count} required</span>`       : ''}
    ${yellow_count ? `<span class="val-chip val-yellow">⚠ ${yellow_count} recommended</span>` : ''}
    ${green_count  ? `<span class="val-chip val-green">✓ ${green_count} complete</span>`   : ''}
  `;
}

const FORM_LABELS = {
  ics_201: ['ICS-201', 'Incident Briefing'],
  ics_204: ['ICS-204', 'Assignment List'],
  ics_205: ['ICS-205', 'Radio Plan'],
  ics_206: ['ICS-206', 'Medical Plan'],
  ics_209: ['ICS-209', 'Status Summary'],
  ics_211: ['ICS-211', 'Check-In List'],
  ics_214: ['ICS-214', 'Activity Log'],
  ics_215: ['ICS-215', 'Operational Planning'],
};

function renderFormsGrid(compiled, validation) {
  const grid = document.getElementById('forms-grid');
  grid.innerHTML = Object.entries(FORM_LABELS).map(([key, [number, title]]) => {
    const formVal  = validation.forms[key];
    const status   = formVal?.status || 'green';
    const signed   = compiled[key]?.signed_at;
    const cardClass = signed ? 'form-card-signed' : `form-card-${status}`;
    const statusLabel = signed ? '✓ Signed' : { red: '✕ Required fields missing', yellow: '⚠ Recommended fields missing', green: '✓ Ready' }[status];
    const statusClass = signed ? 'form-status-signed' : `form-status-${status}`;
    return `
      <div class="form-card ${cardClass}" onclick="openFormDetail('${key}')">
        <div class="form-card-number">${number}</div>
        <div class="form-card-title">${title}</div>
        <div class="form-card-status ${statusClass}">${statusLabel}</div>
      </div>
    `;
  }).join('');
}

function openFormDetail(formKey) {
  if (!_compiledForms) return;
  const formVal = _compiledForms.validation.forms[formKey];
  const redFields = formVal?.fields?.filter(f => f.status === 'red') || [];

  if (!redFields.length) {
    alert(`${FORM_LABELS[formKey][0]} — ${FORM_LABELS[formKey][1]}\n\nNo required fields missing. Form is ready.`);
    return;
  }

  _currentNarrativeForm = formKey;
  const title = `${FORM_LABELS[formKey][0]} — Complete required fields`;
  document.getElementById('narrative-modal-title').textContent = title;

  const narrativeFields = {
    ics_201: ['situation_summary', 'initial_objectives', 'current_actions'],
    ics_206: ['hospitals', 'medical_aid_stations'],
    ics_209: ['current_situation', 'primary_mission', 'planned_actions'],
  };

  const fields = narrativeFields[formKey] || [];
  if (!fields.length) {
    alert(`This form cannot be edited manually here. Check BASECAMP data to resolve missing fields.`);
    return;
  }

  const body = document.getElementById('narrative-modal-body');
  body.innerHTML = fields.map(f => `
    <div class="form-group">
      <label>${f.replace(/_/g,' ')}</label>
      <textarea id="narrative-${f}" placeholder="Enter ${f.replace(/_/g,' ')}...">${_compiledForms.compiled[formKey]?.[f] || ''}</textarea>
    </div>
  `).join('');

  document.getElementById('modal-narrative').style.display = 'flex';
}
window.openFormDetail = openFormDetail;

async function submitNarrative() {
  if (!_currentNarrativeForm) return;
  const narrativeFields = {
    ics_201: ['situation_summary', 'initial_objectives', 'current_actions'],
    ics_206: ['hospitals', 'medical_aid_stations'],
    ics_209: ['current_situation', 'primary_mission', 'planned_actions'],
  };
  const fields = {};
  (narrativeFields[_currentNarrativeForm] || []).forEach(f => {
    const el = document.getElementById(`narrative-${f}`);
    if (el) fields[f] = el.value.trim();
  });
  const r = await api('POST', `/api/forms/${state.incidentId}/narrative`, {
    form: _currentNarrativeForm, fields,
  });
  if (r.ok) {
    closeModal('modal-narrative');
    compileForms();
  } else {
    const data = await r.json();
    alert(data.error || 'Failed to save narrative.');
  }
}
window.submitNarrative = submitNarrative;

async function signForms() {
  document.getElementById('modal-signoff').style.display = 'flex';
}
window.signForms = signForms;

async function confirmSignoff() {
  const password = document.getElementById('signoff-password').value;
  const errEl    = document.getElementById('signoff-error');
  errEl.textContent = '';

  if (!password) { errEl.textContent = 'Password is required to confirm sign-off.'; return; }

  // Re-authenticate to confirm identity
  const authR = await fetch(`${API}/api/users/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: state.user.username, password }),
  });
  if (!authR.ok) { errEl.textContent = 'Password incorrect.'; return; }

  const r    = await api('POST', `/api/forms/${state.incidentId}/sign`);
  const data = await r.json();
  if (!r.ok) { errEl.textContent = data.error || 'Sign-off failed.'; return; }

  closeModal('modal-signoff');
  document.getElementById('signoff-password').value = '';
  document.getElementById('export-row').style.display = 'flex';
  document.getElementById('btn-sign').disabled = true;
  alert(`✓ ${data.signed_forms.length} forms signed at ${fmtDateTime(data.signed_at)}`);
  compileForms();
}
window.confirmSignoff = confirmSignoff;

async function exportZip() {
  window.open(`/api/forms/${state.incidentId}/export/zip`);
}
async function exportJson() {
  window.open(`/api/forms/${state.incidentId}/export/json`);
}
window.exportZip  = exportZip;
window.exportJson = exportJson;

// ---------------------------------------------------------------------------
// SocketIO real-time
// ---------------------------------------------------------------------------

function connectSocket() {
  try {
    state.socket = io({ auth: { token: state.token } });

    state.socket.on('connect', () => {
      setSyncStatus('online', 'Live');
      if (state.incidentId) state.socket.emit('join_incident', { incident_id: state.incidentId });
    });

    state.socket.on('disconnect', () => setSyncStatus('offline', 'Disconnected'));

    state.socket.on('gps_update', data => {
      if (state.screen === 'map') updateMapPositions([data]);
    });

    state.socket.on('operator_checkin', data => {
      showAlertBanner(`${data.name} checked in as ${data.role}`);
      if (state.screen === 'deployments') loadDeployments();
    });

    state.socket.on('missed_checkin', data => {
      showAlertBanner(`⚑ MISSED CHECK-IN — ${data.name} (${data.call_sign}) — Last contact: ${fmtTime(data.last_contact || data.logged_at)}`);
      if (state.screen === 'radio') loadRadioLog();
    });

    state.socket.on('radio_entry', () => {
      if (state.screen === 'radio') loadRadioLog();
    });

    state.socket.on('sync_status', data => {
      const online = data.online;
      setSyncStatus(online ? 'online' : 'offline', online ? 'Synced' : 'Offline');
    });
  } catch (e) {
    console.warn('SocketIO not available:', e);
  }
}

function setSyncStatus(state, label) {
  const dot   = document.querySelector('.sync-dot');
  const labelEl = document.getElementById('sync-label');
  if (dot) { dot.className = `sync-dot ${state}`; }
  if (labelEl) labelEl.textContent = label;
}

// ---------------------------------------------------------------------------
// Alert banner
// ---------------------------------------------------------------------------

function showAlertBanner(message) {
  const banner = document.getElementById('alert-banner');
  document.getElementById('alert-text').textContent = message;
  banner.style.display = 'flex';
  clearTimeout(banner._timer);
  banner._timer = setTimeout(dismissAlert, 15000);
}

function dismissAlert() {
  document.getElementById('alert-banner').style.display = 'none';
}
window.dismissAlert = dismissAlert;

// ---------------------------------------------------------------------------
// Auto-refresh
// ---------------------------------------------------------------------------

function startAutoRefresh() {
  state.refreshTimer = setInterval(() => {
    if (state.screen === 'overview')    refreshOverview();
    if (state.screen === 'map')         refreshMap();
    if (state.screen === 'deployments') loadDeployments();
    if (state.screen === 'radio')       loadRadioLog();
  }, 30_000);
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------

function closeModal(id) {
  document.getElementById(id).style.display = 'none';
}
window.closeModal = closeModal;

// Close on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.style.display = 'none';
  });
});

// ---------------------------------------------------------------------------
// Segment modal (placeholder)
// ---------------------------------------------------------------------------
function showSegmentModal() {
  alert('Segment creation — coming in the full UI build. Add segments via the map or API.');
}
window.showSegmentModal = showSegmentModal;

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function fmtTime(ts) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' }); }
  catch { return ts.slice(11, 16); }
}

function fmtDateTime(ts) {
  if (!ts) return '—';
  try { return new Date(ts).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }); }
  catch { return ts.slice(0, 16); }
}