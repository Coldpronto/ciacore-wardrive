#!/usr/bin/env python3
"""Small desktop launcher for Kismet's wardrive configuration overlay."""

from __future__ import annotations

import base64
import concurrent.futures
import csv
import http.server
import math
import os
import queue
import secrets
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import json
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Kismet Wardrive Launcher"
OUTPUT_BATCH_LINES = 200
OUTPUT_MAX_LINES = 5000
AP_ACTIVITY_WINDOW_SECONDS = 15
API_POLL_SECONDS = 1.0
ADAPTER_STALL_SECONDS = 12.0
CHANNEL_VERIFY_SECONDS = 8.0
CHANNEL_PLANS: dict[str, tuple[str, str]] = {
    "All supported": ("hop", ""),
    "2.4 GHz priority (1, 6, 11)": ("hop", "1,6,11"),
    "2.4 GHz all (US 1-11)": ("hop", "1,2,3,4,5,6,7,8,9,10,11"),
    "5 GHz non-DFS": ("hop", "36,40,44,48,149,153,157,161,165"),
    "5 GHz DFS": ("hop", "52,56,60,64,100,104,108,112,116,120,124,128,132,136,140,144"),
    "5 GHz all": ("hop", "36,40,44,48,52,56,60,64,100,104,108,112,116,120,124,128,132,136,140,144,149,153,157,161,165"),
    "2.4 + 5 GHz": ("hop", "1,2,3,4,5,6,7,8,9,10,11,36,40,44,48,52,56,60,64,100,104,108,112,116,120,124,128,132,136,140,144,149,153,157,161,165"),
    "6 GHz PSC": ("hop", "5,21,37,53,69,85,101,117,133,149,165,181,197,213,229"),
}
CUSTOM_HOP_PLAN = "Custom hop"
FIXED_CHANNEL_PLAN = "Fixed channel"
ASSET_DIR = Path(__file__).resolve().parent / "assets"

# WDGoWars-inspired command-center palette.  Kept here instead of scattered
# through the UI so the skin can be adjusted without touching application logic.
COLORS = {
    "void": "#03090e",
    "panel": "#07131b",
    "panel_2": "#0b1d27",
    "line": "#174252",
    "cyan": "#00d9ff",
    "cyan_dim": "#1496ad",
    "amber": "#ffb52e",
    "text": "#d9eef3",
    "muted": "#7895a0",
    "danger": "#ff4057",
    "success": "#29e58c",
}

WDGWARS_BASE_URL = "https://wdgwars.pl"
WDGWARS_MAX_UPLOAD = 40 * 1024 * 1024
WDGWARS_USER_AGENT = "CIACORE-Wardrive/1.0"
ADSB_JSON_PATH = Path("/run/readsb/aircraft.json")
MUNINN_SCRIPT = Path(__file__).resolve().parent.parent / "adsb-to-wdgwars" / "muninn.py"
SETTINGS_PATH = Path.home() / ".config" / "ciacore-wardrive" / "settings.json"
UPLOAD_HISTORY_PATH = Path.home() / ".config" / "ciacore-wardrive" / "uploads.json"
PROFILES_PATH = Path.home() / ".config" / "ciacore-wardrive" / "profiles.json"
KISMET_HTTP_ASSETS = Path("/usr/share/kismet/httpd")
MAP_TILE_CACHE = Path.home() / ".cache" / "ciacore-wardrive" / "tiles"

LIVE_MAP_HTML = """<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>CIACORE Live Wardrive Map</title><link rel=stylesheet href=/assets/leaflet.css>
<style>html,body,#map{height:100%;margin:0}#status{position:absolute;z-index:1000;top:10px;left:50px;background:#07151ddd;color:#d9eef3;padding:9px 13px;border:1px solid #00d9ff;border-radius:5px;font:14px sans-serif}</style>
</head><body><div id=status>Waiting for GPS-tagged access points...</div><div id=map></div>
<script src=/assets/leaflet.js></script><script>
const map=L.map('map').setView([27.95,-82.46],11);
L.tileLayer('/tiles/{z}/{x}/{y}.png',{maxZoom:19,attribution:'&copy; OpenStreetMap • cached for offline reuse'}).addTo(map);
const markers=new Map(),planes=new Map(); const route=L.polyline([],{color:'#00d9ff',weight:4,opacity:.8}).addTo(map);const gaps=L.layerGroup().addTo(map); let fitted=false;
function color(r){return r>=-50?'#29e58c':r>=-70?'#ffb52e':'#ff4057'}
function esc(v){const d=document.createElement('div');d.textContent=String(v??'');return d.innerHTML}
async function refresh(){try{const r=await fetch('/api/points',{cache:'no-store'});const pts=await r.json();
 const seen=new Set(); pts.forEach(p=>{seen.add(p.key);const html=`<b>${p.name}</b><br>${p.mac}<br>${p.signal==null?'No signal':p.signal+' dBm'}`;
  if(markers.has(p.key)){markers.get(p.key).setLatLng([p.lat,p.lon]).setPopupContent(html)}else{markers.set(p.key,L.circleMarker([p.lat,p.lon],{radius:7,color:color(p.signal),fillOpacity:.75}).bindPopup(html).addTo(map))}});
 markers.forEach((m,k)=>{if(!seen.has(k)){map.removeLayer(m);markers.delete(k)}});
 const tr=await (await fetch('/api/track',{cache:'no-store'})).json(); route.setLatLngs(tr.map(p=>[p.lat,p.lon]));
 const ac=await (await fetch('/api/aircraft',{cache:'no-store'})).json(),alive=new Set();ac.forEach(p=>{alive.add(p.hex);const pos=[p.lat,p.lon],rad=(p.track||0)*Math.PI/180,len=.035,end=[p.lat+Math.cos(rad)*len,p.lon+Math.sin(rad)*len/Math.max(.3,Math.cos(p.lat*Math.PI/180))];const html=`<b>✈ ${esc(p.flight||p.hex.toUpperCase())}</b><br>ICAO ${esc(p.hex.toUpperCase())}<br>${esc(p.alt_baro??'—')} ft • ${esc(p.gs??'—')} kt • ${esc(Math.round(p.track||0))}°`;if(planes.has(p.hex)){const q=planes.get(p.hex);q.dot.setLatLng(pos).setPopupContent(html);q.line.setLatLngs([pos,end])}else{planes.set(p.hex,{dot:L.circleMarker(pos,{radius:6,color:'#f0abfc',fillColor:'#a855f7',fillOpacity:.9}).bindPopup(html).addTo(map),line:L.polyline([pos,end],{color:'#f0abfc',weight:2}).addTo(map)})}});planes.forEach((p,k)=>{if(!alive.has(k)){map.removeLayer(p.dot);map.removeLayer(p.line);planes.delete(k)}});
 gaps.clearLayers();tr.filter(p=>p.gap).forEach(p=>L.circleMarker([p.lat,p.lon],{radius:8,color:'#ff4057',fillOpacity:.8}).bindTooltip('GPS coverage gap').addTo(gaps));
 document.getElementById('status').textContent=`${pts.length} GPS-tagged AP${pts.length===1?'':'s'} • ${ac.length} aircraft • ${tr.length} route points — live (base map needs internet)`;
 if(!fitted&&pts.length){map.fitBounds(L.latLngBounds(pts.map(p=>[p.lat,p.lon])).pad(.15),{maxZoom:17});fitted=true}
 }catch(e){document.getElementById('status').textContent='Waiting for Kismet data...'}}
refresh();setInterval(refresh,2000);
</script></body></html>""".encode("utf-8")

REPLAY_MAP_HTML = """<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Wardrive Replay</title><link rel=stylesheet href=/assets/leaflet.css><style>html,body,#map{height:100%;margin:0}#hud{position:absolute;z-index:1000;top:10px;left:50px;right:50px;background:#07151dee;color:#d9eef3;padding:10px;border:1px solid #00d9ff;font:14px sans-serif}input{width:70%}</style></head>
<body><div id=hud><button id=play>Play</button> <input id=timeline type=range min=0 value=0> <span id=status>Loading…</span></div><div id=map></div>
<script src=/assets/leaflet.js></script><script>const map=L.map('map').setView([27.95,-82.46],11);L.tileLayer('/tiles/{z}/{x}/{y}.png',{maxZoom:19}).addTo(map);
let pts=[],shown=[],timer=null;const line=L.polyline([],{color:'#00d9ff'}).addTo(map),range=document.getElementById('timeline');
function draw(n){shown.forEach(m=>map.removeLayer(m));shown=[];const slice=pts.slice(0,n);slice.forEach(p=>shown.push(L.circleMarker([p.lat,p.lon],{radius:5,color:p.rssi>=-50?'#29e58c':p.rssi>=-70?'#ffb52e':'#ff4057'}).bindPopup(`<b>${p.ssid||'&lt;hidden&gt;'}</b><br>${p.bssid}`).addTo(map)));line.setLatLngs(slice.map(p=>[p.lat,p.lon]));range.value=n;document.getElementById('status').textContent=`${n} / ${pts.length} observations`}
fetch('/api/replay').then(r=>r.json()).then(p=>{pts=p;range.max=pts.length;draw(pts.length);if(pts.length)map.fitBounds(line.getBounds().pad(.1),{maxZoom:17})});range.oninput=()=>draw(+range.value);document.getElementById('play').onclick=()=>{if(timer){clearInterval(timer);timer=null;return}draw(0);timer=setInterval(()=>{let n=+range.value+1;draw(n);if(n>=pts.length){clearInterval(timer);timer=null}},Math.max(30,5000/Math.max(1,pts.length)))}</script></body></html>""".encode()


def kismet_web_url(username: str, password: str) -> str:
    """Return Kismet's loopback URL.

    Modern browsers intentionally ignore credentials embedded in URLs.  Kismet's
    own login panel handles authentication, so open a plain URL and give the user
    the launcher's per-session credentials separately.
    """
    return "http://127.0.0.1:2501/"


def valid_wdgwars_api_key(value: str) -> bool:
    """Return whether value has the documented 64-character hex key shape."""
    key = value.strip()
    return len(key) == 64 and all(character in "0123456789abcdefABCDEF" for character in key)


def load_wdgwars_api_key(settings_path: Path = SETTINGS_PATH) -> str:
    """Load a saved key, ignoring missing, malformed, or unsafe settings."""
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        key = data.get("wdgwars_api_key", "") if isinstance(data, dict) else ""
        return key if isinstance(key, str) and valid_wdgwars_api_key(key) else ""
    except (OSError, ValueError):
        return ""


def save_wdgwars_api_key(api_key: str, settings_path: Path = SETTINGS_PATH) -> None:
    """Persist the key atomically in a file readable only by the current user."""
    key = api_key.strip()
    if not valid_wdgwars_api_key(key):
        raise ValueError("Cannot save an invalid WDGWars API key")
    settings_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=settings_path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            fd = -1
            json.dump({"wdgwars_api_key": key}, temporary)
            temporary.write("\n")
        os.replace(temporary_name, settings_path)
        settings_path.chmod(0o600)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            Path(temporary_name).unlink()
        except FileNotFoundError:
            pass


def wdgwars_account(api_key: str, timeout: int = 15) -> dict[str, object]:
    """Fetch the WDGWars profile associated with an API key."""
    request = urllib.request.Request(
        f"{WDGWARS_BASE_URL}/api/me",
        headers={
            "X-API-Key": api_key.strip(),
            "Accept": "application/json",
            "User-Agent": WDGWARS_USER_AGENT,
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {502, 503, 504} or attempt == 2:
                raise
            time.sleep(1 + attempt * 2)
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError(str(result.get("error", "WDGWars rejected the API key")) if isinstance(result, dict)
                         else "WDGWars returned an invalid response")
    return result


def upload_wdgwars_csv(api_key: str, csv_path: Path, timeout: int = 120) -> dict[str, object]:
    """Upload one Kismet/WiGLE CSV using WDGWars' documented multipart API."""
    size = csv_path.stat().st_size
    if size > WDGWARS_MAX_UPLOAD:
        raise ValueError("WDGWars uploads are limited to 40 MiB per file")
    boundary = f"----ciacore-{secrets.token_hex(16)}"
    # Kismet names WiGLE exports ``*.wiglecsv``, but WDGWars only accepts
    # uploads whose multipart filename ends in .csv, .log, or .gz.
    upload_name = csv_path.with_suffix(".csv").name if csv_path.suffix.lower() == ".wiglecsv" else csv_path.name
    filename = upload_name.replace('"', "_")
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: text/csv\r\n\r\n"
    ).encode("utf-8")
    body = prefix + csv_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("ascii")
    request = urllib.request.Request(
        f"{WDGWARS_BASE_URL}/api/upload-csv",
        data=body,
        method="POST",
        headers={
            "X-API-Key": api_key.strip(),
            "Accept": "application/json",
            "User-Agent": WDGWARS_USER_AGENT,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict) or result.get("ok") is False:
        raise ValueError(str(result.get("error", "WDGWars rejected the upload")) if isinstance(result, dict)
                         else "WDGWars returned an invalid response")
    return result


def adsb_snapshot_status(path: Path = ADSB_JSON_PATH) -> tuple[int, int, int]:
    """Return total aircraft, positioned aircraft, and decoder message count."""
    data = json.loads(path.read_text(encoding="utf-8"))
    aircraft = data.get("aircraft", []) if isinstance(data, dict) else []
    if not isinstance(aircraft, list):
        raise ValueError("readsb returned an invalid aircraft list")
    positioned = sum(
        1 for item in aircraft
        if isinstance(item, dict) and isinstance(item.get("lat"), (int, float))
        and isinstance(item.get("lon"), (int, float))
    )
    messages = data.get("messages", 0) if isinstance(data, dict) else 0
    return len(aircraft), positioned, int(messages) if isinstance(messages, (int, float)) else 0


def read_adsb_aircraft(path: Path = ADSB_JSON_PATH,
                       receiver: tuple[float, float] | None = None) -> list[dict[str, object]]:
    """Read live readsb rows and add receiver-relative distance and bearing."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("aircraft", []) if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, object]] = []
    for source in rows:
        if not isinstance(source, dict) or not source.get("hex"):
            continue
        row = dict(source)
        row["flight"] = str(row.get("flight", "")).strip()
        lat, lon = row.get("lat"), row.get("lon")
        if receiver and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            lat1, lon1, lat2, lon2 = map(math.radians, (*receiver, float(lat), float(lon)))
            dlat, dlon = lat2 - lat1, lon2 - lon1
            chord = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
            row["distance_nm"] = 3440.065 * 2 * math.asin(min(1.0, math.sqrt(chord)))
            y = math.sin(dlon) * math.cos(lat2)
            x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
            row["bearing"] = (math.degrees(math.atan2(y, x)) + 360) % 360
        result.append(row)
    return sorted(result, key=lambda row: (float(row.get("distance_nm", 1e9)), float(row.get("seen", 1e9))))


def filter_adsb_aircraft(rows: list[dict[str, object]], mode: str) -> list[dict[str, object]]:
    """Apply the aircraft view's operational filters."""
    if mode == "Nearby (<50 nm)":
        return [row for row in rows if float(row.get("distance_nm", 1e9)) < 50]
    if mode == "Low (<10,000 ft)":
        return [row for row in rows if isinstance(row.get("alt_baro"), (int, float))
                and float(row["alt_baro"]) < 10_000]
    if mode == "Climbing":
        return [row for row in rows if float(row.get("baro_rate", row.get("geom_rate", 0)) or 0) > 128]
    if mode == "Descending":
        return [row for row in rows if float(row.get("baro_rate", row.get("geom_rate", 0)) or 0) < -128]
    if mode == "Emergency":
        return [row for row in rows if str(row.get("emergency", "none")) != "none"
                or str(row.get("squawk", "")) in {"7500", "7600", "7700"}]
    if mode == "Stale (>10s)":
        return [row for row in rows if float(row.get("seen", 0) or 0) > 10]
    return rows


def upload_wdgwars_adsb(api_key: str, json_path: Path = ADSB_JSON_PATH,
                         muninn_script: Path = MUNINN_SCRIPT, timeout: int = 120) -> str:
    """Upload a readsb snapshot through WDGWars' recommended signed Muninn transport."""
    key = api_key.strip()
    if not valid_wdgwars_api_key(key):
        raise ValueError("WDGWars API keys must contain 64 hexadecimal characters")
    if not json_path.is_file():
        raise FileNotFoundError(f"ADS-B snapshot not found: {json_path}")
    if not muninn_script.is_file():
        raise FileNotFoundError(f"Muninn uploader not found: {muninn_script}")
    environment = os.environ.copy()
    environment["WDGWARS_API_KEY"] = key
    vendored = str(muninn_script.parent / "web")
    environment["PYTHONPATH"] = vendored + (os.pathsep + environment["PYTHONPATH"]
                                               if environment.get("PYTHONPATH") else "")
    result = subprocess.run(
        [shutil.which("python3") or "python3", str(muninn_script), str(json_path),
         "--upload", "--no-save", "--no-version-check", "--quiet"],
        capture_output=True, text=True, timeout=timeout, env=environment,
    )
    detail = (result.stdout + "\n" + result.stderr).strip().replace(key, "[REDACTED]")
    if result.returncode:
        raise ValueError(detail or f"Muninn upload failed with exit code {result.returncode}")
    return detail or "accepted"


@dataclass(frozen=True)
class CaptureSource:
    interface: str
    mode: str = "hop"
    channels: str = ""


def channel_plan_for(mode: str, channels: str) -> str:
    """Return the preset label matching a stored source configuration."""
    normalized = ",".join(part.strip() for part in channels.split(",") if part.strip())
    if mode == "fixed":
        return FIXED_CHANNEL_PLAN
    for label, (plan_mode, plan_channels) in CHANNEL_PLANS.items():
        if mode == plan_mode and normalized == plan_channels:
            return label
    return CUSTOM_HOP_PLAN


def hopping_configuration_errors(sources: list[CaptureSource]) -> list[str]:
    """Describe source settings which claim to hop but cannot change channel."""
    errors = []
    for source in sources:
        channels = [part.strip() for part in source.channels.split(",") if part.strip()]
        if source.mode == "hop" and len(channels) == 1:
            errors.append(
                f"{source.interface}: hopping needs at least two channels; "
                f"choose a channel-group preset, leave it blank for all supported channels, "
                f"or use Fixed channel for {channels[0]}"
            )
    return errors


@dataclass(frozen=True)
class SessionStats:
    path: Path
    modified: float
    access_points: int
    hidden: int
    strongest_rssi: int | None
    channels: tuple[str, ...]
    security: tuple[tuple[str, int], ...]
    duration_seconds: int | None


@dataclass(frozen=True)
class NetworkRecord:
    bssid: str
    ssid: str
    security: str
    channel: str
    rssi: int | None
    latitude: float | None
    longitude: float | None
    timestamp: str


@dataclass(frozen=True)
class PreflightItem:
    name: str
    ok: bool
    detail: str


def read_wigle_records(path: Path, limit: int = 1_000_000) -> list[NetworkRecord]:
    """Read unique Wi-Fi records from a Kismet/WiGLE CSV."""
    records: dict[str, NetworkRecord] = {}
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        if not source.readline().lower().startswith("wiglewifi"):
            source.seek(0)
        for index, row in enumerate(csv.DictReader(source)):
            if index >= limit:
                break
            if (row.get("Type") or "WIFI").strip().upper() not in {"WIFI", "802.11"}:
                continue
            bssid = (row.get("MAC") or row.get("BSSID") or "").strip().upper()
            if not bssid:
                continue
            try:
                rssi = int(float(row.get("RSSI") or ""))
            except ValueError:
                rssi = None
            try:
                lat, lon = float(row.get("CurrentLatitude") or ""), float(row.get("CurrentLongitude") or "")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
                    lat = lon = None
            except ValueError:
                lat = lon = None
            candidate = NetworkRecord(
                bssid, (row.get("SSID") or "").strip(), _security_family(row.get("AuthMode") or ""),
                (row.get("Channel") or "").strip(), rssi, lat, lon, (row.get("FirstSeen") or "").strip(),
            )
            previous = records.get(bssid)
            if previous is None or (rssi is not None and (previous.rssi is None or rssi > previous.rssi)):
                records[bssid] = candidate
    return list(records.values())


def compare_sessions(old_path: Path, new_path: Path) -> dict[str, list[NetworkRecord]]:
    old = {record.bssid: record for record in read_wigle_records(old_path)}
    new = {record.bssid: record for record in read_wigle_records(new_path)}
    changed = [new[key] for key in old.keys() & new.keys() if
               (old[key].ssid, old[key].security, old[key].channel) !=
               (new[key].ssid, new[key].security, new[key].channel)]
    return {
        "new": [new[key] for key in sorted(new.keys() - old.keys())],
        "missing": [old[key] for key in sorted(old.keys() - new.keys())],
        "changed": sorted(changed, key=lambda record: record.bssid),
    }


def session_analytics(path: Path) -> dict[str, dict[str, int]]:
    records = read_wigle_records(path)
    channels: dict[str, int] = {}
    security: dict[str, int] = {}
    signals = {"Excellent (≥ -50)": 0, "Good (-69 to -51)": 0, "Weak (< -69)": 0, "Unknown": 0}
    for record in records:
        channels[record.channel or "Unknown"] = channels.get(record.channel or "Unknown", 0) + 1
        security[record.security] = security.get(record.security, 0) + 1
        bucket = "Unknown" if record.rssi is None else ("Excellent (≥ -50)" if record.rssi >= -50 else
                 "Good (-69 to -51)" if record.rssi >= -69 else "Weak (< -69)")
        signals[bucket] += 1
    return {"Channels": channels, "Security": security, "Signal": signals}


def export_geodata(path: Path, destination: Path, kind: str) -> None:
    records = [record for record in read_wigle_records(path) if record.latitude is not None]
    if kind == "geojson":
        payload = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [record.longitude, record.latitude]},
            "properties": {"bssid": record.bssid, "ssid": record.ssid, "security": record.security,
                           "channel": record.channel, "rssi": record.rssi, "timestamp": record.timestamp},
        } for record in records]}
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return
    if kind == "kml":
        root = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
        document = ET.SubElement(root, "Document")
        for record in records:
            mark = ET.SubElement(document, "Placemark")
            ET.SubElement(mark, "name").text = record.ssid or "<hidden>"
            ET.SubElement(mark, "description").text = f"{record.bssid} | {record.security} | ch {record.channel} | {record.rssi} dBm"
            ET.SubElement(ET.SubElement(mark, "Point"), "coordinates").text = f"{record.longitude},{record.latitude},0"
        ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
        return
    if kind == "gpx":
        root = ET.Element("gpx", version="1.1", creator="CIACORE Wardrive", xmlns="http://www.topografix.com/GPX/1/1")
        for record in sorted(records, key=lambda item: item.timestamp):
            point = ET.SubElement(root, "wpt", lat=str(record.latitude), lon=str(record.longitude))
            ET.SubElement(point, "name").text = record.ssid or "<hidden>"
            ET.SubElement(point, "desc").text = f"{record.bssid}; {record.security}; channel {record.channel}; {record.rssi} dBm"
            if record.timestamp:
                ET.SubElement(point, "time").text = record.timestamp.replace(" ", "T") + ("Z" if "T" in record.timestamp and not record.timestamp.endswith("Z") else "")
        ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
        return
    raise ValueError(f"Unsupported export format: {kind}")


def find_recoverable_databases(directory: Path) -> list[Path]:
    """Return Kismet databases that do not have a matching WiGLE export."""
    return sorted((path for path in directory.glob("*.kismet") if not path.with_suffix(".wiglecsv").exists()),
                  key=lambda path: path.stat().st_mtime, reverse=True)


def preflight_checks(sources: list[CaptureSource], log_directory: Path) -> list[PreflightItem]:
    checks = [PreflightItem("Kismet", shutil.which("kismet") is not None, shutil.which("kismet") or "not found in PATH")]
    checks.append(PreflightItem("CSV converter", shutil.which("kismetdb_to_wiglecsv") is not None,
                                shutil.which("kismetdb_to_wiglecsv") or "completed sessions cannot be exported"))
    available = set(network_interfaces())
    for source in sources:
        checks.append(PreflightItem(source.interface, source.interface in available,
                                    "adapter available" if source.interface in available else "adapter missing"))
    try:
        log_directory.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(log_directory)
        writable = os.access(log_directory, os.W_OK)
        checks.append(PreflightItem("Capture directory", writable,
                                    f"{usage.free / (1024 ** 3):.1f} GiB free" if writable else "not writable"))
        checks.append(PreflightItem("Disk space", usage.free >= 512 * 1024 * 1024,
                                    f"{usage.free / (1024 ** 3):.1f} GiB free (512 MiB minimum)"))
    except OSError as exc:
        checks.append(PreflightItem("Capture directory", False, str(exc)))
    try:
        with socket.create_connection(("127.0.0.1", 2947), timeout=.3):
            checks.append(PreflightItem("GPSD", True, "reachable (GPS fix may still be pending)"))
    except OSError:
        checks.append(PreflightItem("GPSD", False, "not reachable; capture can run without coordinates"))
    return checks


def _wigle_timestamp(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _security_family(value: str) -> str:
    upper = value.upper()
    if "WPA3" in upper or "SAE" in upper:
        return "WPA3"
    if "WPA2" in upper or "RSN" in upper:
        return "WPA2"
    if "WPA" in upper:
        return "WPA"
    if "WEP" in upper:
        return "WEP"
    if not upper or upper in {"[]", "[ESS]", "OPEN", "NONE"}:
        return "Open"
    return "Other"


def parse_wigle_session(path: Path) -> SessionStats:
    """Summarize a Kismet/WiGLE CSV without retaining network identifiers."""
    access_points: set[str] = set()
    hidden = 0
    strongest: int | None = None
    channels: set[str] = set()
    security: dict[str, int] = {}
    first_seen: float | None = None
    last_seen: float | None = None
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        first_line = source.readline()
        if not first_line.lower().startswith("wiglewifi"):
            source.seek(0)
        reader = csv.DictReader(source)
        for row_number, row in enumerate(reader):
            if row_number >= 1_000_000:
                break
            kind = (row.get("Type") or "WIFI").strip().upper()
            if kind not in {"WIFI", "802.11"}:
                continue
            try:
                rssi = int(float(row.get("RSSI") or ""))
                strongest = rssi if strongest is None else max(strongest, rssi)
            except ValueError:
                pass
            timestamp = _wigle_timestamp(row.get("FirstSeen") or "")
            if timestamp is not None:
                first_seen = timestamp if first_seen is None else min(first_seen, timestamp)
                last_seen = timestamp if last_seen is None else max(last_seen, timestamp)
            bssid = (row.get("MAC") or row.get("BSSID") or "").strip().upper()
            if not bssid or bssid in access_points:
                continue
            access_points.add(bssid)
            if not (row.get("SSID") or "").strip():
                hidden += 1
            channel = (row.get("Channel") or "").strip()
            if channel:
                channels.add(channel)
            family = _security_family(row.get("AuthMode") or "")
            security[family] = security.get(family, 0) + 1
    duration = int(last_seen - first_seen) if first_seen is not None and last_seen is not None else None
    channel_order = tuple(sorted(channels, key=lambda item: (not item.isdigit(), int(item) if item.isdigit() else item)))
    security_order = ("WPA3", "WPA2", "WPA", "WEP", "Open", "Other")
    return SessionStats(
        path=path, modified=path.stat().st_mtime, access_points=len(access_points), hidden=hidden,
        strongest_rssi=strongest, channels=channel_order,
        security=tuple((name, security[name]) for name in security_order if security.get(name)),
        duration_seconds=duration,
    )


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def load_upload_history(path: Path = UPLOAD_HISTORY_PATH) -> dict[str, str]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(value) for key, value in result.items()} if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def save_upload_history(history: dict[str, str], path: Path = UPLOAD_HISTORY_PATH) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def load_profiles(path: Path = PROFILES_PATH) -> dict[str, dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(name): value for name, value in data.items() if isinstance(value, dict)} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_profiles(profiles: dict[str, dict[str, object]], path: Path = PROFILES_PATH) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(profiles, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def source_definition(source: CaptureSource) -> str:
    """Build one Kismet source definition from launcher settings."""
    if source.mode == "fixed":
        return f"{source.interface}:channel_hop=false,channel={source.channels.strip()}"
    channels = ",".join(part.strip() for part in source.channels.split(",") if part.strip())
    if channels:
        return f'{source.interface}:channel_hop=true,channels="{channels}"'
    return f"{source.interface}:channel_hop=true"


def build_command(
    sources: str | list[CaptureSource],
    log_directory: Path,
    api_config: Path | None = None,
) -> list[str]:
    """Build the Kismet invocation without involving a shell."""
    command = [
        "kismet",
        "--no-ncurses",
        "--no-line-wrap",
        "--override",
        "wardrive",
        "--log-title",
        f"wardrive-{datetime.now():%Y%m%d-%H%M%S}",
        "--log-prefix",
        str(log_directory),
    ]
    if api_config:
        command.extend(["--override", str(api_config)])
    if isinstance(sources, str):
        sources = [CaptureSource(sources)] if sources else []
    for source in sources:
        command.extend(["-c", source_definition(source)])
    return command


def export_wigle_csv(log_directory: Path, log_title: str) -> tuple[Path | None, str]:
    """Create the Wigle CSV for a completed Kismet session."""
    converter = shutil.which("kismetdb_to_wiglecsv")
    if not converter:
        return None, "kismetdb_to_wiglecsv was not found; the Wigle CSV could not be created"

    databases = list(log_directory.glob(f"{log_title}-*.kismet"))
    if not databases:
        return None, f"no Kismet database was found for {log_title}"
    database = max(databases, key=lambda path: path.stat().st_mtime_ns)
    output = database.with_suffix(".wiglecsv")
    try:
        result = subprocess.run(
            [converter, "--in", str(database), "--out", str(output), "--force", "--skip-clean"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Wigle CSV export failed: {exc}"
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        return None, f"Wigle CSV export failed: {detail or f'exit code {result.returncode}'}"
    return output, ""


def network_interfaces() -> list[str]:
    """Return capture-capable wireless interfaces exposed by Linux sysfs.

    Looking only at ``/sys/class/net/*/wireless`` misses some USB drivers while
    they are initializing (and some monitor VIFs). The corresponding phy's
    ``device/net`` directory is the authoritative fallback.
    """
    base = Path("/sys/class/net")
    if not base.is_dir():
        return []
    interfaces = {
        entry.name for entry in base.iterdir()
        if entry.name != "lo" and (entry / "wireless").exists()
    }
    phy_base = Path("/sys/class/ieee80211")
    if phy_base.is_dir():
        for phy in phy_base.iterdir():
            net_dir = phy / "device" / "net"
            if net_dir.is_dir():
                interfaces.update(entry.name for entry in net_dir.iterdir() if (base / entry.name).exists())
    return sorted(interfaces)


def managed_interface_name(interface: str) -> str:
    """Return the likely managed interface behind an airmon-ng monitor VIF."""
    if interface.endswith("mon") and len(interface) > 3:
        candidate = interface[:-3]
        if (Path("/sys/class/net") / candidate).exists():
            return candidate
    return interface


def monitor_interface_name(interface: str) -> str | None:
    """Find the monitor VIF used for a selected managed or monitor interface."""
    if interface.endswith("mon"):
        return interface if (Path("/sys/class/net") / interface).exists() else None
    candidate = f"{interface}mon"
    return candidate if (Path("/sys/class/net") / candidate).exists() else None


def gps_text(value: object, suffix: str = "", precision: int = 6) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.{precision}f}{suffix}"


def gps_device_kind(path: str) -> str:
    """Return a useful receiver class from common Linux GPS device paths."""
    lowered = path.lower()
    return "USB" if any(marker in lowered for marker in ("ttyusb", "ttyacm", "/usb", "usb:")) else "Internal"


def gps_device_choices(devices: object) -> list[tuple[str, str]]:
    """Extract ``(display label, GPSD path)`` choices from a DEVICES report."""
    if not isinstance(devices, list):
        return []
    choices = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        path = device.get("path")
        if isinstance(path, str) and path.strip():
            clean = path.strip()
            choices.append((f"{gps_device_kind(clean)} — {clean}", clean))
    return sorted(set(choices), key=lambda item: (item[0].split(" — ", 1)[0] != "Internal", item[0]))


def gps_report_device(report: object) -> str:
    """Normalize a GPSD report's receiver path for stable source selection."""
    if not isinstance(report, dict):
        return ""
    device = report.get("device")
    return device.strip() if isinstance(device, str) else ""


def gps_active_device(active: str, configured: str, report: object) -> str:
    """Choose a receiver only after it supplies a fix in automatic mode."""
    if configured:
        return configured
    if active or not isinstance(report, dict) or report.get("class") != "TPV":
        return active
    mode = report.get("mode", 0)
    if not isinstance(mode, (int, float)) or mode < 2:
        return active
    return gps_report_device(report)


def access_point_count(views: object) -> int | None:
    """Extract Kismet's Wi-Fi AP view size from an all_views response."""
    if isinstance(views, dict):
        views = views.get("data", views.get("views"))
    if not isinstance(views, list):
        return None
    for view in views:
        if not isinstance(view, dict):
            continue
        view_id = view.get(
            "kismet.devices.view.id",
            view.get("kismet_devices_view_id", view.get("id")),
        )
        if view_id in {"phydot11_accesspoints", "phy80211_accesspoints"}:
            size = view.get(
                "kismet.devices.view.size",
                view.get("kismet_devices_view_size", view.get("size")),
            )
            return int(size) if isinstance(size, (int, float)) and size >= 0 else None
    return None


def device_records(devices: object) -> list[object] | None:
    """Normalize plain and DataTables-wrapped Kismet device responses."""
    if isinstance(devices, list):
        return devices
    if isinstance(devices, dict):
        records = devices.get("data", devices.get("devices"))
        return records if isinstance(records, list) else None
    return None


def observed_access_point_count(views: object, devices: object) -> int | None:
    """Return the AP total, falling back to Kismet's AP device response."""
    candidates = [count for count in (access_point_count(views),) if count is not None]
    records = device_records(devices)
    if records is not None:
        candidates.append(len(records))
    if isinstance(devices, dict):
        for key in ("recordsTotal", "recordsFiltered", "total"):
            total = devices.get(key)
            if isinstance(total, int) and total >= 0:
                candidates.append(total)
    # Kismet refreshes the view summary and device response independently.
    # Prefer the largest valid snapshot so a stale summary cannot freeze or
    # move the displayed session count backwards.
    return max(candidates) if candidates else None


def ap_activity_level(rate_per_minute: float, seconds_since_pickup: float | None) -> tuple[str, str]:
    """Return a glanceable activity label and palette key for new-AP discovery rate."""
    if seconds_since_pickup is not None and seconds_since_pickup >= 30:
        return "DEAD ZONE", "danger"
    if rate_per_minute >= 30:
        return "VERY ACTIVE", "success"
    if rate_per_minute >= 10:
        return "ACTIVE", "cyan"
    if rate_per_minute > 0:
        return "LIGHT ACTIVITY", "amber"
    return "WAITING FOR PICKUPS", "muted"


def network_details(devices: object) -> list[tuple[str, int | None]]:
    """Extract display names and last RSSI values from Kismet AP devices."""
    if not isinstance(devices, list):
        return []
    networks: list[tuple[str, int | None]] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        name = device.get("kismet.device.base.name") or device.get("kismet.device.base.commonname")
        if not isinstance(name, str) or not name.strip():
            name = "<hidden>"
        signal = device.get("kismet.device.base.signal")
        if isinstance(signal, dict):
            signal = signal.get("kismet.common.signal.last_signal")
        rssi = int(signal) if isinstance(signal, (int, float)) else None
        networks.append((name.strip(), rssi))
    return sorted(networks, key=lambda item: item[1] if item[1] is not None else -999, reverse=True)


def adapter_pickup_stats(sources: object, devices: object) -> list[tuple[str, int, int]]:
    """Return ``(adapter, unique APs, packets)`` from Kismet source attribution."""
    if not isinstance(sources, list) or not isinstance(devices, list):
        return []
    source_names: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        uuid = source.get("kismet.datasource.uuid")
        name = (source.get("kismet.datasource.capture_interface") or
                source.get("kismet.datasource.interface") or
                source.get("kismet.datasource.name"))
        if isinstance(uuid, str) and isinstance(name, str) and name.strip():
            source_names[uuid] = name.strip()
    totals = {uuid: [0, 0] for uuid in source_names}
    # Prefer Kismet's live datasource counter; per-device counters can lag.
    source_packet_totals: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            continue
        uuid = source.get("kismet.datasource.uuid")
        packets = source.get("kismet.datasource.num_packets")
        if uuid in totals and isinstance(packets, (int, float)):
            totals[uuid][1] = max(0, int(packets))
            source_packet_totals.add(uuid)
    for device in devices:
        if not isinstance(device, dict):
            continue
        seen_by = device.get("kismet.device.base.seenby")
        if isinstance(seen_by, dict):
            observations = seen_by.items()
        elif isinstance(seen_by, list):
            observations = ((observation.get("kismet.common.seenby.uuid"), observation)
                            for observation in seen_by if isinstance(observation, dict))
        else:
            continue
        for map_key, observation in observations:
            if not isinstance(observation, dict):
                continue
            uuid = observation.get("kismet.common.seenby.uuid", map_key)
            if uuid not in totals:
                continue
            totals[uuid][0] += 1
            packets = observation.get("kismet.common.seenby.num_packets", 0)
            if uuid not in source_packet_totals and isinstance(packets, (int, float)):
                totals[uuid][1] += max(0, int(packets))
    return sorted(
        ((source_names[uuid], values[0], values[1]) for uuid, values in totals.items()),
        key=lambda item: (-item[1], item[0]),
    )


def iw_channel_map(output: str) -> dict[str, str]:
    """Extract each interface's currently tuned channel from ``iw dev`` output."""
    channels: dict[str, str] = {}
    interface = ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.startswith("Interface "):
            interface = line.removeprefix("Interface ").strip()
        elif interface and line.startswith("channel "):
            channel = line.removeprefix("channel ").split(maxsplit=1)[0]
            if channel:
                channels[interface] = channel
    return channels


def kernel_channel_status() -> dict[str, str]:
    """Read live radio channels when Kismet leaves its channel field empty."""
    try:
        result = subprocess.run(
            ["iw", "dev"], capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return iw_channel_map(result.stdout) if result.returncode == 0 else {}


def source_channel_status(
    sources: object, fallback_channels: dict[str, str] | None = None,
) -> list[tuple[str, str, int, bool]]:
    """Return adapter, current channel, hop-list size, and hopping state."""
    if not isinstance(sources, list):
        return []
    status = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = (source.get("kismet.datasource.capture_interface") or
                source.get("kismet.datasource.interface") or
                source.get("kismet.datasource.name"))
        if not isinstance(name, str) or not name.strip():
            continue
        channel = source.get("kismet.datasource.channel")
        channel_text = str(channel).strip() if channel is not None else ""
        if not channel_text and fallback_channels:
            channel_text = fallback_channels.get(name.strip(), "")
        hop_channels = source.get("kismet.datasource.hop_channels")
        hopping = bool(source.get("kismet.datasource.hopping"))
        status.append((
            name.strip(), channel_text,
            len(hop_channels) if isinstance(hop_channels, list) else 0, hopping,
        ))
    return sorted(status)


def source_runtime_status(sources: object) -> dict[str, tuple[bool, str, bool]]:
    """Return running state, error text, and automatic-retry state by adapter."""
    result = {}
    if not isinstance(sources, list):
        return result
    for source in sources:
        if not isinstance(source, dict):
            continue
        name = (source.get("kismet.datasource.capture_interface") or
                source.get("kismet.datasource.interface") or
                source.get("kismet.datasource.name"))
        if not isinstance(name, str) or not name.strip():
            continue
        reason = source.get("kismet.datasource.error_reason") if source.get("kismet.datasource.error") else ""
        result[name.strip()] = (
            bool(source.get("kismet.datasource.running")), str(reason or ""),
            bool(source.get("kismet.datasource.retry")),
        )
    return result


def device_map_points(devices: object) -> list[dict[str, object]]:
    """Extract browser-safe marker data from GPS-tagged Kismet devices."""
    if not isinstance(devices, list):
        return []
    points = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        location = device.get("kismet.device.base.location")
        if not isinstance(location, dict):
            continue
        last = location.get("kismet.common.location.last")
        if not isinstance(last, dict):
            continue
        geopoint = last.get("kismet.common.location.geopoint")
        if not (isinstance(geopoint, list) and len(geopoint) == 2 and
                all(isinstance(value, (int, float)) for value in geopoint)):
            continue
        lon, lat = geopoint
        if not (-180 <= lon <= 180 and -90 <= lat <= 90) or (lon == 0 and lat == 0):
            continue
        signal = device.get("kismet.device.base.signal")
        if isinstance(signal, dict):
            signal = signal.get("kismet.common.signal.last_signal")
        mac = device.get("kismet.device.base.macaddr", "")
        key = device.get("kismet.device.base.key") or mac or f"{lat},{lon}"
        name = device.get("kismet.device.base.name") or device.get("kismet.device.base.commonname") or "<hidden>"
        points.append({"key": str(key), "name": str(name), "mac": str(mac), "lat": lat, "lon": lon,
                       "signal": int(signal) if isinstance(signal, (int, float)) else None})
    return points


def format_file_size(size: int | None) -> str:
    """Format a capture file size for the status display."""
    if size is None:
        return "Waiting for Wigle CSV…"
    value = float(size)
    units = ("B", "KiB", "MiB", "GiB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"


def responsive_window_size(screen_width: int, screen_height: int) -> tuple[int, int]:
    """Choose a useful window size without extending beyond the display."""
    width = min(screen_width, min(900, max(600, screen_width - 80)))
    height = min(screen_height, min(980, max(420, screen_height - 100)))
    return width, height


def adapter_stats_visible_rows(adapter_count: int) -> int:
    """Size the pickup table without letting a large adapter set take over the page."""
    return max(3, min(8, adapter_count))


class WardriveApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        window_width, window_height = responsive_window_size(screen_width, screen_height)
        self.geometry(f"{window_width}x{window_height}")
        self.minsize(min(680, window_width), min(600, window_height))
        self.configure(background=COLORS["void"])

        self.process: subprocess.Popen[str] | None = None
        self.output_queue: queue.Queue[str | None] = queue.Queue()
        self.gps_queue: queue.Queue[dict[str, object]] = queue.Queue()
        self.gps_reconnect_event = threading.Event()
        self.ap_queue: queue.Queue[int | None] = queue.Queue()
        self.networks_queue: queue.Queue[list[tuple[str, int | None]]] = queue.Queue()
        self.adapter_stats_queue: queue.Queue[list[tuple[str, int, int]]] = queue.Queue()
        self.channel_status_queue: queue.Queue[list[tuple[str, str, int, bool]]] = queue.Queue()
        self.source_runtime_queue: queue.Queue[dict[str, tuple[bool, str, bool]]] = queue.Queue()
        self.network_queue: queue.Queue[str] = queue.Queue()
        self.wdgwars_queue: queue.Queue[dict[str, object]] = queue.Queue()
        self.history_queue: queue.Queue[list[SessionStats] | BaseException] = queue.Queue()
        self.shutdown_event = threading.Event()
        self.source_rows: dict[str, dict[str, object]] = {}
        self.adapters_expanded = True
        self.adapter_summary = tk.StringVar(value="WI-FI ADAPTERS")
        self.log_directory = tk.StringVar(value=str(Path.home() / "Kismet-Wardrives"))
        self.status = tk.StringVar(value="Stopped")
        self.gps_status = tk.StringVar(value="GPSD: connecting…")
        self.gps_latitude = tk.StringVar(value="—")
        self.gps_longitude = tk.StringVar(value="—")
        self.gps_altitude = tk.StringVar(value="—")
        self.gps_speed = tk.StringVar(value="—")
        self.gps_satellites = tk.StringVar(value="—")
        self.gps_source = tk.StringVar(value="Automatic (any GPSD receiver)")
        self.gps_banner_text = tk.StringVar(value="●  ACQUIRING GPS     AUTOMATIC RECEIVER     NO FIX")
        self._gps_device_filter = ""
        self._gps_device_labels: dict[str, str] = {}
        self._gps_last_fix_monotonic: float | None = None
        self._gps_banner_state = "acquiring"
        self._gps_ever_locked = False
        self.ap_count = tk.StringVar(value="0")
        self.ap_status = tk.StringVar(value="Waiting for Kismet")
        self.ap_activity = tk.StringVar(value="WAITING FOR PICKUPS  •  0.0 new APs/min")
        self.ap_activity_samples: list[tuple[float, int]] = []
        self._last_ap_pickup: float | None = None
        self.capture_size = tk.StringVar(value="No active capture")
        self.wdgwars_api_key = tk.StringVar(value=load_wdgwars_api_key())
        self.wdgwars_status = tk.StringVar(value="Not connected")
        self.adsb_status = tk.StringVar(value="ADS-B: checking readsb…")
        self.latest_wigle_csv: Path | None = None
        self.session_stats: dict[str, SessionStats] = {}
        self.upload_history = load_upload_history()
        self.history_status = tk.StringVar(value="Scanning capture directory…")
        self.history_scan_running = False
        self._key_save_after_id: str | None = None
        self.api_username = "wardrive-launcher"
        self.api_password = secrets.token_urlsafe(24)
        self.api_config_path: Path | None = None
        self.map_server: http.server.ThreadingHTTPServer | None = None
        self.session_interfaces: list[str] = []
        self.session_log_directory: Path | None = None
        self.session_log_title: str | None = None
        self.network_restore_running = False
        self.close_pending = False
        self.gps_track: list[dict[str, float]] = []
        self.last_gps_position: tuple[float, float] | None = None
        self.aircraft_window: tk.Toplevel | None = None
        self.replay_points: list[dict[str, object]] = []
        self._adapter_packet_samples: dict[str, tuple[int, float]] = {}
        self._adapter_last_packet_change: dict[str, float] = {}
        self._gps_was_fixed = False
        self._adapter_stall_notified: set[str] = set()
        self._channel_warning_notified: set[str] = set()
        self._channel_status: dict[str, tuple[str, int, bool]] = {}
        self._source_runtime: dict[str, tuple[bool, str, bool]] = {}
        self._channel_observations: dict[str, dict[str, float]] = {}
        self._output_line_count = 0

        self._configure_theme()
        self._build_ui()
        self.gps_source.trace_add("write", self._select_gps_source)
        self.wdgwars_api_key.trace_add("write", self._schedule_wdgwars_key_save)
        self.refresh_interfaces()
        self.after(100, self._drain_output)
        self.after(200, self._drain_gps)
        self.after(1000, self._refresh_gps_banner)
        self.after(250, self._drain_ap_count)
        self.after(275, self._drain_networks)
        self.after(290, self._drain_adapter_stats)
        self.after(1000, self._update_capture_size)
        self.after(300, self._drain_network_status)
        self.after(350, self._drain_wdgwars)
        self.after(500, self._refresh_adsb_status)
        self.after(400, self._drain_history)
        self.after(50, self.refresh_capture_history)
        threading.Thread(target=self._gps_worker, daemon=True).start()
        threading.Thread(target=self._ap_worker, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _configure_theme(self) -> None:
        """Apply the dark tactical theme to ttk's native widgets."""
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["void"], foreground=COLORS["text"],
                        fieldbackground=COLORS["panel_2"], bordercolor=COLORS["line"],
                        lightcolor=COLORS["line"], darkcolor=COLORS["line"],
                        font=("DejaVu Sans", 10))
        style.configure("TFrame", background=COLORS["void"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["void"], foreground=COLORS["text"])
        style.configure("Section.TLabel", foreground=COLORS["cyan"],
                        font=("DejaVu Sans", 9, "bold"))
        style.configure("Muted.TLabel", foreground=COLORS["muted"], font=("DejaVu Sans", 9))
        style.configure("Status.TLabel", foreground=COLORS["amber"],
                        font=("DejaVu Sans Mono", 9, "bold"))
        style.configure("TLabelframe", background=COLORS["panel"], bordercolor=COLORS["line"],
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=COLORS["void"], foreground=COLORS["cyan"],
                        font=("DejaVu Sans", 9, "bold"))
        style.configure("TEntry", fieldbackground=COLORS["panel_2"], foreground=COLORS["text"],
                        insertcolor=COLORS["cyan"], padding=7)
        style.configure("TCombobox", fieldbackground=COLORS["panel_2"], foreground=COLORS["text"],
                        arrowcolor=COLORS["cyan"], padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["panel_2"])],
                  foreground=[("readonly", COLORS["text"])])
        style.configure("TCheckbutton", background=COLORS["void"], foreground=COLORS["text"])
        style.map("TCheckbutton", background=[("active", COLORS["void"])],
                  indicatorcolor=[("selected", COLORS["cyan"]), ("!selected", COLORS["panel_2"])])
        style.configure(
            "Adapter.TCheckbutton", background=COLORS["void"], foreground=COLORS["text"],
            indicatorsize=24, indicatormargin=(3, 3, 10, 3), padding=(2, 4),
        )
        style.map(
            "Adapter.TCheckbutton", background=[("active", COLORS["void"])],
            indicatorcolor=[("selected", COLORS["cyan"]), ("!selected", COLORS["panel_2"])],
        )
        style.configure("TButton", background=COLORS["panel_2"], foreground=COLORS["cyan"],
                        bordercolor=COLORS["line"], padding=(12, 7), font=("DejaVu Sans", 9, "bold"))
        style.map("TButton", background=[("active", "#10303e"), ("pressed", "#09232e")],
                  foreground=[("disabled", COLORS["muted"]), ("active", "#ffffff")])
        style.configure("Primary.TButton", background=COLORS["cyan"], foreground=COLORS["void"],
                        bordercolor=COLORS["cyan"], padding=(16, 8))
        style.map("Primary.TButton", background=[("active", "#68eaff"), ("disabled", COLORS["line"])],
                  foreground=[("disabled", COLORS["muted"])])
        style.configure("Danger.TButton", foreground=COLORS["danger"])
        style.configure("Treeview", background=COLORS["panel"], fieldbackground=COLORS["panel"],
                        foreground=COLORS["text"], bordercolor=COLORS["line"], rowheight=27,
                        font=("DejaVu Sans Mono", 9))
        style.configure("Treeview.Heading", background=COLORS["panel_2"], foreground=COLORS["cyan"],
                        bordercolor=COLORS["line"], font=("DejaVu Sans", 9, "bold"))
        style.map("Treeview", background=[("selected", "#12536a")], foreground=[("selected", "#ffffff")])
        style.configure("Vertical.TScrollbar", background=COLORS["panel_2"],
                        troughcolor=COLORS["void"], arrowcolor=COLORS["cyan"])

    def _build_ui(self) -> None:
        shell = ttk.Frame(self)
        shell.pack(fill="both", expand=True)

        # Primary capture actions live outside the scrolling dashboard so they
        # remain reachable even on 720p screens or with large desktop fonts.
        action_bar = ttk.Frame(shell, padding=(20, 10))
        action_bar.pack(side="bottom", fill="x")

        # Keep identity and node context visible while the dashboard scrolls.
        header_shell = ttk.Frame(shell, padding=(20, 12, 20, 0))
        header_shell.pack(side="top", fill="x")
        header_path = ASSET_DIR / "ciacore-header.png"
        if header_path.exists():
            self.header_source_image = tk.PhotoImage(file=header_path)
            self.header_image = self.header_source_image.subsample(2, 2)
            ttk.Label(header_shell, image=self.header_image).pack()
        else:
            ttk.Label(header_shell, text="CIACORE CYBERSECURITY", font=("Sans", 19, "bold")).pack(
                anchor="w", pady=(0, 16))

        masthead = tk.Frame(
            header_shell, bg=COLORS["panel"], highlightbackground=COLORS["line"], highlightthickness=1,
        )
        masthead.pack(fill="x", pady=(0, 8))
        tk.Label(
            masthead, text="  W A R D R I V E  //  F I E L D  C O N S O L E  ",
            bg=COLORS["panel"], fg=COLORS["cyan"], font=("DejaVu Sans Mono", 9, "bold"),
        ).pack(side="left", pady=6)
        tk.Label(
            masthead, text="LOCAL NODE  •  127.0.0.1  ", bg=COLORS["panel"],
            fg=COLORS["muted"], font=("DejaVu Sans Mono", 8),
        ).pack(side="right")

        # Keep the primary wardrive result pinned above the scrolling dashboard.
        # The horizontal layout uses far less vertical space than the old card.
        ap_frame = tk.Frame(
            header_shell, bg=COLORS["panel"], highlightbackground=COLORS["cyan_dim"],
            highlightthickness=1,
        )
        ap_frame.pack(fill="x", pady=(0, 8))
        tk.Label(
            ap_frame, text="// ACCESS POINTS", bg=COLORS["panel"], fg=COLORS["cyan"],
            font=("DejaVu Sans Mono", 9, "bold"),
        ).pack(side="left", padx=(10, 8), pady=6)
        self.ap_count_label = tk.Label(
            ap_frame, textvariable=self.ap_count, bg=COLORS["panel"], fg=COLORS["amber"],
            font=("DejaVu Sans Mono", 24, "bold"),
        )
        self.ap_count_label.pack(side="left", padx=(0, 12), pady=2)
        ap_text = tk.Frame(ap_frame, bg=COLORS["panel"])
        ap_text.pack(side="left", fill="x", expand=True, pady=3)
        tk.Label(
            ap_text, textvariable=self.ap_status, bg=COLORS["panel"], fg=COLORS["text"],
            anchor="w", font=("DejaVu Sans Mono", 8),
        ).pack(fill="x")
        self.ap_activity_label = tk.Label(
            ap_text, textvariable=self.ap_activity, bg=COLORS["panel"], fg=COLORS["muted"],
            anchor="w", font=("DejaVu Sans Mono", 8, "bold"),
        )
        self.ap_activity_label.pack(fill="x")
        self.ap_activity_canvas = tk.Canvas(
            ap_frame, width=180, height=30, bg=COLORS["void"],
            highlightthickness=0, borderwidth=0,
        )
        self.ap_activity_canvas.pack(side="right", fill="x", padx=8, pady=6)
        self.ap_activity_canvas.bind("<Configure>", lambda _event: self._draw_ap_activity())

        adapter_frame = ttk.LabelFrame(header_shell, text=" ADAPTER PICKUP STATS ", padding=6)
        adapter_frame.pack(fill="x", pady=(0, 8))
        adapter_frame.columnconfigure(0, weight=1)
        self.adapter_stats = ttk.Treeview(
            adapter_frame, columns=("adapter", "channel", "hop", "aps", "packets", "rate", "health"),
            show="headings", height=3,
        )
        for column, label in (("adapter", "Adapter"), ("channel", "Now"), ("hop", "Hop plan"),
                              ("aps", "Unique APs"), ("packets", "Packets"),
                              ("rate", "Packets/s"), ("health", "Health")):
            self.adapter_stats.heading(column, text=label)
        self.adapter_stats.column("adapter", anchor="w", minwidth=180)
        self.adapter_stats.column("channel", width=70, stretch=False, anchor="center")
        self.adapter_stats.column("hop", width=90, stretch=False, anchor="center")
        self.adapter_stats.column("aps", width=100, stretch=False, anchor="center")
        self.adapter_stats.column("packets", width=120, stretch=False, anchor="e")
        self.adapter_stats.column("rate", width=90, stretch=False, anchor="e")
        self.adapter_stats.column("health", width=85, stretch=False, anchor="center")
        adapter_stats_scroll = ttk.Scrollbar(
            adapter_frame, orient="vertical", command=self.adapter_stats.yview,
        )
        self.adapter_stats.configure(yscrollcommand=adapter_stats_scroll.set)
        self.adapter_stats.grid(row=0, column=0, sticky="nsew")
        adapter_stats_scroll.grid(row=0, column=1, sticky="ns")
        self.channel_warning = tk.StringVar(value="Channel telemetry waiting for Kismet")
        self.channel_warning_label = tk.Label(
            adapter_frame, textvariable=self.channel_warning, bg=COLORS["void"], fg=COLORS["muted"],
            anchor="w", font=("DejaVu Sans Mono", 9, "bold"),
        )
        self.channel_warning_label.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        self.gps_banner = tk.Label(
            header_shell, textvariable=self.gps_banner_text, bg="#8a5a00", fg="#ffffff",
            font=("DejaVu Sans Mono", 15, "bold"), pady=10, anchor="center",
            highlightthickness=1, highlightbackground=COLORS["amber"],
        )
        self.gps_banner.pack(fill="x", pady=(0, 8))

        adsb_bar = tk.Frame(
            header_shell, bg=COLORS["panel"], highlightbackground=COLORS["cyan_dim"], highlightthickness=1,
        )
        adsb_bar.pack(fill="x", pady=(0, 8))
        tk.Label(
            adsb_bar, text="✈  AIRCRAFT / ADS-B", bg=COLORS["panel"], fg=COLORS["cyan"],
            font=("DejaVu Sans Mono", 10, "bold"),
        ).pack(side="left", padx=(10, 14), pady=8)
        tk.Label(
            adsb_bar, textvariable=self.adsb_status, bg=COLORS["panel"], fg=COLORS["text"],
            font=("DejaVu Sans Mono", 9),
        ).pack(side="left", fill="x", expand=True, pady=8)
        self.adsb_upload_button = ttk.Button(
            adsb_bar, text="UPLOAD ADS-B", command=self.upload_adsb_wdgwars,
        )
        self.adsb_upload_button.pack(side="right", padx=8, pady=5)
        ttk.Button(adsb_bar, text="VIEW AIRCRAFT", command=self.show_aircraft).pack(
            side="right", padx=(8, 0), pady=5
        )

        viewport = ttk.Frame(shell)
        viewport.pack(side="top", fill="both", expand=True)
        dashboard = tk.Canvas(viewport, bg=COLORS["void"], highlightthickness=0, borderwidth=0)
        page_scroll = ttk.Scrollbar(viewport, orient="vertical", command=dashboard.yview)
        dashboard.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side="right", fill="y")
        dashboard.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(dashboard, padding=(20, 12, 20, 18))
        page_window = dashboard.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: dashboard.configure(scrollregion=dashboard.bbox("all")))
        dashboard.bind("<Configure>", lambda event: dashboard.itemconfigure(page_window, width=event.width))
        dashboard.bind_all("<MouseWheel>", lambda event: dashboard.yview_scroll(int(-event.delta / 120), "units"))
        dashboard.bind_all("<Button-4>", lambda _event: dashboard.yview_scroll(-1, "units"))
        dashboard.bind_all("<Button-5>", lambda _event: dashboard.yview_scroll(1, "units"))
        outer.columnconfigure(1, weight=1)

        adapter_header = ttk.Frame(outer)
        adapter_header.grid(row=1, column=0, sticky="nw", padx=(0, 10))
        self.adapter_toggle_button = ttk.Button(
            adapter_header, text="▾", width=2, command=self.toggle_adapter_section,
        )
        self.adapter_toggle_button.pack(side="left", padx=(0, 5))
        ttk.Label(adapter_header, textvariable=self.adapter_summary, style="Section.TLabel").pack(side="left")
        source_area = ttk.Frame(outer)
        source_area.grid(row=1, column=1, sticky="ew")
        source_area.columnconfigure(3, weight=1)
        self.source_area = source_area
        self.refresh_button = ttk.Button(outer, text="Refresh", command=self.refresh_interfaces)
        self.refresh_button.grid(row=1, column=2, sticky="n", padx=(8, 0))

        ttk.Label(outer, text="CAPTURE PATH", style="Section.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=10)
        ttk.Entry(outer, textvariable=self.log_directory).grid(row=2, column=1, sticky="ew", pady=10)
        ttk.Button(outer, text="Browse", command=self.choose_log_directory).grid(row=2, column=2, padx=(8, 0), pady=10)

        controls = ttk.Frame(outer)
        gps = ttk.LabelFrame(outer, text=" GPS TELEMETRY ", padding=10)
        gps.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(2, 10))
        for column in range(5):
            gps.columnconfigure(column, weight=1)
        self.gps_indicator = tk.Canvas(gps, width=18, height=18, bg=COLORS["panel"], highlightthickness=0)
        self.gps_indicator.grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.gps_light = self.gps_indicator.create_oval(3, 3, 15, 15, fill="#c62828", outline="#7f1616")
        ttk.Label(gps, textvariable=self.gps_status, font=("Sans", 10, "bold")).grid(
            row=0, column=1, columnspan=4, sticky="w", pady=(0, 6)
        )
        gps_items = (
            ("Latitude", self.gps_latitude),
            ("Longitude", self.gps_longitude),
            ("Altitude", self.gps_altitude),
            ("Speed", self.gps_speed),
            ("Satellites", self.gps_satellites),
        )
        for column, (label, variable) in enumerate(gps_items):
            ttk.Label(gps, text=label.upper(), foreground=COLORS["cyan_dim"], background=COLORS["panel"]).grid(row=1, column=column)
            ttk.Label(gps, textvariable=variable).grid(row=2, column=column)
        ttk.Label(gps, text="GPS SOURCE", foreground=COLORS["cyan_dim"], background=COLORS["panel"]).grid(
            row=3, column=0, sticky="w", pady=(10, 0))
        self.gps_source_combo = ttk.Combobox(
            gps, textvariable=self.gps_source, values=("Automatic (any GPSD receiver)",),
            state="readonly", width=48,
        )
        self.gps_source_combo.grid(row=3, column=1, columnspan=3, sticky="ew", pady=(10, 0), padx=(8, 8))
        ttk.Button(gps, text="Refresh GPS", command=self.refresh_gps_sources).grid(
            row=3, column=4, sticky="e", pady=(10, 0))

        networks_frame = ttk.LabelFrame(outer, text=" NETWORK INTELLIGENCE ", padding=8)
        networks_frame.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(0, 10))
        networks_frame.columnconfigure(0, weight=1)
        self.networks = ttk.Treeview(networks_frame, columns=("name", "rssi"), show="headings", height=6)
        self.networks.heading("name", text="Name (SSID)")
        self.networks.heading("rssi", text="RSSI")
        self.networks.column("name", anchor="w", minwidth=180)
        self.networks.column("rssi", width=90, stretch=False, anchor="center")
        network_scroll = ttk.Scrollbar(networks_frame, orient="vertical", command=self.networks.yview)
        self.networks.configure(yscrollcommand=network_scroll.set)
        self.networks.grid(row=0, column=0, sticky="nsew")
        network_scroll.grid(row=0, column=1, sticky="ns")

        wdgwars = ttk.LabelFrame(outer, text=" WDGWARS UPLINK ", padding=8)
        wdgwars.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        wdgwars.columnconfigure(1, weight=1)
        ttk.Label(wdgwars, text="API KEY", foreground=COLORS["cyan_dim"],
                  background=COLORS["panel"]).grid(row=0, column=0, padx=(0, 8))
        self.wdgwars_key_entry = ttk.Entry(wdgwars, textvariable=self.wdgwars_api_key, show="•")
        self.wdgwars_key_entry.grid(row=0, column=1, sticky="ew")
        self.wdgwars_verify_button = ttk.Button(wdgwars, text="Verify", command=self.verify_wdgwars)
        self.wdgwars_verify_button.grid(row=0, column=2, padx=(8, 0))
        self.wdgwars_upload_button = ttk.Button(
            wdgwars, text="Upload latest CSV", command=self.upload_latest_wdgwars
        )
        self.wdgwars_upload_button.grid(row=0, column=3, padx=(8, 0))
        ttk.Label(wdgwars, textvariable=self.wdgwars_status, style="Status.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 0)
        )
        history = ttk.LabelFrame(outer, text=" CAPTURE HISTORY + SESSION STATS ", padding=8)
        history.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(0, 10))
        history.columnconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(
            history, columns=("date", "file", "aps", "duration", "size", "upload"),
            show="headings", height=5, selectmode="browse",
        )
        for column, label in (
            ("date", "Date"), ("file", "Capture"), ("aps", "APs"),
            ("duration", "Duration"), ("size", "Size"), ("upload", "WDGWars"),
        ):
            self.history_tree.heading(column, text=label)
        self.history_tree.column("date", width=125, stretch=False)
        self.history_tree.column("file", minwidth=180, anchor="w")
        self.history_tree.column("aps", width=70, stretch=False, anchor="e")
        self.history_tree.column("duration", width=75, stretch=False, anchor="center")
        self.history_tree.column("size", width=75, stretch=False, anchor="e")
        self.history_tree.column("upload", width=90, stretch=False, anchor="center")
        history_scroll = ttk.Scrollbar(history, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=history_scroll.set)
        self.history_tree.grid(row=0, column=0, columnspan=6, sticky="nsew")
        history_scroll.grid(row=0, column=6, sticky="ns")
        self.history_tree.bind("<<TreeviewSelect>>", self._show_session_stats)
        ttk.Label(history, textvariable=self.history_status, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(7, 0)
        )
        ttk.Button(history, text="Refresh", command=self.refresh_capture_history).grid(
            row=1, column=1, padx=(8, 0), pady=(7, 0)
        )
        ttk.Button(history, text="Upload selected", command=self.upload_selected_wdgwars).grid(
            row=1, column=2, padx=(8, 0), pady=(7, 0)
        )
        ttk.Button(history, text="Analyze", command=self.show_analytics).grid(row=1, column=3, padx=(8, 0), pady=(7, 0))
        ttk.Button(history, text="Compare", command=self.compare_selected).grid(row=1, column=4, padx=(8, 0), pady=(7, 0))
        ttk.Button(history, text="Export…", command=self.export_selected).grid(row=1, column=5, padx=(8, 0), pady=(7, 0))
        ttk.Button(history, text="Replay", command=self.replay_selected).grid(row=2, column=5, padx=(8, 0), pady=(7, 0))

        controls = action_bar
        self.start_button = ttk.Button(controls, text="▶  START SCAN", style="Primary.TButton", command=self.start_wardrive)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(controls, text="■  STOP", style="Danger.TButton", command=self.stop_wardrive, state="disabled")
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(controls, text="⌖  LIVE MAP", command=self.open_live_map).pack(side="left")
        ttk.Button(controls, text="✓ PREFLIGHT", command=self.show_preflight).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="PROFILES", command=self.show_profiles).pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="RECOVER", command=self.recover_sessions).pack(side="left")
        ttk.Label(controls, textvariable=self.status, style="Status.TLabel").pack(side="right")
        ttk.Label(controls, text="Wigle CSV:").pack(side="left", padx=(18, 4))
        ttk.Label(controls, textvariable=self.capture_size).pack(side="left")

        log_frame = ttk.LabelFrame(outer, text=" KISMET EVENT STREAM ", padding=8)
        log_frame.grid(row=9, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.output = tk.Text(log_frame, wrap="word", state="disabled", height=9,
                              bg=COLORS["void"], fg=COLORS["text"], insertbackground=COLORS["cyan"],
                              selectbackground="#12536a", relief="flat", borderwidth=0,
                              font=("DejaVu Sans Mono", 9), padx=10, pady=8)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.output.yview)
        self.output.configure(yscrollcommand=scrollbar.set)
        self.output.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        ttk.Label(
            outer,
            text="Use only where you have permission. GPS must be configured in Kismet for location-tagged logs.",
            style="Muted.TLabel",
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _ap_worker(self) -> None:
        views_url = "http://127.0.0.1:2501/devices/views/all_views.json"
        networks_url = "http://127.0.0.1:2501/devices/views/phydot11_accesspoints/devices.json"
        sources_url = "http://127.0.0.1:2501/datasource/all_sources.json"
        while not self.shutdown_event.is_set():
            count = None
            views: object = None
            devices: object = None
            networks: list[tuple[str, int | None]] = []
            adapter_stats: list[tuple[str, int, int]] = []
            channel_status: list[tuple[str, str, int, bool]] = []
            credentials = base64.b64encode(
                f"{self.api_username}:{self.api_password}".encode("utf-8")
            ).decode("ascii")
            headers = {"Authorization": f"Basic {credentials}"}
            def fetch(url: str) -> object:
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=2) as response:
                    return json.load(response)

            source_data: object = None
            # Run independent API reads together; sequential timeouts previously
            # made a single slow endpoint hold the UI back by up to six seconds.
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {url: executor.submit(fetch, url) for url in (views_url, networks_url, sources_url)}
                for url, future in futures.items():
                    try:
                        value = future.result()
                    except (OSError, ValueError):
                        continue
                    if url == views_url:
                        views = value
                    elif url == networks_url:
                        devices = value
                    else:
                        source_data = value
            count = observed_access_point_count(views, devices)
            # Keep the last good UI snapshot when one Kismet poll fails.
            if count is not None:
                self.ap_queue.put(count)
            if devices is not None:
                self.networks_queue.put(network_details(device_records(devices)))
            if source_data is not None:
                if devices is not None:
                    self.adapter_stats_queue.put(adapter_pickup_stats(source_data, device_records(devices)))
                channel_status = source_channel_status(source_data)
                if any(not channel for _adapter, channel, _count, _hopping in channel_status):
                    channel_status = source_channel_status(source_data, kernel_channel_status())
                self.channel_status_queue.put(channel_status)
                self.source_runtime_queue.put(source_runtime_status(source_data))
            self.shutdown_event.wait(API_POLL_SECONDS)

    def _drain_ap_count(self) -> None:
        try:
            while True:
                count = self.ap_queue.get_nowait()
                if count is None:
                    self.ap_status.set("Waiting for Kismet API")
                else:
                    self.ap_count.set(f"{count:,}")
                    self.ap_status.set("Wi-Fi APs observed this session")
                    self._record_ap_activity(count)
        except queue.Empty:
            pass
        if not self.shutdown_event.is_set():
            self.after(250, self._drain_ap_count)

    def _record_ap_activity(self, count: int) -> None:
        now = time.monotonic()
        previous_count = self.ap_activity_samples[-1][1] if self.ap_activity_samples else 0
        if count > previous_count:
            self._last_ap_pickup = now
        if self.ap_activity_samples and count < previous_count:
            self.ap_activity_samples.clear()
            self._last_ap_pickup = now if count else None
        self.ap_activity_samples.append((now, count))
        self.ap_activity_samples = [
            sample for sample in self.ap_activity_samples
            if now - sample[0] <= AP_ACTIVITY_WINDOW_SECONDS
        ]
        if len(self.ap_activity_samples) >= 2:
            elapsed = max(1.0, now - self.ap_activity_samples[0][0])
            gained = max(0, count - self.ap_activity_samples[0][1])
            rate = gained * 60 / elapsed
        else:
            rate = 0.0
        since = now - self._last_ap_pickup if self._last_ap_pickup is not None else None
        level, color_key = ap_activity_level(rate, since if self.process and self.process.poll() is None else None)
        self.ap_activity.set(
            f"{level}  •  {rate:.1f} new APs/min  •  rolling {AP_ACTIVITY_WINDOW_SECONDS}s"
        )
        self.ap_activity_label.configure(fg=COLORS[color_key])
        # A quiet scan does not invalidate the total; dim only the activity text.
        self.ap_count_label.configure(fg=COLORS["cyan"])
        self._draw_ap_activity()

    def _draw_ap_activity(self) -> None:
        canvas = getattr(self, "ap_activity_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        width, height = max(200, canvas.winfo_width()), max(40, canvas.winfo_height())
        canvas.create_line(0, height - 2, width, height - 2, fill=COLORS["line"])
        if len(self.ap_activity_samples) < 2:
            return
        deltas = [max(0, current[1] - previous[1])
                  for previous, current in zip(self.ap_activity_samples, self.ap_activity_samples[1:])]
        maximum = max(1, max(deltas, default=1))
        bar_width = width / max(30, len(deltas))
        offset = width - len(deltas) * bar_width
        for index, delta in enumerate(deltas):
            bar_height = (height - 6) * delta / maximum
            x1 = offset + index * bar_width
            canvas.create_rectangle(x1, height - 2 - bar_height, x1 + max(1, bar_width - 1), height - 2,
                                    fill=COLORS["cyan"] if delta else COLORS["panel_2"], outline="")

    def _drain_networks(self) -> None:
        latest = None
        try:
            while True:
                latest = self.networks_queue.get_nowait()
        except queue.Empty:
            pass
        if latest is not None:
            self.networks.delete(*self.networks.get_children())
            for name, rssi in latest:
                self.networks.insert("", "end", values=(name, f"{rssi} dBm" if rssi is not None else "—"))
        if not self.shutdown_event.is_set():
            self.after(275, self._drain_networks)

    def _drain_adapter_stats(self) -> None:
        latest = None
        latest_channels = None
        try:
            while True:
                latest = self.adapter_stats_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                latest_channels = self.channel_status_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            while True:
                self._source_runtime = self.source_runtime_queue.get_nowait()
        except queue.Empty:
            pass
        if latest_channels is not None:
            observed_at = time.monotonic()
            self._channel_status = {
                adapter: (channel, hop_count, hopping)
                for adapter, channel, hop_count, hopping in latest_channels
            }
            for adapter, (channel, _count, hopping) in self._channel_status.items():
                if hopping and channel:
                    observations = self._channel_observations.setdefault(adapter, {})
                    observations[channel] = observed_at
                    self._channel_observations[adapter] = {
                        item: seen for item, seen in observations.items()
                        if observed_at - seen <= CHANNEL_VERIFY_SECONDS
                    }
            broken = [adapter for adapter, (_channel, count, hopping) in self._channel_status.items()
                      if hopping and count < 2]
            if broken:
                detail = ", ".join(f"{adapter} ({self._channel_status[adapter][1]} channel)" for adapter in broken)
                self.channel_warning.set(f"⚠ NOT HOPPING: {detail}")
                self.channel_warning_label.configure(fg=COLORS["danger"])
                for adapter in broken:
                    if adapter not in self._channel_warning_notified:
                        self._notify("Wardrive channel warning", f"{adapter} is configured to hop but has fewer than two channels.")
                        self._channel_warning_notified.add(adapter)
            else:
                summaries = []
                for adapter, (_channel, count, hopping) in self._channel_status.items():
                    if hopping:
                        seen = len(self._channel_observations.get(adapter, {}))
                        summaries.append(f"{adapter}: {seen}/{count} seen, {'verified' if seen >= 2 else 'verifying'}")
                self.channel_warning.set("HOPPING OK  •  " + "  •  ".join(summaries) if summaries else "No hopping adapters active")
                self.channel_warning_label.configure(fg=COLORS["success"] if summaries else COLORS["muted"])
                self._channel_warning_notified.clear()
        if latest is not None:
            self.adapter_stats.delete(*self.adapter_stats.get_children())
            self.adapter_stats.configure(height=adapter_stats_visible_rows(len(latest)))
            now = time.monotonic()
            for adapter, access_points, packets in latest:
                channel, hop_count, hopping = self._channel_status.get(adapter, ("", 0, False))
                hop_text = f"{hop_count} ch" if hopping else "FIXED"
                previous = self._adapter_packet_samples.get(adapter)
                rate = max(0.0, (packets - previous[0]) / (now - previous[1])) if previous and now > previous[1] else 0.0
                if not previous or packets != previous[0]:
                    self._adapter_last_packet_change[adapter] = now
                quiet_for = now - self._adapter_last_packet_change.get(adapter, now)
                running, error, retrying = self._source_runtime.get(adapter, (True, "", False))
                if not running:
                    health = "RETRYING" if retrying else "DISCONNECTED"
                elif quiet_for >= ADAPTER_STALL_SECONDS:
                    health = "STALLED"
                elif rate <= 0:
                    health = "QUIET"
                else:
                    health = "OK"
                if health == "STALLED" and previous and adapter not in self._adapter_stall_notified:
                    self._notify("Wardrive adapter warning", f"{adapter} has received no packets for {int(quiet_for)} seconds.")
                    self._adapter_stall_notified.add(adapter)
                elif rate > 0:
                    self._adapter_stall_notified.discard(adapter)
                self._adapter_packet_samples[adapter] = (packets, now)
                self.adapter_stats.insert(
                    "", "end", values=(adapter, channel or "—", hop_text, f"{access_points:,}",
                                        f"{packets:,}", f"{rate:,.1f}", health),
                )
                if error and not running:
                    retry_detail = "automatic retry enabled" if retrying else "retry disabled"
                    self.channel_warning.set(f"⚠ {adapter}: {error} ({retry_detail})")
                    self.channel_warning_label.configure(fg=COLORS["danger"])
        if not self.shutdown_event.is_set():
            self.after(290, self._drain_adapter_stats)

    def _update_capture_size(self) -> None:
        size = None
        if self.session_log_directory and self.session_log_title:
            try:
                files = list(self.session_log_directory.glob(f"{self.session_log_title}-*.wiglecsv"))
                if files:
                    size = max(files, key=lambda path: path.stat().st_mtime_ns).stat().st_size
            except OSError:
                pass
            self.capture_size.set(format_file_size(size))
        else:
            self.capture_size.set("No active capture")
        if not self.shutdown_event.is_set():
            self.after(1000, self._update_capture_size)

    def refresh_capture_history(self) -> None:
        if self.history_scan_running:
            return
        self.history_scan_running = True
        self.history_status.set("Scanning capture directory…")
        directory = Path(self.log_directory.get()).expanduser()
        threading.Thread(target=self._history_worker, args=(directory,), daemon=True).start()

    def _history_worker(self, directory: Path) -> None:
        try:
            paths = sorted(directory.glob("*.wiglecsv"), key=lambda item: item.stat().st_mtime, reverse=True)[:200]
            sessions: list[SessionStats] = []
            for path in paths:
                if self.shutdown_event.is_set():
                    return
                try:
                    sessions.append(parse_wigle_session(path))
                except (OSError, csv.Error):
                    continue
            self.history_queue.put(sessions)
        except OSError as exc:
            self.history_queue.put(exc)

    def _drain_history(self) -> None:
        try:
            update = self.history_queue.get_nowait()
        except queue.Empty:
            if not self.shutdown_event.is_set():
                self.after(400, self._drain_history)
            return
        self.history_scan_running = False
        if isinstance(update, BaseException):
            self.history_status.set(f"History unavailable: {update}")
        else:
            self.history_tree.delete(*self.history_tree.get_children())
            self.session_stats.clear()
            for index, stats in enumerate(update):
                item_id = f"session-{index}"
                self.session_stats[item_id] = stats
                key = str(stats.path.resolve())
                uploaded = "Uploaded" if key in self.upload_history else "—"
                try:
                    size = format_file_size(stats.path.stat().st_size)
                except OSError:
                    size = "—"
                self.history_tree.insert("", "end", iid=item_id, values=(
                    datetime.fromtimestamp(stats.modified).strftime("%Y-%m-%d %H:%M"),
                    stats.path.name, f"{stats.access_points:,}", format_duration(stats.duration_seconds),
                    size, uploaded,
                ))
            self.history_status.set(f"{len(update)} capture session{'s' if len(update) != 1 else ''}")
        if not self.shutdown_event.is_set():
            self.after(400, self._drain_history)

    def _selected_session(self) -> SessionStats | None:
        selection = self.history_tree.selection()
        return self.session_stats.get(selection[0]) if selection else None

    def _show_session_stats(self, _event: object = None) -> None:
        stats = self._selected_session()
        if not stats:
            return
        security = "  ".join(f"{name} {count:,}" for name, count in stats.security) or "No security data"
        channels = ", ".join(stats.channels[:16])
        if len(stats.channels) > 16:
            channels += f" +{len(stats.channels) - 16}"
        strongest = f"{stats.strongest_rssi} dBm" if stats.strongest_rssi is not None else "—"
        self.history_status.set(
            f"{stats.access_points:,} APs  •  {stats.hidden:,} hidden  •  strongest {strongest}  •  "
            f"channels {channels or '—'}  •  {security}"
        )

    def _text_dialog(self, title: str, content: str) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry("700x520")
        text_widget = tk.Text(dialog, wrap="word", bg=COLORS["void"], fg=COLORS["text"],
                              insertbackground=COLORS["cyan"], padx=14, pady=14)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")
        text_widget.pack(fill="both", expand=True)
        ttk.Button(dialog, text="Close", command=dialog.destroy).pack(pady=8)

    def show_analytics(self) -> None:
        stats = self._selected_session()
        if not stats:
            messagebox.showerror(APP_NAME, "Select a capture session first.")
            return
        try:
            analytics = session_analytics(stats.path)
        except (OSError, csv.Error) as exc:
            messagebox.showerror(APP_NAME, f"Could not analyze the session:\n{exc}")
            return
        sections = []
        for title, values in analytics.items():
            maximum = max(values.values(), default=1)
            lines = [f"{name:24} {count:6,}  {'█' * max(1, round(count / maximum * 30))}"
                     for name, count in sorted(values.items(), key=lambda item: (-item[1], item[0])) if count]
            sections.append(title.upper() + "\n" + ("\n".join(lines) or "No data"))
        self._text_dialog(f"Analytics — {stats.path.name}", "\n\n".join(sections))

    def compare_selected(self) -> None:
        newer = self._selected_session()
        if not newer:
            messagebox.showerror(APP_NAME, "Select the newer capture session first.")
            return
        older_name = filedialog.askopenfilename(
            title="Choose the older session", initialdir=str(newer.path.parent),
            filetypes=(("WiGLE CSV", "*.wiglecsv"), ("CSV", "*.csv"), ("All files", "*")),
        )
        if not older_name:
            return
        try:
            result = compare_sessions(Path(older_name), newer.path)
        except (OSError, csv.Error) as exc:
            messagebox.showerror(APP_NAME, f"Could not compare sessions:\n{exc}")
            return
        sections = []
        for kind in ("new", "missing", "changed"):
            records = result[kind]
            lines = [f"{record.bssid}  {record.ssid or '<hidden>'}  {record.security}  ch {record.channel or '—'}"
                     for record in records[:500]]
            suffix = f"\n… and {len(records) - 500:,} more" if len(records) > 500 else ""
            sections.append(f"{kind.upper()} ({len(records):,})\n" + ("\n".join(lines) or "None") + suffix)
        self._text_dialog(f"Session comparison — {newer.path.name}", "\n\n".join(sections))

    def export_selected(self) -> None:
        stats = self._selected_session()
        if not stats:
            messagebox.showerror(APP_NAME, "Select a capture session first.")
            return
        destination = filedialog.asksaveasfilename(
            title="Export mapped observations", initialdir=str(stats.path.parent),
            initialfile=stats.path.stem + ".geojson", defaultextension=".geojson",
            filetypes=(("GeoJSON", "*.geojson"), ("KML", "*.kml"), ("GPX", "*.gpx")),
        )
        if not destination:
            return
        suffix = Path(destination).suffix.lower().lstrip(".")
        if suffix not in {"geojson", "kml", "gpx"}:
            messagebox.showerror(APP_NAME, "Choose a .geojson, .kml, or .gpx destination.")
            return
        try:
            export_geodata(stats.path, Path(destination), suffix)
            self._notify("Wardrive export complete", Path(destination).name)
        except (OSError, ValueError, csv.Error) as exc:
            messagebox.showerror(APP_NAME, f"Export failed:\n{exc}")

    def replay_selected(self) -> None:
        stats = self._selected_session()
        if not stats:
            messagebox.showerror(APP_NAME, "Select a capture session first.")
            return
        try:
            self.replay_points = [{"bssid": record.bssid, "ssid": record.ssid, "lat": record.latitude,
                                   "lon": record.longitude, "rssi": record.rssi, "time": record.timestamp}
                                  for record in read_wigle_records(stats.path) if record.latitude is not None]
            self.replay_points.sort(key=lambda item: str(item["time"]))
        except (OSError, csv.Error) as exc:
            messagebox.showerror(APP_NAME, f"Could not load replay:\n{exc}")
            return
        if not self.replay_points:
            messagebox.showinfo(APP_NAME, "This capture has no GPS-tagged observations to replay.")
            return
        self.open_live_map(replay=True)

    def show_preflight(self) -> None:
        checks = preflight_checks(self.selected_sources(), Path(self.log_directory.get()).expanduser())
        content = "\n".join(f"{'PASS' if item.ok else 'WARN'}  {item.name}: {item.detail}" for item in checks)
        self._text_dialog("Wardrive preflight", content)

    def show_profiles(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Capture profiles")
        dialog.geometry("500x300")
        ttk.Label(dialog, text="Apply a channel profile to every selected adapter.").pack(pady=(18, 10))
        saved = load_profiles()
        profile = tk.StringVar(value="All supported channels")
        choices = {
            "All supported channels": ("hop", ""), "2.4 GHz (1, 6, 11)": ("hop", "1,6,11"),
            "5 GHz common": ("hop", "36,40,44,48,149,153,157,161"),
            "6 GHz PSC": ("hop", "5,21,37,53,69,85,101,117,133,149,165,181,197,213,229"),
        }
        picker = ttk.Combobox(dialog, textvariable=profile, values=list(choices) + sorted(saved), state="readonly", width=38)
        picker.pack(pady=8)

        def apply_profile() -> None:
            if profile.get() in choices:
                mode, channels = choices[profile.get()]
                for source_name, source_row in self.source_rows.items():
                    if source_row["enabled"].get():
                        source_row["mode"].set(mode)
                        source_row["channels"].set(channels)
                        source_row["plan"].set(channel_plan_for(mode, channels))
                        self._set_channel_entry_state(source_name)
            else:
                data = saved.get(profile.get(), {})
                self.log_directory.set(str(data.get("log_directory", self.log_directory.get())))
                configured = data.get("sources", {})
                if isinstance(configured, dict):
                    for name, source_row in self.source_rows.items():
                        source = configured.get(name)
                        source_row["enabled"].set(isinstance(source, dict))
                        if isinstance(source, dict):
                            source_row["mode"].set(str(source.get("mode", "hop")))
                            source_row["channels"].set(str(source.get("channels", "")))
                            source_row["plan"].set(channel_plan_for(
                                str(source.get("mode", "hop")), str(source.get("channels", ""))
                            ))
                            self._set_channel_entry_state(name)
            dialog.destroy()
        ttk.Button(dialog, text="Apply", command=apply_profile, style="Primary.TButton").pack(pady=12)
        name = tk.StringVar()
        save_row = ttk.Frame(dialog)
        save_row.pack(fill="x", padx=28)
        ttk.Entry(save_row, textvariable=name).pack(side="left", fill="x", expand=True)
        def save_current() -> None:
            clean = name.get().strip()
            if not clean:
                messagebox.showerror(APP_NAME, "Enter a profile name.", parent=dialog)
                return
            saved[clean] = {"log_directory": self.log_directory.get(), "sources": {
                source: {"mode": str(item["mode"].get()), "channels": str(item["channels"].get())}
                for source, item in self.source_rows.items() if item["enabled"].get()
            }}
            try:
                save_profiles(saved)
                picker.configure(values=list(choices) + sorted(saved))
                profile.set(clean)
            except OSError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=dialog)
        ttk.Button(save_row, text="Save current", command=save_current).pack(side="left", padx=(8, 0))

    def recover_sessions(self) -> None:
        directory = Path(self.log_directory.get()).expanduser()
        try:
            databases = find_recoverable_databases(directory)
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        if not databases:
            messagebox.showinfo(APP_NAME, "No interrupted or unexported Kismet sessions were found.")
            return
        converter = shutil.which("kismetdb_to_wiglecsv")
        if not converter:
            messagebox.showerror(APP_NAME, "kismetdb_to_wiglecsv was not found in PATH.")
            return
        if not messagebox.askyesno(APP_NAME, f"Export {len(databases)} recoverable session(s) to WiGLE CSV?"):
            return
        completed, errors = 0, []
        for database in databases:
            try:
                result = subprocess.run([converter, "--in", str(database), "--out", str(database.with_suffix('.wiglecsv')),
                                         "--force", "--skip-clean"], capture_output=True, text=True, timeout=120)
                if result.returncode:
                    errors.append(database.name)
                else:
                    completed += 1
            except (OSError, subprocess.TimeoutExpired):
                errors.append(database.name)
        self.refresh_capture_history()
        messagebox.showinfo(APP_NAME, f"Recovered {completed} session(s)." +
                            (f" Failed: {', '.join(errors)}" if errors else ""))

    @staticmethod
    def _notify(title: str, message: str) -> None:
        notifier = shutil.which("notify-send")
        if notifier:
            try:
                subprocess.Popen([notifier, title, message], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                pass

    def _gps_worker(self) -> None:
        """Stream TPV/SKY reports from the standard local GPSD socket."""
        while not self.shutdown_event.is_set():
            self.gps_reconnect_event.clear()
            # Automatic mode locks to one receiver per connection. Otherwise
            # interleaved reports from two GPS units make the fix jump between
            # receivers and can falsely signal that a good fix was lost.
            active_device = self._gps_device_filter
            try:
                with socket.create_connection(("127.0.0.1", 2947), timeout=3) as gpsd:
                    gpsd.settimeout(4)
                    gpsd.sendall(b'?DEVICES;\n?WATCH={"enable":true,"json":true};\n')
                    self.gps_queue.put({"status": "GPSD connected · waiting for fix", "fix": False})
                    buffer = ""
                    while not self.shutdown_event.is_set():
                        if self.gps_reconnect_event.is_set():
                            raise ConnectionError("GPS source refresh requested")
                        chunk = gpsd.recv(4096)
                        if not chunk:
                            raise ConnectionError("GPSD closed the connection")
                        buffer += chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            try:
                                report = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            kind = report.get("class")
                            if kind == "DEVICES":
                                choices = gps_device_choices(report.get("devices"))
                                self.gps_queue.put({"devices": choices})
                                continue
                            report_device = gps_report_device(report)
                            active_device = gps_active_device(active_device, self._gps_device_filter, report)
                            if active_device and report_device != active_device:
                                continue
                            if kind == "TPV":
                                mode = int(report.get("mode", 0))
                                update: dict[str, object] = {
                                    "status": "GPS fix: 3D" if mode >= 3 else "GPS fix: 2D" if mode == 2 else "GPSD connected · no fix",
                                    "fix": mode >= 2,
                                }
                                if mode >= 2:
                                    update.update(
                                        lat=report.get("lat"), lon=report.get("lon"),
                                        alt=report.get("altMSL", report.get("alt")), speed=report.get("speed"),
                                    )
                                self.gps_queue.put(update)
                            elif kind == "SKY":
                                self.gps_queue.put({"satellites": report.get("uSat", 0)})
            except (OSError, ConnectionError):
                if not self.gps_reconnect_event.is_set():
                    self.gps_queue.put({"status": "GPSD unavailable · start gpsd or connect a GPS", "clear": True, "fix": False})
            if not self.gps_reconnect_event.is_set():
                self.shutdown_event.wait(3)

    def _select_gps_source(self, *_args: object) -> None:
        label = self.gps_source.get()
        self._gps_device_filter = self._gps_device_labels.get(label, "")
        selected = self._gps_device_filter or "any GPSD receiver"
        self.gps_status.set(f"Switching GPS source to {selected}…")
        self._gps_last_fix_monotonic = None
        self._gps_was_fixed = False
        self.gps_reconnect_event.set()

    def refresh_gps_sources(self) -> None:
        self.gps_status.set("Refreshing GPSD receivers…")
        self.gps_reconnect_event.set()

    def _refresh_gps_banner(self) -> None:
        now = time.monotonic()
        age = now - self._gps_last_fix_monotonic if self._gps_last_fix_monotonic is not None else None
        unavailable = "unavailable" in self.gps_status.get().lower()
        if self._gps_was_fixed and age is not None and age <= 5:
            state, headline, color, border = "locked", "GPS LOCKED", "#087a45", COLORS["success"]
            age_text = f"FIX {max(0, int(age))}s AGO"
        elif unavailable or (self._gps_ever_locked and self._gps_last_fix_monotonic is not None and
                             (not self._gps_was_fixed or (age is not None and age > 5))):
            state, headline, color, border = "lost", "GPS LOST", "#9b1727", COLORS["danger"]
            age_text = f"LAST FIX {int(age)}s AGO" if age is not None else "NO RECEIVER DATA"
        else:
            state, headline, color, border = "acquiring", "ACQUIRING GPS", "#8a5a00", COLORS["amber"]
            age_text = "WAITING FOR FIX"

        source = self.gps_source.get()
        receiver = source.replace(" — ", " ").upper() if " — " in source else "AUTOMATIC RECEIVER"
        satellites = self.gps_satellites.get()
        satellite_text = f"{satellites} SATELLITES" if satellites not in {"", "—"} else "SATELLITES —"
        self.gps_banner_text.set(f"●  {headline}     {receiver}     {satellite_text}     {age_text}")
        pulse_color = color
        if state != "locked" and int(now) % 2:
            pulse_color = "#6f111d" if state == "lost" else "#684400"
        self.gps_banner.configure(bg=pulse_color, highlightbackground=border)

        previous = self._gps_banner_state
        if state == "lost" and previous == "locked":
            self.bell()
        elif state == "locked" and previous == "lost" and self._gps_ever_locked:
            self.bell()
            self.after(180, self.bell)
        if state == "locked":
            self._gps_ever_locked = True
        self._gps_banner_state = state
        if not self.shutdown_event.is_set():
            self.after(1000, self._refresh_gps_banner)

    def _drain_gps(self) -> None:
        try:
            while True:
                update = self.gps_queue.get_nowait()
                if "devices" in update:
                    choices = update["devices"]
                    if isinstance(choices, list):
                        self._gps_device_labels = {
                            str(label): str(path) for label, path in choices
                            if isinstance(label, str) and isinstance(path, str)
                        }
                        labels = ["Automatic (any GPSD receiver)", *self._gps_device_labels]
                        self.gps_source_combo.configure(values=labels)
                        if self.gps_source.get() not in labels:
                            self.gps_source.set(labels[0])
                self.gps_status.set(str(update.get("status", self.gps_status.get())))
                if "fix" in update:
                    fixed = bool(update["fix"])
                    if self._gps_was_fixed and not fixed and self.process and self.process.poll() is None:
                        self._notify("Wardrive GPS warning", "GPS fix was lost; observations may lack coordinates.")
                        if self.gps_track:
                            self.gps_track[-1]["gap"] = 1.0
                    self._gps_was_fixed = fixed
                    if fixed:
                        self._gps_last_fix_monotonic = time.monotonic()
                    self.gps_indicator.itemconfigure(
                        self.gps_light,
                        fill="#2eae4e" if fixed else "#c62828",
                        outline="#176b2d" if fixed else "#7f1616",
                    )
                if update.get("clear"):
                    for variable in (self.gps_latitude, self.gps_longitude, self.gps_altitude, self.gps_speed, self.gps_satellites):
                        variable.set("—")
                if "lat" in update:
                    self.gps_latitude.set(gps_text(update["lat"], "°"))
                    self.gps_longitude.set(gps_text(update.get("lon"), "°"))
                    self.gps_altitude.set(gps_text(update.get("alt"), " m", 1))
                    speed = update.get("speed")
                    self.gps_speed.set(gps_text(speed * 3.6 if isinstance(speed, (int, float)) else None, " km/h", 1))
                    lat, lon = update.get("lat"), update.get("lon")
                    if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                        self.last_gps_position = (float(lat), float(lon))
                    if self.process and self.process.poll() is None and isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                        if not self.gps_track or abs(lat - self.gps_track[-1]["lat"]) + abs(lon - self.gps_track[-1]["lon"]) > 0.00001:
                            self.gps_track.append({"lat": float(lat), "lon": float(lon), "time": time.time()})
                            if len(self.gps_track) > 20000:
                                del self.gps_track[:1000]
                if "satellites" in update:
                    self.gps_satellites.set(str(update["satellites"]))
        except queue.Empty:
            pass
        if not self.shutdown_event.is_set():
            self.after(200, self._drain_gps)

    def refresh_interfaces(self) -> None:
        interfaces = network_interfaces()
        previous = {
            name: (row["enabled"].get(), row["mode"].get(), row["channels"].get())
            for name, row in self.source_rows.items()
        }
        for child in self.source_area.winfo_children():
            child.destroy()
        self.source_rows.clear()
        for column, label in enumerate(("Use", "Adapter", "Channel group", "Custom / fixed channel(s)")):
            ttk.Label(self.source_area, text=label).grid(row=0, column=column, sticky="w", padx=(0, 8))
        for index, interface in enumerate(interfaces, start=1):
            old = previous.get(interface, (index == 1, "hop", ""))
            enabled = tk.BooleanVar(value=old[0])
            enabled.trace_add("write", self._update_adapter_summary)
            mode = tk.StringVar(value=old[1])
            channels = tk.StringVar(value=old[2])
            plan = tk.StringVar(value=channel_plan_for(old[1], old[2]))
            check = ttk.Checkbutton(self.source_area, variable=enabled, style="Adapter.TCheckbutton")
            combo = ttk.Combobox(
                self.source_area, textvariable=plan,
                values=tuple(CHANNEL_PLANS) + (CUSTOM_HOP_PLAN, FIXED_CHANNEL_PLAN),
                state="readonly", width=30,
            )
            entry = ttk.Entry(self.source_area, textvariable=channels)
            check.grid(row=index, column=0, sticky="w")
            ttk.Label(self.source_area, text=interface).grid(row=index, column=1, sticky="w", padx=(0, 8))
            combo.grid(row=index, column=2, sticky="w", padx=(0, 8))
            entry.grid(row=index, column=3, sticky="ew")
            self.source_rows[interface] = {
                "enabled": enabled, "mode": mode, "channels": channels, "plan": plan,
                "widgets": (check, combo, entry),
            }
            combo.bind("<<ComboboxSelected>>", lambda _event, name=interface: self._apply_channel_plan(name))
            self._set_channel_entry_state(interface)
        self._update_adapter_summary()

    def _apply_channel_plan(self, interface: str) -> None:
        row = self.source_rows[interface]
        selected = str(row["plan"].get())
        if selected in CHANNEL_PLANS:
            mode, channels = CHANNEL_PLANS[selected]
            row["mode"].set(mode)
            row["channels"].set(channels)
        elif selected == FIXED_CHANNEL_PLAN:
            row["mode"].set("fixed")
        else:
            row["mode"].set("hop")
        self._set_channel_entry_state(interface)

    def _set_channel_entry_state(self, interface: str) -> None:
        row = self.source_rows[interface]
        entry = row["widgets"][2]
        editable = str(row["plan"].get()) in {CUSTOM_HOP_PLAN, FIXED_CHANNEL_PLAN}
        entry.configure(state="normal" if editable else "disabled")

    def _update_adapter_summary(self, *_args: object) -> None:
        selected = sum(bool(row["enabled"].get()) for row in self.source_rows.values())
        available = len(self.source_rows)
        self.adapter_summary.set(f"WI-FI ADAPTERS  ({selected} selected / {available} available)")

    def toggle_adapter_section(self) -> None:
        self.adapters_expanded = not self.adapters_expanded
        if self.adapters_expanded:
            self.source_area.grid()
            self.adapter_toggle_button.configure(text="▾")
        else:
            self.source_area.grid_remove()
            self.adapter_toggle_button.configure(text="▸")

    def selected_sources(self) -> list[CaptureSource]:
        return [
            CaptureSource(name, str(row["mode"].get()), str(row["channels"].get()))
            for name, row in self.source_rows.items() if row["enabled"].get()
        ]

    def _set_source_controls_state(self, enabled: bool) -> None:
        for name, row in self.source_rows.items():
            check, combo, entry = row["widgets"]
            check.configure(state="normal" if enabled else "disabled")
            combo.configure(state="readonly" if enabled else "disabled")
            if enabled:
                self._set_channel_entry_state(name)
            else:
                entry.configure(state="disabled")

    def choose_log_directory(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.log_directory.get() or str(Path.home()))
        if chosen:
            self.log_directory.set(chosen)
            self.refresh_capture_history()

    def open_live_map(self, replay: bool = False) -> None:
        """Open the launcher's live map of GPS-tagged Kismet access points."""
        if not replay:
            try:
                with socket.create_connection(("127.0.0.1", 2501), timeout=1):
                    pass
            except OSError:
                if not ADSB_JSON_PATH.is_file():
                    messagebox.showerror(APP_NAME, "Neither Kismet nor the readsb aircraft receiver is running.")
                    return
        if self.map_server is None:
            app = self

            class MapHandler(http.server.BaseHTTPRequestHandler):
                def do_GET(self) -> None:
                    if self.path == "/replay" or self.path.startswith("/replay?"):
                        self._send(REPLAY_MAP_HTML, "text/html; charset=utf-8")
                        return
                    if self.path == "/" or self.path.startswith("/?"):
                        self._send(LIVE_MAP_HTML, "text/html; charset=utf-8")
                        return
                    if self.path in {"/assets/leaflet.js", "/assets/leaflet.css"}:
                        filename = self.path.removeprefix("/assets/")
                        asset = KISMET_HTTP_ASSETS / ("js" if filename.endswith(".js") else "css") / filename
                        try:
                            payload = asset.read_bytes()
                        except OSError:
                            self.send_error(404)
                            return
                        kind = "text/javascript" if asset.suffix == ".js" else "text/css"
                        self._send(payload, kind)
                        return
                    if self.path.startswith("/tiles/"):
                        relative = self.path.removeprefix("/tiles/").split("?", 1)[0]
                        parts = relative.split("/")
                        if len(parts) != 3 or not all(part.replace(".png", "").isdigit() for part in parts):
                            self.send_error(400)
                            return
                        tile = MAP_TILE_CACHE.joinpath(*parts)
                        try:
                            if not tile.exists():
                                request = urllib.request.Request(
                                    f"https://tile.openstreetmap.org/{relative}",
                                    headers={"User-Agent": "CIACORE-Wardrive/1.0 (local tile cache)"},
                                )
                                with urllib.request.urlopen(request, timeout=8) as response:
                                    payload = response.read(2 * 1024 * 1024)
                                tile.parent.mkdir(parents=True, exist_ok=True)
                                tile.write_bytes(payload)
                            self._send(tile.read_bytes(), "image/png")
                        except OSError:
                            self.send_error(404)
                        return
                    if self.path.startswith("/api/points"):
                        try:
                            credentials = base64.b64encode(
                                f"{app.api_username}:{app.api_password}".encode()
                            ).decode("ascii")
                            request = urllib.request.Request(
                                "http://127.0.0.1:2501/devices/views/phydot11_accesspoints/devices.json",
                                headers={"Authorization": f"Basic {credentials}"},
                            )
                            with urllib.request.urlopen(request, timeout=2) as response:
                                payload = json.dumps(device_map_points(json.load(response))).encode()
                            self._send(payload, "application/json")
                        except (OSError, ValueError):
                            self._send(b"[]", "application/json", 503)
                        return
                    if self.path.startswith("/api/track"):
                        self._send(json.dumps(app.gps_track).encode(), "application/json")
                        return
                    if self.path.startswith("/api/aircraft"):
                        try:
                            rows = [row for row in read_adsb_aircraft(receiver=app.last_gps_position)
                                    if isinstance(row.get("lat"), (int, float)) and isinstance(row.get("lon"), (int, float))]
                            self._send(json.dumps(rows).encode(), "application/json")
                        except (OSError, ValueError, json.JSONDecodeError):
                            self._send(b"[]", "application/json", 503)
                        return
                    if self.path.startswith("/api/replay"):
                        self._send(json.dumps(app.replay_points).encode(), "application/json")
                        return
                    self.send_error(404)

                def _send(self, payload: bytes, kind: str, status: int = 200) -> None:
                    self.send_response(status)
                    self.send_header("Content-Type", kind)
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format: str, *args: object) -> None:
                    pass

            self.map_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MapHandler)
            threading.Thread(target=self.map_server.serve_forever, daemon=True).start()
        port = self.map_server.server_address[1]
        page = "/replay" if replay else "/"
        if not webbrowser.open(f"http://127.0.0.1:{port}{page}", new=2):
            messagebox.showerror(APP_NAME, "Could not open the default web browser.")

    def _set_wdgwars_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.wdgwars_key_entry.configure(state=state)
        self.wdgwars_verify_button.configure(state=state)
        self.wdgwars_upload_button.configure(state=state)
        self.adsb_upload_button.configure(state=state)

    def _refresh_adsb_status(self) -> None:
        try:
            aircraft, positioned, messages = adsb_snapshot_status()
            self.adsb_status.set(
                f"ADS-B LIVE  //  {aircraft} aircraft  •  {positioned} positioned  •  {messages:,} messages"
            )
        except (OSError, ValueError, json.JSONDecodeError):
            self.adsb_status.set("ADS-B OFFLINE  //  start the readsb service")
        if not self.shutdown_event.is_set():
            self.after(2000, self._refresh_adsb_status)

    def show_aircraft(self) -> None:
        if self.aircraft_window and self.aircraft_window.winfo_exists():
            self.aircraft_window.lift()
            return
        window = tk.Toplevel(self)
        self.aircraft_window = window
        window.title("Live Aircraft / ADS-B")
        window.geometry("1120x680")
        window.minsize(820, 500)
        window.configure(background=COLORS["void"])
        window.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "aircraft_window", None), window.destroy()))

        toolbar = ttk.Frame(window, padding=10)
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="FILTER", style="Section.TLabel").pack(side="left")
        self.aircraft_filter = tk.StringVar(value="All")
        filter_box = ttk.Combobox(
            toolbar, textvariable=self.aircraft_filter, state="readonly", width=20,
            values=("All", "Nearby (<50 nm)", "Low (<10,000 ft)", "Climbing", "Descending",
                    "Emergency", "Stale (>10s)"),
        )
        filter_box.pack(side="left", padx=8)
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_aircraft_view(False))
        self.aircraft_summary = tk.StringVar(value="Loading live receiver data…")
        ttk.Label(toolbar, textvariable=self.aircraft_summary, style="Status.TLabel").pack(side="left", padx=12)
        ttk.Button(toolbar, text="LIVE MAP", command=self.open_live_map).pack(side="right")

        table_frame = ttk.Frame(window, padding=(10, 0, 10, 8))
        table_frame.pack(fill="both", expand=True)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=3)
        columns = ("flight", "icao", "alt", "speed", "track", "vertical", "distance", "bearing", "signal", "seen")
        self.aircraft_tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=13)
        headings = (("flight", "Flight"), ("icao", "ICAO"), ("alt", "Altitude"), ("speed", "Speed"),
                    ("track", "Track"), ("vertical", "Vertical"), ("distance", "Distance"),
                    ("bearing", "Bearing"), ("signal", "Signal"), ("seen", "Last seen"))
        for column, heading in headings:
            self.aircraft_tree.heading(column, text=heading)
            self.aircraft_tree.column(column, width=92, anchor="center", stretch=column in {"flight"})
        self.aircraft_tree.column("flight", width=110, anchor="w")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.aircraft_tree.yview)
        self.aircraft_tree.configure(yscrollcommand=scrollbar.set)
        self.aircraft_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.aircraft_tree.bind("<<TreeviewSelect>>", self._show_aircraft_detail)
        self.aircraft_detail = tk.Text(
            table_frame, height=10, bg=COLORS["panel"], fg=COLORS["text"], insertbackground=COLORS["cyan"],
            relief="solid", borderwidth=1, wrap="word", font=("DejaVu Sans Mono", 9), state="disabled",
        )
        self.aircraft_detail.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        self._refresh_aircraft_view()

    @staticmethod
    def _aircraft_value(value: object, suffix: str = "", digits: int = 0) -> str:
        if not isinstance(value, (int, float)):
            return "—"
        return f"{value:.{digits}f}{suffix}"

    def _refresh_aircraft_view(self, reschedule: bool = True) -> None:
        if not self.aircraft_window or not self.aircraft_window.winfo_exists():
            return
        try:
            rows = read_adsb_aircraft(receiver=self.last_gps_position)
            rows = filter_adsb_aircraft(rows, self.aircraft_filter.get())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.aircraft_summary.set(f"Receiver data error: {exc}")
            rows = []
        selected = self.aircraft_tree.selection()
        selected_icao = selected[0] if selected else ""
        self.aircraft_tree.delete(*self.aircraft_tree.get_children())
        self._aircraft_rows = {str(row["hex"]): row for row in rows}
        for row in rows:
            icao = str(row["hex"])
            vertical = row.get("baro_rate", row.get("geom_rate"))
            self.aircraft_tree.insert("", "end", iid=icao, values=(
                row.get("flight") or "—", icao.upper(), self._aircraft_value(row.get("alt_baro"), " ft"),
                self._aircraft_value(row.get("gs"), " kt"), self._aircraft_value(row.get("track"), "°"),
                self._aircraft_value(vertical, " fpm"), self._aircraft_value(row.get("distance_nm"), " nm", 1),
                self._aircraft_value(row.get("bearing"), "°"), self._aircraft_value(row.get("rssi"), " dBFS", 1),
                self._aircraft_value(row.get("seen"), " s", 1),
            ))
        if selected_icao in self._aircraft_rows:
            self.aircraft_tree.selection_set(selected_icao)
        positioned = sum(isinstance(row.get("lat"), (int, float)) for row in rows)
        origin = "GPS distance enabled" if self.last_gps_position else "waiting for GPS fix for distance"
        self.aircraft_summary.set(f"{len(rows)} shown  •  {positioned} positioned  •  {origin}")
        if reschedule:
            self.aircraft_window.after(2000, self._refresh_aircraft_view)

    def _show_aircraft_detail(self, _event: object = None) -> None:
        selected = self.aircraft_tree.selection()
        if not selected:
            return
        row = self._aircraft_rows.get(selected[0], {})
        fields = (
            ("Flight / callsign", row.get("flight") or "—"), ("ICAO24", str(row.get("hex", "—")).upper()),
            ("Coordinates", f"{row.get('lat', '—')}, {row.get('lon', '—')}"),
            ("Barometric altitude", self._aircraft_value(row.get("alt_baro"), " ft")),
            ("Geometric altitude", self._aircraft_value(row.get("alt_geom"), " ft")),
            ("Ground speed / track", f"{self._aircraft_value(row.get('gs'), ' kt')} / {self._aircraft_value(row.get('track'), '°')}"),
            ("Vertical rate", self._aircraft_value(row.get("baro_rate", row.get("geom_rate")), " fpm")),
            ("Distance / bearing", f"{self._aircraft_value(row.get('distance_nm'), ' nm', 1)} / {self._aircraft_value(row.get('bearing'), '°')}"),
            ("Squawk / emergency", f"{row.get('squawk', '—')} / {row.get('emergency', 'none')}"),
            ("Emitter category", row.get("category", "—")),
            ("Selected altitude", self._aircraft_value(row.get("nav_altitude_mcp"), " ft")),
            ("Selected heading / QNH", f"{self._aircraft_value(row.get('nav_heading'), '°')} / {self._aircraft_value(row.get('nav_qnh'), ' hPa', 1)}"),
            ("ADS-B version / source", f"{row.get('version', '—')} / {row.get('type', '—')}"),
            ("NIC / NACp / SIL", f"{row.get('nic', '—')} / {row.get('nac_p', '—')} / {row.get('sil', '—')}"),
            ("Signal / messages / age", f"{self._aircraft_value(row.get('rssi'), ' dBFS', 1)} / {row.get('messages', '—')} / {self._aircraft_value(row.get('seen'), ' s', 1)}"),
        )
        text = "\n".join(f"{label:24} {value}" for label, value in fields)
        self.aircraft_detail.configure(state="normal")
        self.aircraft_detail.delete("1.0", "end")
        self.aircraft_detail.insert("1.0", text)
        self.aircraft_detail.configure(state="disabled")

    def upload_adsb_wdgwars(self) -> None:
        key = self.wdgwars_api_key.get().strip()
        if not valid_wdgwars_api_key(key):
            messagebox.showerror(APP_NAME, "Enter and verify your 64-character WDGWars API key first.")
            return
        try:
            aircraft, positioned, _messages = adsb_snapshot_status()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror(APP_NAME, f"Could not read the live ADS-B snapshot:\n\n{exc}")
            return
        if not aircraft:
            messagebox.showerror(APP_NAME, "readsb is running, but no aircraft are currently visible.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Upload the current ADS-B snapshot to your WDGWars account?\n\n"
            f"Aircraft: {aircraft}\nWith decoded positions: {positioned}\n\n"
            "Only data received by your local Nooelec SDR will be sent.",
        ):
            return
        self._set_wdgwars_busy(True)
        self.wdgwars_status.set(f"Uploading ADS-B snapshot ({aircraft} aircraft)…")
        threading.Thread(target=self._upload_adsb_wdgwars_worker, args=(key,), daemon=True).start()

    def _upload_adsb_wdgwars_worker(self, key: str) -> None:
        try:
            detail = upload_wdgwars_adsb(key)
            self.wdgwars_queue.put({"kind": "adsb_upload", "detail": detail})
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.wdgwars_queue.put({"kind": "error", "message": str(exc)})

    def _schedule_wdgwars_key_save(self, *_args: object) -> None:
        if self._key_save_after_id:
            self.after_cancel(self._key_save_after_id)
        self._key_save_after_id = self.after(500, self._persist_wdgwars_key)

    def _persist_wdgwars_key(self) -> None:
        self._key_save_after_id = None
        key = self.wdgwars_api_key.get().strip()
        if not valid_wdgwars_api_key(key):
            return
        try:
            save_wdgwars_api_key(key)
        except OSError as exc:
            self.wdgwars_status.set(f"KEY SAVE ERROR  //  {exc}")

    def verify_wdgwars(self) -> None:
        key = self.wdgwars_api_key.get().strip()
        if not valid_wdgwars_api_key(key):
            messagebox.showerror(APP_NAME, "WDGWars API keys must contain 64 hexadecimal characters.")
            return
        self._set_wdgwars_busy(True)
        self.wdgwars_status.set("Contacting WDGWars…")
        threading.Thread(target=self._verify_wdgwars_worker, args=(key,), daemon=True).start()

    def _verify_wdgwars_worker(self, key: str) -> None:
        try:
            account = wdgwars_account(key)
            self.wdgwars_queue.put({"kind": "account", "account": account})
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            self.wdgwars_queue.put({"kind": "error", "message": self._wdgwars_error(exc)})

    @staticmethod
    def _wdgwars_error(error: BaseException) -> str:
        if isinstance(error, urllib.error.HTTPError):
            try:
                body = error.read(2048).decode("utf-8", errors="replace").strip()
                decoded = json.loads(body)
                detail = str(decoded.get("error", "")) if isinstance(decoded, dict) else ""
            except (OSError, ValueError):
                detail = ""
            if error.code == 401:
                return detail or "WDGWars rejected the API key"
            if error.code == 403:
                return detail or "WDGWars or Cloudflare denied this request"
            if error.code == 413:
                return "The capture exceeds WDGWars' upload limit"
            if error.code == 429:
                return "WDGWars rate limit reached; try again later"
            if error.code in {502, 503, 504}:
                return "WDGWars is temporarily unavailable after 3 attempts; try again shortly"
            return detail or f"WDGWars returned HTTP {error.code}"
        return str(error) or "Could not contact WDGWars"

    def _newest_wigle_csv(self) -> Path | None:
        if self.latest_wigle_csv and self.latest_wigle_csv.is_file():
            return self.latest_wigle_csv
        try:
            files = list(Path(self.log_directory.get()).expanduser().glob("*.wiglecsv"))
            return max(files, key=lambda path: path.stat().st_mtime_ns) if files else None
        except OSError:
            return None

    def upload_latest_wdgwars(self) -> None:
        csv_path = self._newest_wigle_csv()
        if not csv_path:
            messagebox.showerror(APP_NAME, "No WiGLE CSV was found in the selected capture directory.")
            return
        self._confirm_wdgwars_upload(csv_path)

    def upload_selected_wdgwars(self) -> None:
        stats = self._selected_session()
        if not stats:
            messagebox.showerror(APP_NAME, "Select a capture session from the history table first.")
            return
        self._confirm_wdgwars_upload(stats.path)

    def _confirm_wdgwars_upload(self, csv_path: Path) -> None:
        key = self.wdgwars_api_key.get().strip()
        if not valid_wdgwars_api_key(key):
            messagebox.showerror(APP_NAME, "Enter and verify your 64-character WDGWars API key first.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Upload {csv_path.name} to your WDGWars account?\n\n"
            "Its wireless observations and location data will be sent to WDGWars.",
        ):
            return
        self._set_wdgwars_busy(True)
        self.wdgwars_status.set(f"Uploading {csv_path.name}…")
        threading.Thread(target=self._upload_wdgwars_worker, args=(key, csv_path), daemon=True).start()

    def _upload_wdgwars_worker(self, key: str, csv_path: Path) -> None:
        try:
            result = upload_wdgwars_csv(key, csv_path)
            self.wdgwars_queue.put({"kind": "upload", "result": result, "path": csv_path})
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            self.wdgwars_queue.put({"kind": "error", "message": self._wdgwars_error(exc)})

    def _drain_wdgwars(self) -> None:
        try:
            while True:
                update = self.wdgwars_queue.get_nowait()
                kind = update.get("kind")
                if kind == "account":
                    account = update["account"]
                    username = account.get("username", "unknown")
                    total = account.get("total", 0)
                    gang = account.get("gang") or "solo"
                    self.wdgwars_status.set(f"CONNECTED  //  {username}  •  {total:,} total  •  {gang}")
                    self._append_output(f"WDGWars account verified: {username}\n")
                    self._set_wdgwars_busy(False)
                elif kind == "upload":
                    result = update["result"]
                    path = update["path"]
                    added = result.get("added", result.get("new", result.get("count", "accepted")))
                    self.wdgwars_status.set(f"UPLOAD COMPLETE  //  {path.name}  •  {added}")
                    self._append_output(f"WDGWars upload complete: {path.name}\n")
                    self.upload_history[str(path.resolve())] = datetime.now().isoformat(timespec="seconds")
                    try:
                        save_upload_history(self.upload_history)
                    except OSError as exc:
                        self._append_output(f"Could not save upload history: {exc}\n")
                    self._set_wdgwars_busy(False)
                    self.refresh_capture_history()
                elif kind == "capture_ready":
                    self.latest_wigle_csv = Path(str(update["path"]))
                    self.refresh_capture_history()
                elif kind == "adsb_upload":
                    self.wdgwars_status.set("ADS-B UPLOAD COMPLETE  //  snapshot accepted")
                    self._append_output(f"WDGWars ADS-B upload complete: {update.get('detail', 'accepted')}\n")
                    self._set_wdgwars_busy(False)
                elif kind == "error":
                    message = str(update["message"])
                    self.wdgwars_status.set(f"UPLINK ERROR  //  {message}")
                    self._append_output(f"WDGWars: {message}\n")
                    self._set_wdgwars_busy(False)
        except queue.Empty:
            pass
        if not self.shutdown_event.is_set():
            self.after(350, self._drain_wdgwars)

    def _append_output(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text)
        self._output_line_count += text.count("\n")
        excess = self._output_line_count - OUTPUT_MAX_LINES
        if excess > 0:
            self.output.delete("1.0", f"{excess + 1}.0")
            self._output_line_count -= excess
        self.output.see("end")
        self.output.configure(state="disabled")

    def _create_api_config(self) -> Path:
        fd, name = tempfile.mkstemp(prefix="kismet-wardrive-", suffix=".conf")
        config = (
            "httpd_bind_address=127.0.0.1\n"
            f"httpd_username={self.api_username}\n"
            f"httpd_password={self.api_password}\n"
            "retry_on_source_error=true\n"
        )
        try:
            os.write(fd, config.encode("utf-8"))
        finally:
            os.close(fd)
        self.api_config_path = Path(name)
        return self.api_config_path

    def _remove_api_config(self) -> None:
        path, self.api_config_path = self.api_config_path, None
        if path:
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def start_wardrive(self) -> None:
        if self.process and self.process.poll() is None:
            return
        if not shutil.which("kismet"):
            messagebox.showerror(APP_NAME, "Kismet was not found in PATH. Install Kismet first.")
            return
        sources = self.selected_sources()
        if not sources:
            messagebox.showerror(APP_NAME, "Select at least one network adapter.")
            return
        hop_errors = hopping_configuration_errors(sources)
        if hop_errors:
            messagebox.showerror(APP_NAME, "Invalid channel hopping configuration:\n\n" + "\n".join(hop_errors))
            return
        available = set(network_interfaces())
        if any(source.interface not in available for source in sources):
            messagebox.showerror(APP_NAME, "A selected network adapter is no longer available.")
            self.refresh_interfaces()
            return
        for source in sources:
            if source.mode == "fixed" and (not source.channels.strip() or "," in source.channels):
                messagebox.showerror(APP_NAME, f"{source.interface}: fixed mode requires exactly one channel.")
                return

        log_dir = Path(self.log_directory.get()).expanduser()
        checks = preflight_checks(sources, log_dir)
        blockers = [item for item in checks if not item.ok and item.name not in {"GPSD", "CSV converter"}]
        if blockers:
            messagebox.showerror(APP_NAME, "Preflight failed:\n\n" +
                                 "\n".join(f"{item.name}: {item.detail}" for item in blockers))
            return
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Cannot create the log directory:\n{exc}")
            return

        self._remove_api_config()
        command = build_command(sources, log_dir, self._create_api_config())
        self.session_log_directory = log_dir
        self.session_log_title = command[command.index("--log-title") + 1]
        self.ap_count.set("0")
        self.ap_status.set("Starting Kismet…")
        activity_started = time.monotonic()
        self.ap_activity_samples[:] = [(activity_started, 0)]
        self._last_ap_pickup = activity_started
        self.ap_activity.set("WAITING FOR PICKUPS  •  0.0 new APs/min")
        self.ap_activity_label.configure(fg=COLORS["muted"])
        self.ap_count_label.configure(fg=COLORS["muted"])
        self._draw_ap_activity()
        self.capture_size.set("Waiting for Wigle CSV…")
        self.gps_track.clear()
        self._adapter_packet_samples.clear()
        self._adapter_last_packet_change.clear()
        self._adapter_stall_notified.clear()
        self._channel_warning_notified.clear()
        self._channel_status.clear()
        self._source_runtime.clear()
        self._channel_observations.clear()
        self.channel_warning.set("Channel telemetry waiting for Kismet")
        self.channel_warning_label.configure(fg=COLORS["muted"])
        self.networks.delete(*self.networks.get_children())
        self._append_output(f"Starting: {' '.join(command)}\n")
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
        except OSError as exc:
            self._remove_api_config()
            messagebox.showerror(APP_NAME, f"Could not start Kismet:\n{exc}")
            return

        self.status.set(f"Running (PID {self.process.pid})")
        self.session_interfaces = [source.interface for source in sources]
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._set_source_controls_state(False)
        self.refresh_button.configure(state="disabled")
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _read_process_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            self.output_queue.put(line)
        process.wait()
        self.output_queue.put(None)

    def _drain_output(self) -> None:
        lines: list[str] = []
        process_exited = False
        for _ in range(OUTPUT_BATCH_LINES):
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                process_exited = True
                break
            lines.append(line)

        if lines:
            self._append_output("".join(lines))
        if process_exited:
            code = self.process.returncode if self.process else "unknown"
            self._append_output(f"\nKismet exited (code {code}).\n")
            self._set_stopped()
            self._notify("Wardrive stopped", f"Kismet exited with code {code}.")

        # Yield to Tk between batches so capture output cannot starve status,
        # AP, GPS, redraw, and input events when Kismet becomes noisy.
        delay = 10 if len(lines) == OUTPUT_BATCH_LINES and not process_exited else 100
        self.after(delay, self._drain_output)

    def stop_wardrive(self) -> None:
        if not self.process or self.process.poll() is not None:
            self._set_stopped()
            return
        self.status.set("Stopping…")
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            self._set_stopped()

    def _set_stopped(self) -> None:
        self._remove_api_config()
        self.status.set("Restoring Wi-Fi…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self._set_source_controls_state(True)
        self.refresh_button.configure(state="normal")
        if not self.network_restore_running:
            self.network_restore_running = True
            threading.Thread(target=self._restore_network_worker, daemon=True).start()

    def _restore_network_worker(self) -> None:
        messages: list[str] = []

        try:
            if self.session_log_directory and self.session_log_title:
                output, error = export_wigle_csv(self.session_log_directory, self.session_log_title)
                if output:
                    self.output_queue.put(f"Wigle CSV saved: {output}\n")
                    self.wdgwars_queue.put({"kind": "capture_ready", "path": output})
                else:
                    messages.append(error)

            for interface in self.session_interfaces:
                managed = managed_interface_name(interface)
                monitor = monitor_interface_name(interface)
                if monitor and shutil.which("airmon-ng"):
                    command = ["airmon-ng", "stop", monitor]
                    if shutil.which("pkexec"):
                        command.insert(0, "pkexec")
                    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    if result.returncode:
                        messages.append(f"{monitor} cleanup needs permission")

                if shutil.which("nmcli"):
                    subprocess.run(
                        ["nmcli", "device", "set", managed, "managed", "yes"],
                        capture_output=True, timeout=15,
                    )
                    subprocess.run(
                        ["nmcli", "device", "wifi", "rescan", "ifname", managed],
                        capture_output=True, timeout=20,
                    )
            if shutil.which("nmcli"):
                subprocess.run(["nmcli", "radio", "wifi", "on"], capture_output=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            messages.append("automatic Wi-Fi restore did not finish")

        self.network_queue.put("; ".join(messages) if messages else "Wi-Fi restored")

    def _drain_network_status(self) -> None:
        try:
            result = self.network_queue.get_nowait()
        except queue.Empty:
            if not self.shutdown_event.is_set():
                self.after(300, self._drain_network_status)
            return

        self.network_restore_running = False
        self.session_interfaces = []
        self.session_log_directory = None
        self.session_log_title = None
        self.status.set(result)
        self.start_button.configure(state="normal")
        if self.close_pending:
            self._finish_close()
            return
        if not self.shutdown_event.is_set():
            self.after(300, self._drain_network_status)

    def on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno(APP_NAME, "Kismet is running. Stop it and exit?"):
                return
            self.close_pending = True
            self.stop_wardrive()
            return
        if self.network_restore_running:
            self.close_pending = True
            return
        self._finish_close()

    def _finish_close(self) -> None:
        self.shutdown_event.set()
        if self.map_server:
            self.map_server.shutdown()
            self.map_server.server_close()
            self.map_server = None
        self._remove_api_config()
        self.destroy()


if __name__ == "__main__":
    WardriveApp().mainloop()
