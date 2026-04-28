# TRAILHEAD

**SARPack Field Operator App**

TRAILHEAD is the mobile-first Progressive Web App (PWA) component of the SARPack platform. It puts real-time situational awareness, GPS tracking, patient assessment, and radio logging in the hands of field operators — no app store required, and it works offline.

---

## Screenshots

<table>
<tr>
<td align="center" width="20%">
<img src="../docs/screenshots/trailhead/th_login.png" alt="Login Screen" width="180"/><br/>
<sub><b>Login</b></sub>
</td>
<td align="center" width="20%">
<img src="../docs/screenshots/trailhead/th_main.png" alt="Main / Map Screen" width="180"/><br/>
<sub><b>Map</b></sub>
</td>
<td align="center" width="20%">
<img src="../docs/screenshots/trailhead/th_status.png" alt="Status Screen" width="180"/><br/>
<sub><b>Status</b></sub>
</td>
<td align="center" width="20%">
<img src="../docs/screenshots/trailhead/th_patient.png" alt="Patient Assessment Screen" width="180"/><br/>
<sub><b>Patient Assessment</b></sub>
</td>
<td align="center" width="20%">
<img src="../docs/screenshots/trailhead/th_radio.png" alt="Radio Log Screen" width="180"/><br/>
<sub><b>Radio Log</b></sub>
</td>
</tr>
</table>

---

## Overview

Field operators install TRAILHEAD directly from their phone browser by visiting the TRAILHEAD URL and tapping **Add to Home Screen**. Once installed, TRAILHEAD runs as a standalone app with offline capability. All data syncs back to BASECAMP automatically when a connection is restored.

TRAILHEAD is designed for use in degraded network environments — dense terrain, remote wilderness, or infrastructure failures — where connectivity cannot be guaranteed.

---

## Installation (Field Operator)

1. Ensure TRAILHEAD is running on the local SARPack server
2. Connect your phone to the same network (Wi-Fi or hotspot)
3. Open your phone browser and navigate to the TRAILHEAD URL:
   ```
   https://<server-ip>:<PORT_TRAILHEAD>
   ```
4. Tap **Add to Home Screen** when prompted (or via browser menu)
5. Log in with your SARPack credentials
6. TRAILHEAD will cache itself for offline use automatically

> **Note:** TRAILHEAD requires HTTPS because mobile browsers require a secure context for GPS access. The server uses a self-signed certificate — accept the browser warning on first load.

---

## Running the Server

```bash
python -m trailhead.app
```

Or launched automatically as part of the full SARPack platform:

```bash
python sarpack.py
```

Default port is defined in `core/config.py` as `PORT_TRAILHEAD`.

---

## Screens

### Login

<img src="../docs/screenshots/trailhead/th_login.png" alt="TRAILHEAD Login" width="300"/>

The login screen is the entry point for all field operators. Sign in with your SARPack credentials — the same username and password used across all SARPack apps. TRAILHEAD uses the shared platform auth system, so no separate account is needed.

---

### Status

<img src="../docs/screenshots/trailhead/th_status.png" alt="TRAILHEAD Status Screen" width="800"/>

The Status screen is the home screen. It shows the operator's current deployment at a glance:

- Incident name and number
- Assigned role, division, and team
- Assigned search segment and segment status
- Check-in time
- Incident county and state

Refreshes automatically on load and when returning from other screens. If the operator is not checked in to an active incident, a clear message is displayed.

---

### Map

<img src="../docs/screenshots/trailhead/th_main.png" alt="TRAILHEAD Map Screen" width="800"/>

An interactive Leaflet map centered on the incident location. Displays:

- The operator's current GPS position with a live indicator
- The operator's recorded track for the current incident
- All active team members with their last known positions
- Search segment boundaries with color-coded status overlays:
  - **Unassigned** — available for tasking
  - **Assigned** — team actively searching
  - **Cleared** — search complete
  - **Suspended** — search paused

GPS position updates every 30 seconds while online. In offline mode, positions are queued and synced on reconnect.

---

### Patient Assessment

<img src="../docs/screenshots/trailhead/th_patient.png" alt="TRAILHEAD Patient Assessment Screen" width="800"/>

A structured field form for documenting a located subject. Captures:

- **Demographics** — name, age, sex
- **Chief complaint** — free text with category (Trauma / Medical / Environmental / Behavioral / Unknown)
- **Mechanism of injury**
- **Scene location** — description and GPS coordinates
- **Level of consciousness** — Alert / Verbal / Pain / Unresponsive
- **Vitals** — heart rate, blood pressure, respiratory rate, SpO2, temperature, GCS score, pupils, skin condition
- **Physical exam findings**
- **Treatment given**
- **Disposition** — transport method, release, or still on scene

Assessments are submitted to the server and immediately visible to the IC in BASECAMP. Assessment data also feeds into the ICS-206 Medical Plan compiled in LOGBOOK.

---

### Radio Log

<img src="../docs/screenshots/trailhead/th_radio.png" alt="TRAILHEAD Radio Log Screen" width="800"/>

Allows field operators to log radio transmissions directly from the field. Each entry captures the channel, message content, and timestamp. Entries are appended to the incident radio log and visible in BASECAMP's radio panel in real time.

---

## GPS Tracking

TRAILHEAD tracks operator position continuously during active deployments.

**Live mode (online):** Position is pushed to the server every 30 seconds via `POST /api/gps/position`. BASECAMP receives each fix immediately via SocketIO and updates the live map.

**Offline mode:** When connectivity is lost, positions are queued in the browser's IndexedDB. On reconnect, the full queue is flushed via `POST /api/gps/sync` (up to 5,000 positions per request). Original timestamps are preserved — the recorded track is accurate even after a multi-hour offline period.

---

## Offline Capability

TRAILHEAD uses a Service Worker (`sw.js`) to cache the app shell, static assets, and the most recent API responses. Once installed, the following features remain available without a network connection:

- Viewing last known deployment status
- Viewing the cached incident map
- Capturing patient assessments (queued for sync)
- Logging radio entries (queued for sync)
- Recording GPS positions (queued for sync)

A connection status indicator at the top of the app shows **ONLINE** or **OFFLINE** at all times.

---

## API Endpoints

All endpoints require authentication via the shared SARPack session cookie.

### Operator
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/operator/me` | Bootstrap call — returns deployment, segment, and last GPS fix |
| `GET` | `/api/operator/incident/<id>` | Lightweight incident summary for map screen |
| `POST` | `/api/operator/radio` | Log a radio transmission |
| `GET` | `/api/operator/checkin-status/<incident_id>` | Confirm deployment status for an incident |

### GPS
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/gps/position` | Push a single live GPS fix |
| `POST` | `/api/gps/sync` | Bulk sync of queued offline positions |
| `GET` | `/api/gps/track/<incident_id>` | Retrieve operator's full GPS track |
| `GET` | `/api/gps/last-position/<incident_id>` | Retrieve most recent GPS fix |

### Patient Assessment
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/patient/` | Create a new patient assessment |
| `PATCH` | `/api/patient/<id>` | Update an existing assessment |
| `GET` | `/api/patient/<id>` | Retrieve a single assessment |
| `GET` | `/api/patient/incident/<incident_id>` | All assessments for an incident |
| `GET` | `/api/patient/options` | Valid dropdown values for the assessment form |

### Auth (shared with platform)
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/users/login` | Authenticate and receive session cookie |
| `POST` | `/api/users/logout` | Revoke session |
| `GET` | `/api/users/me` | Current user profile |

---

## Roles & Permissions

TRAILHEAD respects the same role system as the rest of SARPack. Access by role:

| Role | Access |
|------|--------|
| `field_op` | Full TRAILHEAD access — intended primary role for field operators |
| `IC` | Full access — IC can use TRAILHEAD for field coordination |
| `ops_chief` | Full access |
| `logistics` | Full access |
| `observer` | Read-only — can view status and map, cannot submit forms or log radio |

Field operators have no write access to incident management data. They cannot create or modify incidents, deployments, or search segments — those are managed by the IC and Ops Chief in BASECAMP and WARDEN.

---

## Prerequisites

- Python 3.11+
- Flask
- A valid SARPack user account with a linked personnel record
  (user accounts without a linked personnel record cannot use TRAILHEAD — contact your IC or logistics officer)
- HTTPS-capable network connection for initial install and GPS access
- Mobile browser supporting PWA install (Chrome for Android, Safari for iOS 16.4+)

---

## Integration with the SARPack Platform

TRAILHEAD does not operate in isolation. It is one of five interconnected SARPack apps:

| App | Role in relation to TRAILHEAD |
|-----|-------------------------------|
| **BASECAMP** | Receives live GPS updates and patient assessments; displays operator positions on the IC map |
| **WARDEN** | Manages the personnel records that TRAILHEAD user accounts must be linked to |
| **LOGBOOK** | Compiles patient assessment data into ICS-206 Medical Plan forms |
| **TRAILHEAD** | Field — this app |

All five apps share the same SQLite database and authentication system.

---

## File Structure

```
trailhead/
├── app.py                  # Flask app factory, route registration, PWA serving
├── routes/
│   ├── operator.py         # Deployment status, incident summary, radio log
│   ├── gps.py              # Live position push and offline bulk sync
│   └── patient.py          # Patient assessment CRUD
├── static/
│   ├── js/
│   │   ├── app.js          # Main app controller, screen routing, auth state
│   │   ├── db.js           # IndexedDB wrapper for offline queue
│   │   ├── gps.js          # GPS acquisition and push logic
│   │   ├── map.js          # Leaflet map controller
│   │   └── sw.js           # Service Worker — caching and offline sync
│   └── manifest.json       # PWA manifest
└── templates/
    └── index.html          # Single-page app shell
```