/**
 * SARPack TRAILHEAD — gps.js
 * GPS position collection. Collects fixes every 30 seconds using the
 * browser Geolocation API, pushes live when online, queues offline.
 *
 * Requires HTTPS — the Geolocation API is blocked on HTTP in modern browsers.
 * TRAILHEAD runs with ssl_context='adhoc' in development.
 */

import { queueGpsFix, getGpsQueueCount, clearGpsQueue, getAuth } from './db.js';

const GPS_INTERVAL_MS    = 30_000;   // collect every 30 seconds
const GPS_ACCURACY_MIN   = 100;      // ignore fixes worse than 100m accuracy
const GPS_TIMEOUT_MS     = 10_000;   // geolocation timeout

let _watchId     = null;
let _intervalId  = null;
let _incidentId  = null;
let _lastFix     = null;
let _onlineMode  = navigator.onLine;

// Callbacks for UI updates
let _onFix       = null;   // called with each new position fix
let _onQueue     = null;   // called when queue count changes
let _onError     = null;   // called on geolocation error


/**
 * Start GPS collection for an incident.
 * @param {string} incidentId - Active incident UUID
 * @param {object} callbacks  - { onFix, onQueue, onError }
 */
export function startGps(incidentId, callbacks = {}) {
  _incidentId = incidentId;
  _onFix      = callbacks.onFix   || (() => {});
  _onQueue    = callbacks.onQueue || (() => {});
  _onError    = callbacks.onError || (() => {});

  if (!navigator.geolocation) {
    _onError('Geolocation is not supported by this browser.');
    return;
  }

  // Collect immediately then on interval
  _collectFix();
  _intervalId = setInterval(_collectFix, GPS_INTERVAL_MS);

  // Listen for online/offline events
  window.addEventListener('online',  _onOnline);
  window.addEventListener('offline', _onOffline);

  console.log('[GPS] Started — incident:', incidentId);
}


/**
 * Stop GPS collection and clean up.
 */
export function stopGps() {
  if (_watchId)    navigator.geolocation.clearWatch(_watchId);
  if (_intervalId) clearInterval(_intervalId);
  window.removeEventListener('online',  _onOnline);
  window.removeEventListener('offline', _onOffline);
  _watchId    = null;
  _intervalId = null;
  _incidentId = null;
  console.log('[GPS] Stopped');
}


/**
 * Get the last collected GPS fix.
 */
export function getLastFix() {
  return _lastFix;
}


// ---------------------------------------------------------------------------
// Internal
// ---------------------------------------------------------------------------

async function _collectFix() {
  if (!_incidentId) return;

  return new Promise(resolve => {
    navigator.geolocation.getCurrentPosition(
      async position => {
        const fix = {
          incident_id:  _incidentId,
          lat:          position.coords.latitude,
          lng:          position.coords.longitude,
          elevation:    position.coords.altitude,
          accuracy:     position.coords.accuracy,
          recorded_at:  new Date().toISOString(),
        };

        // Ignore if accuracy is too poor
        if (fix.accuracy && fix.accuracy > GPS_ACCURACY_MIN) {
          console.log(`[GPS] Fix ignored — accuracy ${fix.accuracy.toFixed(0)}m exceeds limit`);
          resolve();
          return;
        }

        _lastFix = fix;
        _onFix(fix);

        if (navigator.onLine) {
          await _pushFix(fix);
        } else {
          await _queueFix(fix);
        }

        resolve();
      },
      error => {
        const messages = {
          1: 'Location permission denied. Enable location access in browser settings.',
          2: 'Position unavailable. Check GPS signal.',
          3: 'Location request timed out.',
        };
        const message = messages[error.code] || `GPS error: ${error.message}`;
        console.warn('[GPS] Error:', message);
        _onError(message);
        resolve();
      },
      {
        enableHighAccuracy: true,
        timeout:            GPS_TIMEOUT_MS,
        maximumAge:         0,
      }
    );
  });
}


async function _pushFix(fix) {
  try {
    const auth = await getAuth();
    if (!auth?.token) {
      await _queueFix(fix);
      return;
    }

    const response = await fetch('/api/gps/position', {
      method:  'POST',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${auth.token}`,
      },
      body: JSON.stringify(fix),
    });

    if (!response.ok) {
      console.warn('[GPS] Push failed, queueing:', response.status);
      await _queueFix(fix);
    } else {
      console.log('[GPS] Fix pushed live');
    }
  } catch {
    // Network error — queue for later
    await _queueFix(fix);
  }
}


async function _queueFix(fix) {
  await queueGpsFix(fix);
  const count = await getGpsQueueCount();
  console.log(`[GPS] Queued offline (${count} pending)`);
  _onQueue(count);

  // Register background sync if supported
  if ('serviceWorker' in navigator && 'SyncManager' in window) {
    const reg = await navigator.serviceWorker.ready;
    await reg.sync.register('sync-gps-positions').catch(() => {});
  }
}


async function _onOnline() {
  _onlineMode = true;
  console.log('[GPS] Back online — triggering sync');
  // Trigger immediate fix and sync
  await _collectFix();
  await _triggerSync();
}


function _onOffline() {
  _onlineMode = false;
  console.log('[GPS] Gone offline — queuing mode');
}


async function _triggerSync() {
  if ('serviceWorker' in navigator && 'SyncManager' in window) {
    const reg = await navigator.serviceWorker.ready;
    await reg.sync.register('sync-gps-positions').catch(() => {});
  } else {
    // Fallback — manual sync if Background Sync API not available
    await _manualSync();
  }
}


async function _manualSync() {
  const auth = await getAuth();
  if (!auth?.token) return;

  const { getPendingGps } = await import('./db.js');
  const pending = await getPendingGps();
  if (!pending.length) return;

  console.log(`[GPS] Manual sync: ${pending.length} positions`);

  try {
    const response = await fetch('/api/gps/sync', {
      method:  'POST',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${auth.token}`,
      },
      body: JSON.stringify(pending.map(p => p.data)),
    });

    if (response.ok) {
      await clearGpsQueue();
      _onQueue(0);
      const result = await response.json();
      console.log(`[GPS] Manual sync complete: ${result.created} synced`);
    }
  } catch (err) {
    console.error('[GPS] Manual sync failed:', err);
  }
}
