/**
 * SARPack TRAILHEAD — Service Worker
 * Handles offline caching, background sync for GPS positions and patient forms.
 *
 * Cache strategy:
 *   App shell (HTML, CSS, JS) — Cache First, update in background
 *   Map tiles (OpenTopoMap)   — Cache First, never expire (tiles don't change)
 *   API calls                 — Network First, fallback to cache
 *   GPS pushes (offline)      — Queue in IndexedDB, sync on reconnect
 */

const CACHE_VERSION = 'trailhead-v1';
const SHELL_CACHE   = `${CACHE_VERSION}-shell`;
const TILE_CACHE    = `${CACHE_VERSION}-tiles`;
const API_CACHE     = `${CACHE_VERSION}-api`;

// App shell files to cache on install
const SHELL_FILES = [
  '/',
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/js/db.js',
  '/static/js/gps.js',
  '/static/js/map.js',
  '/static/js/sync.js',
  '/manifest.json',
];

// Sync queue tag names
const SYNC_GPS     = 'sync-gps-positions';
const SYNC_PATIENT = 'sync-patient-forms';
const SYNC_RADIO   = 'sync-radio-entries';


// ---------------------------------------------------------------------------
// Install — cache app shell
// ---------------------------------------------------------------------------

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then(cache => {
      console.log('[SW] Caching app shell');
      return cache.addAll(SHELL_FILES).catch(err => {
        console.warn('[SW] Shell cache partial failure:', err);
        // Continue even if some files fail — offline will be degraded but functional
      });
    }).then(() => self.skipWaiting())
  );
});


// ---------------------------------------------------------------------------
// Activate — clean up old caches
// ---------------------------------------------------------------------------

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys
          .filter(key => key.startsWith('trailhead-') && !key.startsWith(CACHE_VERSION))
          .map(key => {
            console.log('[SW] Deleting old cache:', key);
            return caches.delete(key);
          })
      );
    }).then(() => self.clients.claim())
  );
});


// ---------------------------------------------------------------------------
// Fetch — route requests through appropriate cache strategy
// ---------------------------------------------------------------------------

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Map tiles — Cache First (tiles never change)
  if (url.hostname.includes('opentopomap.org') ||
      url.hostname.includes('tile.openstreetmap.org')) {
    event.respondWith(cacheTileFirst(event.request));
    return;
  }

  // API calls — Network First with cache fallback
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirstApi(event.request));
    return;
  }

  // Service worker itself — never cache
  if (url.pathname === '/sw.js') {
    return;
  }

  // App shell — Cache First, update in background
  event.respondWith(cacheFirstShell(event.request));
});


// ---------------------------------------------------------------------------
// Cache strategies
// ---------------------------------------------------------------------------

async function cacheFirstShell(request) {
  const cached = await caches.match(request);
  if (cached) {
    // Update cache in background
    fetch(request).then(response => {
      if (response.ok) {
        caches.open(SHELL_CACHE).then(cache => cache.put(request, response));
      }
    }).catch(() => {});
    return cached;
  }
  // Not cached — fetch and cache
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Offline and not cached — return offline page if available
    return caches.match('/') || new Response('Offline', { status: 503 });
  }
}


async function cacheTileFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(TILE_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('', { status: 503 });
  }
}


async function networkFirstApi(request) {
  try {
    const response = await fetch(request);
    // Cache successful GET responses
    if (response.ok && request.method === 'GET') {
      const cache = await caches.open(API_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Offline — try cache
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({ error: 'Offline — data not available', offline: true }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}


// ---------------------------------------------------------------------------
// Background sync — replay queued operations when back online
// ---------------------------------------------------------------------------

self.addEventListener('sync', event => {
  if (event.tag === SYNC_GPS) {
    event.waitUntil(syncGpsPositions());
  } else if (event.tag === SYNC_PATIENT) {
    event.waitUntil(syncPatientForms());
  } else if (event.tag === SYNC_RADIO) {
    event.waitUntil(syncRadioEntries());
  }
});


async function syncGpsPositions() {
  const db    = await openDB();
  const items = await getAllPending(db, 'gps_queue');
  if (!items.length) return;

  console.log(`[SW] Syncing ${items.length} queued GPS positions`);

  try {
    // Get auth token from IndexedDB
    const auth = await getAuth(db);
    if (!auth?.token) {
      console.warn('[SW] No auth token — GPS sync deferred');
      return;
    }

    const response = await fetch('/api/gps/sync', {
      method:  'POST',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': `Bearer ${auth.token}`,
      },
      body: JSON.stringify(items.map(i => i.data)),
    });

    if (response.ok) {
      const result = await response.json();
      console.log(`[SW] GPS sync complete: ${result.created} created, ${result.failed} failed`);
      // Clear successfully synced items
      await clearPending(db, 'gps_queue');
      // Notify all open clients
      broadcastToClients({ type: 'GPS_SYNC_COMPLETE', result });
    }
  } catch (err) {
    console.error('[SW] GPS sync failed:', err);
  }
}


async function syncPatientForms() {
  const db    = await openDB();
  const items = await getAllPending(db, 'patient_queue');
  if (!items.length) return;

  console.log(`[SW] Syncing ${items.length} queued patient forms`);

  const auth = await getAuth(db);
  if (!auth?.token) return;

  for (const item of items) {
    try {
      const response = await fetch('/api/patient/', {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${auth.token}`,
        },
        body: JSON.stringify(item.data),
      });

      if (response.ok) {
        await deletePending(db, 'patient_queue', item.id);
        broadcastToClients({ type: 'PATIENT_SYNC_COMPLETE', item: item.data });
      }
    } catch (err) {
      console.error('[SW] Patient form sync failed:', err);
    }
  }
}


async function syncRadioEntries() {
  const db    = await openDB();
  const items = await getAllPending(db, 'radio_queue');
  if (!items.length) return;

  const auth = await getAuth(db);
  if (!auth?.token) return;

  for (const item of items) {
    try {
      const response = await fetch('/api/operator/radio', {
        method:  'POST',
        headers: {
          'Content-Type':  'application/json',
          'Authorization': `Bearer ${auth.token}`,
        },
        body: JSON.stringify(item.data),
      });

      if (response.ok) {
        await deletePending(db, 'radio_queue', item.id);
      }
    } catch (err) {
      console.error('[SW] Radio sync failed:', err);
    }
  }
}


// ---------------------------------------------------------------------------
// IndexedDB helpers (minimal, no library dependencies)
// ---------------------------------------------------------------------------

function openDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open('trailhead', 2);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains('gps_queue')) {
        db.createObjectStore('gps_queue', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('patient_queue')) {
        db.createObjectStore('patient_queue', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('radio_queue')) {
        db.createObjectStore('radio_queue', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('auth')) {
        db.createObjectStore('auth', { keyPath: 'key' });
      }
      if (!db.objectStoreNames.contains('state')) {
        db.createObjectStore('state', { keyPath: 'key' });
      }
    };
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

function getAllPending(db, store) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readonly');
    const req = tx.objectStore(store).getAll();
    req.onsuccess = e => resolve(e.target.result || []);
    req.onerror   = e => reject(e.target.error);
  });
}

function clearPending(db, store) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).clear();
    req.onsuccess = () => resolve();
    req.onerror   = e => reject(e.target.error);
  });
}

function deletePending(db, store, id) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).delete(id);
    req.onsuccess = () => resolve();
    req.onerror   = e => reject(e.target.error);
  });
}

function getAuth(db) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction('auth', 'readonly');
    const req = tx.objectStore('auth').get('session');
    req.onsuccess = e => resolve(e.target.result?.value || null);
    req.onerror   = e => reject(e.target.error);
  });
}

function broadcastToClients(message) {
  self.clients.matchAll({ type: 'window' }).then(clients => {
    clients.forEach(client => client.postMessage(message));
  });
}
