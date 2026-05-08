/* ============================================================
   SARPack 2.0 — portal.js
   Core portal JS: auth, socket, module switching,
   SOS banner, popout windows, op clock, incident selector.
   ============================================================ */

'use strict';

// ── Global state ──────────────────────────────────────────────
const SP = {
  token:        null,
  user:         null,
  incident:     null,   // currently selected incident
  incidents:    [],
  socket:       null,
  opClockStart: null,
  opClockTimer: null,
  activeModule: 'basecamp',
  wardenMFAVerified: false,
  preToken:     null,   // MFA pre-auth token
  popouts:      {},     // track open popout windows
};

// ── API base ──────────────────────────────────────────────────
const API = '';

async function apiFetch(method, path, body, mfaCode) {
  const headers = { 'Content-Type': 'application/json' };
  if (SP.token) headers['Authorization'] = `Bearer ${SP.token}`;
  if (mfaCode)  headers['X-MFA-Code']    = mfaCode;

  const opts = { method, headers };
  if (body) opts.body = JSON.stringify(body);

  const res  = await fetch(API + path, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ── Toast notifications ───────────────────────────────────────
function toast(msg, type = 'info', duration = 3500) {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  document.getElementById('toast-container').appendChild(el);
  setTimeout(() => el.remove(), duration);
}

// ── Modal helpers ─────────────────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

// ============================================================
//  BOOT
// ============================================================

document.addEventListener('DOMContentLoaded', async () => {
  // Check for saved token
  const savedToken = localStorage.getItem('sp_token');
  const savedUser  = localStorage.getItem('sp_user');

  if (savedToken && savedUser) {
    SP.token = savedToken;
    SP.user  = JSON.parse(savedUser);

    // Verify token is still valid
    try {
      await apiFetch('GET', '/api/users/me');
      bootPortal();
      return;
    } catch {
      // Token expired — clear and show login
      clearAuth();
    }
  }

  showLogin();
});

// ============================================================
//  AUTH
// ============================================================

function showLogin() {
  document.getElementById('login-screen').style.display  = 'flex';
  document.getElementById('portal-shell').style.display  = 'none';
  document.getElementById('login-step-1').style.display  = 'block';
  document.getElementById('login-step-2').style.display  = 'none';
  document.getElementById('login-error').textContent     = '';
}

function hideLogin() {
  document.getElementById('login-screen').style.display = 'none';
  document.getElementById('portal-shell').style.display = 'grid';
}

function clearAuth() {
  SP.token    = null;
  SP.user     = null;
  SP.incident = null;
  localStorage.removeItem('sp_token');
  localStorage.removeItem('sp_user');
  localStorage.removeItem('sp_incident');
}

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
    const data = await apiFetch('POST', '/api/users/login', { username, password });

    if (data.mfa_required) {
      // Store pre-token and show MFA step
      SP.preToken = data.pre_token;
      document.getElementById('login-step-1').style.display = 'none';
      document.getElementById('login-step-2').style.display = 'block';
      document.getElementById('mfa-code').focus();
      return;
    }

    // Full login success
    SP.token = data.token;
    SP.user  = data;
    localStorage.setItem('sp_token', data.token);
    localStorage.setItem('sp_user', JSON.stringify(data));
    hideLogin();
    bootPortal();

  } catch (e) {
    errEl.textContent = e.message || 'Login failed.';
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Sign In';
  }
}

async function handleMFA() {
  const code  = document.getElementById('mfa-code').value.trim();
  const errEl = document.getElementById('mfa-error');
  const btn   = document.getElementById('mfa-btn');

  errEl.textContent = '';
  if (!code || code.length !== 6) {
    errEl.textContent = 'Enter the 6-digit code.';
    return;
  }

  btn.disabled    = true;
  btn.textContent = 'Verifying...';

  try {
    const data = await apiFetch('POST', '/api/users/login/mfa', {
      pre_token: SP.preToken,
      code,
    });

    SP.token = data.token;
    SP.user  = data;
    localStorage.setItem('sp_token', data.token);
    localStorage.setItem('sp_user', JSON.stringify(data));
    hideLogin();
    bootPortal();

  } catch (e) {
    errEl.textContent = e.message || 'Invalid code.';
    document.getElementById('mfa-code').value = '';
    document.getElementById('mfa-code').focus();
  } finally {
    btn.disabled    = false;
    btn.textContent = 'Verify';
  }
}

function backToLogin() {
  SP.preToken = null;
  document.getElementById('login-step-2').style.display = 'none';
  document.getElementById('login-step-1').style.display = 'block';
  document.getElementById('mfa-code').value = '';
  document.getElementById('mfa-error').textContent = '';
}

async function handleLogout() {
  if (!confirm('Sign out of SARPack?')) return;
  try { await apiFetch('POST', '/api/users/logout'); } catch {}
  clearAuth();
  if (SP.socket) SP.socket.disconnect();
  clearInterval(SP.opClockTimer);
  // Close all popout windows
  Object.values(SP.popouts).forEach(w => { try { w.close(); } catch {} });
  showLogin();
}

// ============================================================
//  PORTAL BOOT
// ============================================================

async function bootPortal() {
  updateTopbarUser();
  connectSocket();
  startOpClock();
  await loadIncidents();

  // Restore last selected incident
  const savedIncident = localStorage.getItem('sp_incident');
  if (savedIncident) {
    const inc = SP.incidents.find(i => i.id === savedIncident);
    if (inc) selectIncident(inc);
  }

  // Boot each module
  await loadBasecamp();
}

function updateTopbarUser() {
  const el = document.getElementById('topbar-user');
  if (!el || !SP.user) return;
  const role = (SP.user.role || '').replace('_', ' ').toUpperCase();
  const name = (SP.user.username || '').toUpperCase();
  el.textContent = `${role} · ${name}`;
  el.title       = 'Click to sign out';
}

// ============================================================
//  OPERATION CLOCK
// ============================================================

function startOpClock() {
  if (SP.incident?.started_at) {
    SP.opClockStart = new Date(SP.incident.started_at);
  } else {
    SP.opClockStart = new Date();
  }

  clearInterval(SP.opClockTimer);
  SP.opClockTimer = setInterval(tickOpClock, 1000);
  tickOpClock();
}

function tickOpClock() {
  const el = document.getElementById('op-clock');
  if (!el) return;

  const now     = new Date();
  const start   = SP.opClockStart || now;
  const elapsed = Math.max(0, Math.floor((now - start) / 1000));
  const h       = Math.floor(elapsed / 3600);
  const m       = Math.floor((elapsed % 3600) / 60);
  const s       = elapsed % 60;

  el.textContent = `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

// ============================================================
//  INCIDENT SELECTOR
// ============================================================

async function loadIncidents() {
  try {
    const data    = await apiFetch('GET', '/api/incidents/?status=active&limit=50');
    SP.incidents  = data.incidents || [];
    renderIncidentDropdown();
  } catch (e) {
    console.error('Failed to load incidents:', e);
  }
}

function renderIncidentDropdown() {
  const dropdown = document.getElementById('incident-dropdown');
  if (!dropdown) return;

  if (SP.incidents.length === 0) {
    dropdown.innerHTML = `
      <div class="incident-dropdown-item" style="color:var(--text-muted)">
        No active incidents
      </div>
      <div class="incident-dropdown-item" onclick="openNewIncidentModal(); toggleIncidentDropdown()">
        <div style="color:var(--orange);font-family:var(--font-display);font-size:13px;letter-spacing:1px">
          + Create New Incident
        </div>
      </div>`;
    return;
  }

  dropdown.innerHTML = SP.incidents.map(inc => `
    <div class="incident-dropdown-item" onclick="selectIncident(${JSON.stringify(inc).replace(/"/g,'&quot;')}); toggleIncidentDropdown()">
      <div class="incident-dropdown-number">${inc.incident_number}</div>
      <div class="incident-dropdown-name">${inc.incident_name}</div>
      <div style="font-family:var(--font-mono);font-size:10px;color:var(--text-muted);margin-top:2px">
        ${inc.county ? inc.county + ', ' : ''}${inc.state || 'PA'} · ${inc.deployed_count || 0} deployed
      </div>
    </div>
  `).join('') + `
    <div class="incident-dropdown-item" onclick="openNewIncidentModal(); toggleIncidentDropdown()">
      <div style="color:var(--orange);font-family:var(--font-display);font-size:13px;letter-spacing:1px">
        + Create New Incident
      </div>
    </div>`;
}

function toggleIncidentDropdown() {
  const dd = document.getElementById('incident-dropdown');
  dd.classList.toggle('open');
}

// Close dropdown when clicking outside
document.addEventListener('click', (e) => {
  const selector = document.getElementById('incident-selector');
  if (selector && !selector.contains(e.target)) {
    document.getElementById('incident-dropdown')?.classList.remove('open');
  }
});

function selectIncident(incident) {
  SP.incident = incident;
  localStorage.setItem('sp_incident', incident.id);

  // Update topbar display
  document.getElementById('topbar-incident-display').textContent =
    `${incident.incident_number} — ${incident.incident_name}`;

  // Restart op clock from incident start time
  if (incident.started_at) {
    SP.opClockStart = new Date(incident.started_at);
  }

  // Join socket room
  if (SP.socket) {
    SP.socket.emit('join_incident', { incident_id: incident.id });
  }

  // Refresh active module with new incident
  refreshActiveModule();

  toast(`Incident: ${incident.incident_name}`, 'info');
}

// ============================================================
//  MODULE SWITCHING
// ============================================================

function switchModule(moduleName) {
  // Update tab buttons
  document.querySelectorAll('.tab-btn[data-module]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.module === moduleName);
  });

  // Show/hide panels
  document.querySelectorAll('.module-panel').forEach(panel => {
    panel.classList.remove('active');
  });
  document.getElementById(`module-${moduleName}`)?.classList.add('active');

  SP.activeModule = moduleName;

  // WARDEN MFA gate
  if (moduleName === 'warden') {
    checkWardenAccess();
    return;
  }

  // Load module content
  switch (moduleName) {
    case 'basecamp': loadBasecamp(); break;
    case 'logbook':  loadLogbook();  break;
    case 'relay':    loadRelay();    break;
  }
}

function refreshActiveModule() {
  switch (SP.activeModule) {
    case 'basecamp': loadBasecamp(); break;
    case 'warden':   loadWarden();   break;
    case 'logbook':  loadLogbook();  break;
    case 'relay':    loadRelay();    break;
  }
}

// ── WARDEN MFA gate ───────────────────────────────────────────
function checkWardenAccess() {
  if (SP.wardenMFAVerified || !SP.user?.mfa_enabled) {
    // No MFA required or already verified
    showWardenContent();
    loadWarden();
    return;
  }

  // Show MFA gate
  document.getElementById('warden-mfa-gate').style.display    = 'flex';
  document.getElementById('warden-content').style.display     = 'none';
  document.getElementById('warden-mfa-input').value           = '';
  document.getElementById('warden-mfa-error').textContent     = '';
  setTimeout(() => document.getElementById('warden-mfa-input').focus(), 100);
}

async function verifyWardenMFA() {
  const code  = document.getElementById('warden-mfa-input').value.trim();
  const errEl = document.getElementById('warden-mfa-error');
  errEl.textContent = '';

  if (!code || code.length !== 6) {
    errEl.textContent = 'Enter the 6-digit code.';
    return;
  }

  try {
    // Test the code by hitting a WARDEN endpoint with X-MFA-Code header
    await apiFetch('GET', '/api/personnel/?limit=1', null, code);
    SP.wardenMFAVerified = true;
    // Store MFA code for subsequent requests this session
    SP._wardenMFACode = code;
    showWardenContent();
    loadWarden();
  } catch (e) {
    errEl.textContent = 'Invalid code — try again.';
    document.getElementById('warden-mfa-input').value = '';
    document.getElementById('warden-mfa-input').focus();
  }
}

function showWardenContent() {
  document.getElementById('warden-mfa-gate').style.display = 'none';
  document.getElementById('warden-content').style.display  = 'flex';
}

// ── Sub-tab switchers ─────────────────────────────────────────
function switchWardenTab(tab) {
  document.querySelectorAll('[data-warden-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.wardenTab === tab);
  });
  loadWardenTab(tab);
}

function switchLogbookTab(tab) {
  document.querySelectorAll('[data-logbook-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.logbookTab === tab);
  });
  loadLogbookTab(tab);
}

function switchRelayTab(tab) {
  document.querySelectorAll('[data-relay-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.relayTab === tab);
  });
  loadRelayTab(tab);
}

// ============================================================
//  POPOUT WINDOWS
// ============================================================

function popoutModule(moduleName, event) {
  if (event) event.stopPropagation();

  // If already open, focus it
  if (SP.popouts[moduleName] && !SP.popouts[moduleName].closed) {
    SP.popouts[moduleName].focus();
    return;
  }

  const url    = `/popout/${moduleName}?token=${SP.token}`;
  const width  = 1280;
  const height = 800;
  const left   = window.screen.width > 2000 ? window.screen.width - width : 100;
  const top    = 40;

  const win = window.open(
    url,
    `sarpack_${moduleName}`,
    `width=${width},height=${height},left=${left},top=${top},` +
    `menubar=no,toolbar=no,location=no,status=no`
  );

  if (win) {
    SP.popouts[moduleName] = win;
    toast(`${moduleName.toUpperCase()} opened in new window`, 'info');
  } else {
    toast('Popup blocked — allow popups for SARPack', 'warn');
  }
}

// ============================================================
//  SOCKETIO
// ============================================================

function connectSocket() {
  SP.socket = io({
    auth: { token: SP.token },
    reconnection:      true,
    reconnectionDelay: 2000,
  });

  SP.socket.on('connect', () => {
    setConnectionStatus(true);
    // Rejoin incident room if active
    if (SP.incident) {
      SP.socket.emit('join_incident', { incident_id: SP.incident.id });
    }
    // Join personal operator room for DZ targeting
    if (SP.user?.personnel_id) {
      SP.socket.emit('join_operator_room', { personnel_id: SP.user.personnel_id });
    }
  });

  SP.socket.on('disconnect', () => {
    setConnectionStatus(false);
  });

  SP.socket.on('connect_error', () => {
    setConnectionStatus(false);
  });

  // ── SOS — fires on ALL windows simultaneously ─────────────
  SP.socket.on('sos_alert', (data) => {
    showSOSBanner(data);
    // Also notify any open popout windows
    broadcastToPopouts('sos_alert', data);
  });

  SP.socket.on('sos_acknowledged', (data) => {
    hideSOSBanner();
    broadcastToPopouts('sos_acknowledged', data);
  });

  // ── Map / position updates ────────────────────────────────
  SP.socket.on('position_update', (data) => {
    if (typeof onPositionUpdate === 'function') onPositionUpdate(data);
    broadcastToPopouts('position_update', data);
  });

  SP.socket.on('lkp_updated', (data) => {
    if (typeof onLKPUpdated === 'function') onLKPUpdated(data);
    broadcastToPopouts('lkp_updated', data);
  });

  SP.socket.on('lkp_cleared', (data) => {
    if (typeof onLKPCleared === 'function') onLKPCleared(data);
    broadcastToPopouts('lkp_cleared', data);
  });

  SP.socket.on('marker_added', (data) => {
    if (typeof onMarkerAdded === 'function') onMarkerAdded(data);
    broadcastToPopouts('marker_added', data);
  });

  SP.socket.on('marker_removed', (data) => {
    if (typeof onMarkerRemoved === 'function') onMarkerRemoved(data);
    broadcastToPopouts('marker_removed', data);
  });

  // ── DZ targeting (personal room) ─────────────────────────
  SP.socket.on('dz_target', (data) => {
    // In portal this goes to BASECAMP map notification
    if (typeof onDZTarget === 'function') onDZTarget(data);
  });

  // ── Radio ─────────────────────────────────────────────────
  SP.socket.on('radio_entry', (data) => {
    if (typeof onRadioEntry === 'function') onRadioEntry(data);
    broadcastToPopouts('radio_entry', data);
  });

  SP.socket.on('missed_checkin', (data) => {
    if (typeof onMissedCheckin === 'function') onMissedCheckin(data);
    broadcastToPopouts('missed_checkin', data);
    updateTabBadge('basecamp', 1);
  });

  // ── Personnel ─────────────────────────────────────────────
  SP.socket.on('personnel_checkin', (data) => {
    if (typeof onPersonnelCheckin === 'function') onPersonnelCheckin(data);
    broadcastToPopouts('personnel_checkin', data);
  });

  SP.socket.on('personnel_checkout', (data) => {
    if (typeof onPersonnelCheckout === 'function') onPersonnelCheckout(data);
    broadcastToPopouts('personnel_checkout', data);
  });

  // ── Segments ──────────────────────────────────────────────
  SP.socket.on('segment_updated', (data) => {
    if (typeof onSegmentUpdated === 'function') onSegmentUpdated(data);
    broadcastToPopouts('segment_updated', data);
  });

  // ── Patients ──────────────────────────────────────────────
  SP.socket.on('patient_reported', (data) => {
    if (typeof onPatientReported === 'function') onPatientReported(data);
    toast(`New patient report — severity: ${data.severity || 'unknown'}`, 'warn');
    broadcastToPopouts('patient_reported', data);
  });

  // ── Drone feed ────────────────────────────────────────────
  SP.socket.on('drone_stream_ready', (data) => {
    showDroneNotification(data);
    broadcastToPopouts('drone_stream_ready', data);
  });

  // ── RELAY nodes ───────────────────────────────────────────
  SP.socket.on('relay_node_update', (data) => {
    if (typeof onRelayNodeUpdate === 'function') onRelayNodeUpdate(data);
  });
}

function setConnectionStatus(online) {
  const dot   = document.getElementById('connection-dot');
  const label = document.getElementById('connection-label');
  if (!dot || !label) return;

  if (online) {
    dot.className        = 'status-dot';
    label.textContent    = 'Online';
  } else {
    dot.className        = 'status-dot offline';
    label.textContent    = 'Offline';
  }
}

// Broadcast events to all open popout windows via postMessage
function broadcastToPopouts(event, data) {
  Object.values(SP.popouts).forEach(win => {
    if (win && !win.closed) {
      try { win.postMessage({ sarpack_event: event, data }, '*'); }
      catch {}
    }
  });
}

// ============================================================
//  SOS BANNER
// ============================================================

let _activeSOS = null;

function showSOSBanner(data) {
  _activeSOS = data;
  const banner = document.getElementById('sos-banner');
  const detail = document.getElementById('sos-banner-detail');

  let text = '⚑ OPERATOR IN DISTRESS';
  if (data.personnel) {
    const p = data.personnel;
    text    = `⚑ ${p.call_sign || p.last_name} — OPERATOR IN DISTRESS`;
  }

  detail.textContent = 'Click to navigate to GPS location →';
  banner.classList.add('active');

  // Flash tab title
  _startTitleFlash('⚑ SOS ALERT');

  // Play alert sound if available
  try {
    const ctx  = new AudioContext();
    const osc  = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.setValueAtTime(660, ctx.currentTime + 0.2);
    gain.gain.setValueAtTime(0.3, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.5);
  } catch {}
}

function hideSOSBanner() {
  _activeSOS = null;
  document.getElementById('sos-banner').classList.remove('active');
  _stopTitleFlash();
}

function handleSOSBannerClick() {
  if (!_activeSOS) return;

  // Switch to BASECAMP and navigate map to SOS location
  switchModule('basecamp');

  if (_activeSOS.latitude && _activeSOS.longitude) {
    // BASECAMP map will handle this event
    if (typeof navigateMapTo === 'function') {
      navigateMapTo(_activeSOS.latitude, _activeSOS.longitude, 16);
    }
  }

  // Acknowledge the SOS
  if (_activeSOS.sos_id && SP.incident) {
    apiFetch('POST',
      `/api/incidents/${SP.incident.id}/sos/${_activeSOS.sos_id}/acknowledge`
    ).catch(() => {});
  }
}

// ── Tab title flash ───────────────────────────────────────────
let _titleFlashTimer = null;
let _originalTitle   = document.title;

function _startTitleFlash(alertText) {
  _originalTitle  = document.title;
  let show        = true;
  _titleFlashTimer = setInterval(() => {
    document.title = show ? alertText : _originalTitle;
    show           = !show;
  }, 800);
}

function _stopTitleFlash() {
  clearInterval(_titleFlashTimer);
  document.title = _originalTitle;
}

// ============================================================
//  TAB BADGES
// ============================================================

const _badgeCounts = {};

function updateTabBadge(module, delta) {
  _badgeCounts[module] = (_badgeCounts[module] || 0) + delta;
  const el = document.getElementById(`badge-${module}`);
  if (!el) return;

  if (_badgeCounts[module] > 0) {
    el.style.display = 'inline';
    el.textContent   = _badgeCounts[module];
  } else {
    el.style.display   = 'none';
    _badgeCounts[module] = 0;
  }
}

function clearTabBadge(module) {
  _badgeCounts[module] = 0;
  const el = document.getElementById(`badge-${module}`);
  if (el) el.style.display = 'none';
}

// ============================================================
//  DRONE FEED NOTIFICATION
// ============================================================

function showDroneNotification(data) {
  const msg = document.createElement('div');
  msg.className = 'toast toast-info';
  msg.style.cssText = 'cursor:pointer;min-width:320px';
  msg.innerHTML = `
    <div>
      <div style="font-family:var(--font-display);font-size:13px;letter-spacing:1px">
        DRONE FEED AVAILABLE
      </div>
      <div style="font-size:11px;margin-top:2px">${data.asset_name || 'Air asset'} — click to open</div>
    </div>`;
  msg.onclick = () => {
    openDroneFeed(data.asset_id, data.stream_url);
    msg.remove();
  };
  document.getElementById('toast-container').appendChild(msg);
  setTimeout(() => msg.remove(), 10000);
}

function openDroneFeed(assetId, streamUrl) {
  const url = `/drone?asset=${assetId}`;
  const win = window.open(url, 'sarpack_drone',
    'width=1280,height=720,left=1920,top=0,menubar=no,toolbar=no,location=no');
  if (win) {
    SP.popouts['drone'] = win;
  } else {
    toast('Popup blocked — allow popups for SARPack', 'warn');
  }
}

// ============================================================
//  INCIDENT MANAGEMENT
// ============================================================

function openNewIncidentModal() {
  openModal('modal-new-incident');
}

async function createIncident() {
  const number = document.getElementById('new-inc-number').value.trim();
  const name   = document.getElementById('new-inc-name').value.trim();
  const type   = document.getElementById('new-inc-type').value;
  const county = document.getElementById('new-inc-county').value.trim();
  const state  = document.getElementById('new-inc-state').value.trim();
  const ic     = document.getElementById('new-inc-ic').value.trim();
  const desc   = document.getElementById('new-inc-desc').value.trim();

  if (!number || !name) {
    toast('Incident number and name are required', 'error');
    return;
  }

  try {
    const inc = await apiFetch('POST', '/api/incidents/', {
      incident_number: number,
      incident_name:   name,
      incident_type:   type,
      county, state, ic_name: ic, description: desc,
    });

    SP.incidents.unshift(inc);
    renderIncidentDropdown();
    selectIncident(inc);
    closeModal('modal-new-incident');
    toast(`Incident ${number} created`, 'success');

    // Clear form
    ['new-inc-number','new-inc-name','new-inc-county','new-inc-ic','new-inc-desc']
      .forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('new-inc-state').value = 'PA';

  } catch (e) {
    toast(e.message, 'error');
  }
}

// ============================================================
//  PERSONNEL MANAGEMENT
// ============================================================

function openNewPersonnelModal() {
  openModal('modal-new-personnel');
}

async function createPersonnel() {
  const first  = document.getElementById('new-p-first').value.trim();
  const last   = document.getElementById('new-p-last').value.trim();
  const cs     = document.getElementById('new-p-callsign').value.trim();
  const blood  = document.getElementById('new-p-blood').value;
  const phone  = document.getElementById('new-p-phone').value.trim();
  const email  = document.getElementById('new-p-email').value.trim();
  const agency = document.getElementById('new-p-agency').value.trim();
  const notes  = document.getElementById('new-p-notes').value.trim();

  if (!first || !last) {
    toast('First and last name are required', 'error');
    return;
  }

  try {
    await apiFetch('POST', '/api/personnel/', {
      first_name: first, last_name: last,
      call_sign: cs || undefined,
      blood_type: blood || undefined,
      phone: phone || undefined,
      email: email || undefined,
      home_agency: agency || undefined,
      notes: notes || undefined,
    }, SP._wardenMFACode);

    closeModal('modal-new-personnel');
    toast(`${first} ${last} added to roster`, 'success');

    // Clear form
    ['new-p-first','new-p-last','new-p-callsign','new-p-phone',
     'new-p-email','new-p-agency','new-p-notes']
      .forEach(id => { document.getElementById(id).value = ''; });
    document.getElementById('new-p-blood').value = '';

    // Refresh WARDEN if active
    if (SP.activeModule === 'warden') loadWardenTab('roster');

  } catch (e) {
    toast(e.message, 'error');
  }
}

// ============================================================
//  REFRESH HELPERS (called by buttons)
// ============================================================

function refreshBasecamp() { loadBasecamp(); }
function refreshWarden()   { loadWardenTab('roster'); }
function refreshRelay()    { loadRelayTab('status'); }

// ============================================================
//  KEYBOARD SHORTCUTS
// ============================================================

document.addEventListener('keydown', (e) => {
  // Alt + 1-4 to switch modules
  if (e.altKey && !e.shiftKey && !e.ctrlKey) {
    const modules = ['basecamp','warden','logbook','relay'];
    const idx     = parseInt(e.key) - 1;
    if (idx >= 0 && idx < modules.length) {
      e.preventDefault();
      switchModule(modules[idx]);
    }
  }

  // Escape closes any open modal
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => {
      m.classList.remove('open');
    });
    document.querySelectorAll('.incident-selector-dropdown.open').forEach(d => {
      d.classList.remove('open');
    });
  }
});

// ============================================================
//  STUB HANDLERS
// Called by socket events — implemented in module JS files.
// Defined here as no-ops so portal.js doesn't throw if a
// module JS hasn't loaded yet.
// ============================================================

if (typeof onPositionUpdate  === 'undefined') window.onPositionUpdate  = () => {};
if (typeof onLKPUpdated      === 'undefined') window.onLKPUpdated      = () => {};
if (typeof onLKPCleared      === 'undefined') window.onLKPCleared      = () => {};
if (typeof onMarkerAdded     === 'undefined') window.onMarkerAdded     = () => {};
if (typeof onMarkerRemoved   === 'undefined') window.onMarkerRemoved   = () => {};
if (typeof onRadioEntry      === 'undefined') window.onRadioEntry      = () => {};
if (typeof onMissedCheckin   === 'undefined') window.onMissedCheckin   = () => {};
if (typeof onPersonnelCheckin=== 'undefined') window.onPersonnelCheckin= () => {};
if (typeof onPersonnelCheckout==='undefined') window.onPersonnelCheckout=() => {};
if (typeof onSegmentUpdated  === 'undefined') window.onSegmentUpdated  = () => {};
if (typeof onPatientReported === 'undefined') window.onPatientReported = () => {};
if (typeof onRelayNodeUpdate === 'undefined') window.onRelayNodeUpdate = () => {};
if (typeof onDZTarget        === 'undefined') window.onDZTarget        = () => {};
if (typeof navigateMapTo     === 'undefined') window.navigateMapTo     = () => {};
if (typeof loadBasecamp      === 'undefined') window.loadBasecamp      = () => {};
if (typeof loadWarden        === 'undefined') window.loadWarden        = () => {};
if (typeof loadWardenTab     === 'undefined') window.loadWardenTab     = () => {};
if (typeof loadLogbook       === 'undefined') window.loadLogbook       = () => {};
if (typeof loadLogbookTab    === 'undefined') window.loadLogbookTab    = () => {};
if (typeof loadRelay         === 'undefined') window.loadRelay         = () => {};
if (typeof loadRelayTab      === 'undefined') window.loadRelayTab      = () => {};