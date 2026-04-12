/**
 * SARPack TRAILHEAD — db.js
 * IndexedDB wrapper. Handles all local storage for offline operation.
 * Used by gps.js, sync.js, and the UI to read/write local data.
 */

const DB_NAME    = 'trailhead';
const DB_VERSION = 2;

let _db = null;

/**
 * Open the IndexedDB database. Returns a promise resolving to the db instance.
 * Safe to call multiple times — returns cached connection.
 */
export async function openDB() {
  if (_db) return _db;
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);

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
        // General app state — deployment info, incident, etc.
        db.createObjectStore('state', { keyPath: 'key' });
      }
    };

    req.onsuccess = e => {
      _db = e.target.result;
      resolve(_db);
    };
    req.onerror = e => reject(e.target.error);
  });
}


// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function saveAuth(token, user) {
  const db = await openDB();
  return putItem(db, 'auth', { key: 'session', value: { token, user } });
}

export async function getAuth() {
  const db  = await openDB();
  const row = await getItem(db, 'auth', 'session');
  return row?.value || null;
}

export async function clearAuth() {
  const db = await openDB();
  return deleteItem(db, 'auth', 'session');
}


// ---------------------------------------------------------------------------
// App state (deployment, incident, etc.)
// ---------------------------------------------------------------------------

export async function saveState(key, value) {
  const db = await openDB();
  return putItem(db, 'state', { key, value });
}

export async function getState(key) {
  const db  = await openDB();
  const row = await getItem(db, 'state', key);
  return row?.value ?? null;
}


// ---------------------------------------------------------------------------
// GPS queue
// ---------------------------------------------------------------------------

export async function queueGpsFix(fix) {
  const db = await openDB();
  return addItem(db, 'gps_queue', { data: fix, queued_at: new Date().toISOString() });
}

export async function getPendingGps() {
  const db = await openDB();
  return getAllItems(db, 'gps_queue');
}

export async function clearGpsQueue() {
  const db = await openDB();
  return clearStore(db, 'gps_queue');
}

export async function getGpsQueueCount() {
  const db = await openDB();
  return countItems(db, 'gps_queue');
}


// ---------------------------------------------------------------------------
// Patient form queue
// ---------------------------------------------------------------------------

export async function queuePatientForm(form) {
  const db = await openDB();
  return addItem(db, 'patient_queue', { data: form, queued_at: new Date().toISOString() });
}

export async function getPendingPatientForms() {
  const db = await openDB();
  return getAllItems(db, 'patient_queue');
}

export async function deletePatientForm(id) {
  const db = await openDB();
  return deleteItem(db, 'patient_queue', id);
}


// ---------------------------------------------------------------------------
// Radio queue
// ---------------------------------------------------------------------------

export async function queueRadioEntry(entry) {
  const db = await openDB();
  return addItem(db, 'radio_queue', { data: entry, queued_at: new Date().toISOString() });
}

export async function getPendingRadioEntries() {
  const db = await openDB();
  return getAllItems(db, 'radio_queue');
}

export async function deleteRadioEntry(id) {
  const db = await openDB();
  return deleteItem(db, 'radio_queue', id);
}


// ---------------------------------------------------------------------------
// Low-level helpers
// ---------------------------------------------------------------------------

function putItem(db, store, item) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).put(item);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

function addItem(db, store, item) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).add(item);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

function getItem(db, store, key) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readonly');
    const req = tx.objectStore(store).get(key);
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}

function deleteItem(db, store, key) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).delete(key);
    req.onsuccess = () => resolve();
    req.onerror   = e => reject(e.target.error);
  });
}

function getAllItems(db, store) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readonly');
    const req = tx.objectStore(store).getAll();
    req.onsuccess = e => resolve(e.target.result || []);
    req.onerror   = e => reject(e.target.error);
  });
}

function clearStore(db, store) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readwrite');
    const req = tx.objectStore(store).clear();
    req.onsuccess = () => resolve();
    req.onerror   = e => reject(e.target.error);
  });
}

function countItems(db, store) {
  return new Promise((resolve, reject) => {
    const tx  = db.transaction(store, 'readonly');
    const req = tx.objectStore(store).count();
    req.onsuccess = e => resolve(e.target.result);
    req.onerror   = e => reject(e.target.error);
  });
}
