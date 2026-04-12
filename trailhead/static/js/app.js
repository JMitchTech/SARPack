/**
 * SARPack TRAILHEAD — app.js
 * Main application controller. Handles routing between screens,
 * authentication state, and coordinates GPS, map, and sync modules.
 *
 * Screens: login → status → map → patient → radio
 */

import { saveAuth, getAuth, clearAuth, saveState, getState } from './db.js';
import { startGps, stopGps, getLastFix } from './gps.js';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  token:      null,
  user:       null,
  deployment: null,
  incident:   null,
  segment:    null,
  screen:     'login',
  online:     navigator.onLine,
  gpsCount:   0,    // pending GPS fixes in queue
};


// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', async () => {
  // Register service worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js', { scope: '/' })
      .then(reg => console.log('[App] Service worker registered:', reg.scope))
      .catch(err => console.warn('[App] Service worker failed:', err));

    // Listen for messages from service worker
    navigator.serviceWorker.addEventListener('message', onSwMessage);
  }

  // Listen for online/offline
  window.addEventListener('online',  () => { state.online = true;  updateConnStatus(); });
  window.addEventListener('offline', () => { state.online = false; updateConnStatus(); });

  // Try to restore session from IndexedDB
  const auth = await getAuth();
  if (auth?.token) {
    state.token = auth.token;
    state.user  = auth.user;
    // Verify token is still valid
    const valid = await verifyToken();
    if (valid) {
      await loadDeployment();
      showScreen('status');
      return;
    }
  }

  showScreen('login');
});


// ---------------------------------------------------------------------------
// Screen routing
// ---------------------------------------------------------------------------

function showScreen(name) {
  state.screen = name;
  document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
  const screen = document.getElementById(`screen-${name}`);
  if (screen) screen.classList.add('active');
  updateNav();
}

function updateNav() {
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.screen === state.screen);
  });
  // Hide nav on login screen
  const nav = document.getElementById('bottom-nav');
  if (nav) nav.style.display = state.screen === 'login' ? 'none' : 'flex';
}


// ---------------------------------------------------------------------------
// Authentication
// ---------------------------------------------------------------------------

window.handleLogin = async function(e) {
  e.preventDefault();
  const username = document.getElementById('username').value.trim();
  const password = document.getElementById('password').value;
  const errorEl  = document.getElementById('login-error');

  if (!username || !password) {
    errorEl.textContent = 'Username and password are required.';
    return;
  }

  try {
    showLoginLoading(true);
    const response = await fetch('/api/users/login', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ username, password }),
    });

    const data = await response.json();

    if (!response.ok) {
      errorEl.textContent = data.error || 'Login failed.';
      showLoginLoading(false);
      return;
    }

    state.token = data.token;
    state.user  = { username: data.username, role: data.role, permissions: data.permissions };

    await saveAuth(data.token, state.user);
    await loadDeployment();
    showScreen('status');
    showLoginLoading(false);

  } catch (err) {
    errorEl.textContent = 'Connection error. Check network.';
    showLoginLoading(false);
    console.error('[App] Login error:', err);
  }
};


window.handleLogout = async function() {
  stopGps();
  await clearAuth();
  state.token      = null;
  state.user       = null;
  state.deployment = null;
  state.incident   = null;
  showScreen('login');
};


async function verifyToken() {
  try {
    const response = await fetch('/api/users/me', {
      headers: { 'Authorization': `Bearer ${state.token}` },
    });
    return response.ok;
  } catch {
    return false;
  }
}


// ---------------------------------------------------------------------------
// Deployment status
// ---------------------------------------------------------------------------

async function loadDeployment() {
  try {
    const response = await apiGet('/api/operator/me');
    const data     = await response.json();

    if (data.deployed) {
      state.deployment = data.deployment;
      state.incident   = data.deployment;
      state.segment    = data.segment;
      await saveState('deployment', data);
      renderStatusScreen(data);
      // Start GPS collection
      startGps(data.deployment.incident_id, {
        onFix:   onGpsFix,
        onQueue: onGpsQueue,
        onError: onGpsError,
      });
    } else {
      // Restore from IndexedDB if offline
      const cached = await getState('deployment');
      if (cached) {
        state.deployment = cached.deployment;
        renderStatusScreen(cached);
      } else {
        renderNotDeployed();
      }
    }
  } catch (err) {
    console.warn('[App] Load deployment failed:', err);
    // Try cache
    const cached = await getState('deployment');
    if (cached) renderStatusScreen(cached);
  }
}


// ---------------------------------------------------------------------------
// GPS callbacks
// ---------------------------------------------------------------------------

function onGpsFix(fix) {
  // Update last fix display
  const el = document.getElementById('last-gps');
  if (el) {
    el.textContent = `${fix.lat.toFixed(5)}, ${fix.lng.toFixed(5)}`;
  }
  // Update map if on map screen
  if (state.screen === 'map' && window.trailheadMap) {
    window.trailheadMap.updatePosition(fix.lat, fix.lng);
  }
}

function onGpsQueue(count) {
  state.gpsCount = count;
  const el = document.getElementById('gps-queue-count');
  if (el) el.textContent = count > 0 ? `${count} fixes queued` : '';
}

function onGpsError(message) {
  const el = document.getElementById('gps-error');
  if (el) {
    el.textContent = message;
    el.style.display = 'block';
  }
}


// ---------------------------------------------------------------------------
// Connectivity
// ---------------------------------------------------------------------------

function updateConnStatus() {
  const el = document.getElementById('conn-status');
  if (!el) return;
  el.textContent   = state.online ? 'Online' : 'Offline';
  el.className     = `conn-status ${state.online ? 'online' : 'offline'}`;
}


// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

export async function apiGet(url) {
  return fetch(url, {
    headers: { 'Authorization': `Bearer ${state.token}` },
  });
}

export async function apiPost(url, body) {
  return fetch(url, {
    method:  'POST',
    headers: {
      'Content-Type':  'application/json',
      'Authorization': `Bearer ${state.token}`,
    },
    body: JSON.stringify(body),
  });
}

export function getToken()      { return state.token;      }
export function getDeployment() { return state.deployment; }
export function getIncident()   { return state.incident;   }
export function getSegment()    { return state.segment;    }
export function isOnline()      { return state.online;     }


// ---------------------------------------------------------------------------
// Screen renderers (populated by the HTML template)
// ---------------------------------------------------------------------------

function renderStatusScreen(data) {
  const dep = data.deployment;
  const seg = data.segment;

  const setEl = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val || '—';
  };

  setEl('status-incident',  dep?.incident_name || 'No active incident');
  setEl('status-number',    dep?.incident_number);
  setEl('status-role',      dep?.role);
  setEl('status-division',  dep?.division);
  setEl('status-team',      dep?.team);
  setEl('status-checkin',   dep?.checked_in_at?.slice(0, 16).replace('T', ' '));
  setEl('status-segment',   seg ? `${seg.segment_id} (${seg.status})` : 'No segment assigned');
  setEl('status-county',    `${dep?.county || ''}, ${dep?.state || ''}`.trim().replace(/^,\s*/, ''));
}

function renderNotDeployed() {
  const el = document.getElementById('not-deployed-msg');
  if (el) el.style.display = 'block';
}

function showLoginLoading(loading) {
  const btn = document.getElementById('login-btn');
  if (btn) {
    btn.disabled     = loading;
    btn.textContent  = loading ? 'Signing in...' : 'Sign in';
  }
}


// ---------------------------------------------------------------------------
// Service worker messages
// ---------------------------------------------------------------------------

function onSwMessage(event) {
  const { type, result } = event.data;
  if (type === 'GPS_SYNC_COMPLETE') {
    onGpsQueue(0);
    console.log('[App] GPS sync complete:', result);
  }
}


// ---------------------------------------------------------------------------
// Navigation (called from HTML)
// ---------------------------------------------------------------------------

window.goToScreen = function(name) {
  showScreen(name);
  if (name === 'map' && state.deployment) {
    // Init map when first navigating to it
    import('./map.js').then(m => {
      if (!window.trailheadMap) {
        window.trailheadMap = m.initMap(
          state.deployment.incident_id,
          state.deployment.incident_lat,
          state.deployment.incident_lng,
          getToken,
        );
      }
    });
  }
};
