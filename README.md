# Kismet Wardrive Launcher

A small Python/Tk desktop launcher for Kismet's optimized wardrive configuration.
It includes CIACORE Cybersecurity branding and live GPS status from GPSD. A red/green indicator shows whether GPS has a fix.
The logo and field-console branding bar remain fixed at the top while the dashboard
scrolls; primary capture controls remain fixed along the bottom.
The fixed header also contains a large GPS banner designed for quick glances while
driving: green means **GPS Locked**, amber means **Acquiring GPS**, and red means
**GPS Lost** or stale coordinates. It includes the selected receiver, satellite count,
and fix age. Warning states pulse gently, with one audible cue when a lock is lost and
two when it recovers.
The large **Unique Access Points Found** counter and signal-sorted network list read Kismet's dedicated Wi-Fi AP view every second. A temporary failed API poll keeps the last valid display instead of clearing or greying it. The **Adapter Pickup Stats** panel attributes unique APs and Kismet's live packet count to each capture adapter. It shows the current channel and verifies hopping only after observing the radio change channels; `verifying` changes to `verified` once at least two channels have been seen. Adapter health distinguishes normal `QUIET` airtime from a sustained `STALLED` condition and reports Kismet's `RETRYING` or `DISCONNECTED` source state. Kismet's automatic source retry remains enabled for unplugged or reset adapters. While capturing, the launcher also displays the current Wigle CSV file size.

Below the AP counter, a live 60-second activity histogram measures newly discovered
unique APs per minute. Its glanceable status changes between **Light Activity**,
**Active**, **Very Active**, and **Dead Zone**; a dead zone is reported after 30 seconds
without a new AP while capture is running. This deliberately measures discovery rate,
not background packet volume from networks already seen.

## Run

```bash
./run.sh
```

## Install on another Kali/Debian computer

Build the Debian package on this computer:

```bash
./packaging/build-deb.sh
```

Copy `dist/ciacore-wardrive_1.0.4_all.deb` to the destination, then install it:

```bash
sudo apt install ./ciacore-wardrive_1.0.4_all.deb
```

Published versions can instead be downloaded from the repository's
[Releases](https://github.com/Coldpronto/ciacore-wardrive/releases) page. Download
the `.deb` attached to the latest release, then install it with the command above.

Launch **CIACORE Wardrive** from the application menu or run
`ciacore-wardrive`. The package installs Kismet, its log converter, and Tk as
required dependencies. GPSD, NetworkManager integration, desktop notifications,
and aircrack-ng recovery tools are recommended dependencies.

On the destination computer, add the operator to the `kismet` group if the Kismet
installer did not already do so, then log out and back in:

```bash
sudo usermod -aG kismet "$USER"
```

Wi-Fi adapter support, GPSD receiver configuration, and any optional `readsb` /
Muninn ADS-B setup are machine-specific and are not changed by the package.

Select one or more Wi-Fi adapters and a log directory, then select **Start Wardrive**. Each adapter has a **Channel group** selector for all supported channels, 2.4 GHz priority or full coverage, 5 GHz DFS/non-DFS/full coverage, combined 2.4+5 GHz, and 6 GHz PSC channels. **Custom hop** and **Fixed channel** keep a manual field available for unusual hardware or survey plans. Kismet automatically splits hopping coverage between compatible radios.

A custom hopping plan must contain at least two channels. The launcher refuses a one-channel hop configuration because it looks enabled in Kismet while behaving like a fixed-channel capture; use **Fixed channel** when that behavior is intentional.

Use the arrow beside **Wi-Fi Adapters** to collapse or expand a long adapter list.
The header continues to show how many adapters are selected and available while the
list is minimized, and collapsing it does not change any adapter settings.

The launcher runs the equivalent of:

```bash
kismet --no-ncurses --no-line-wrap --override wardrive --log-title wardrive-TIMESTAMP --log-prefix DIRECTORY -c wlan0:channel_hop=true -c wlan1:channel_hop=false,channel=36
```

Kismet normally exposes its full interface at <http://localhost:2501> while running.
Select **Live Map** after starting a scan to open the launcher's GPS-tagged
access-point map in the default browser. It updates every two seconds and does
not require a separate login. Open Kismet itself at <http://localhost:2501> for
the full Kismet interface.

## Field tools

- **Preflight** checks Kismet and its CSV converter, selected adapters, capture-directory
  permissions, free disk space, and GPSD before departure. Capture startup repeats the
  blocking checks automatically.
- **Profiles** applies built-in 2.4, 5, and 6 GHz channel plans or saves the current
  adapters, channel configuration, and log directory as a named local profile.
- **Adapter health** adds packet rate and stalled-radio warnings to the per-adapter table.
- **Recover** finds `.kismet` databases without a completed `.wiglecsv` and converts them.
- Desktop notifications report GPS loss, stalled adapters, capture completion, and exports
  when `notify-send` is installed.

The live map draws the GPS route and marks GPS coverage gaps. OpenStreetMap tiles pass
through a local cache under `~/.cache/ciacore-wardrive/tiles`, so previously viewed areas
remain available when the computer goes offline.

## WDGWars upload

The **WDGWars Uplink** panel can verify a 64-character API key and manually upload
the newest `.wiglecsv` in the selected log directory. Generate the key in your
WDGWars profile, paste it into the masked field, and select **Verify**. After a
capture is exported, select **Upload latest CSV** and confirm the file.

The key is saved locally in `~/.config/ciacore-wardrive/settings.json` with
owner-only permissions (`0600`) and is never included in the event log. Upload is
never automatic. A WiGLE CSV can contain wireless observations and GPS coordinates,
so review the selected file and the WDGWars terms before sending it.

### ADS-B

When `readsb` is running, the WDGWars panel also shows live aircraft, positioned
aircraft, and decoder message counts from `/run/readsb/aircraft.json`. **Upload
ADS-B snapshot** confirms the current counts and then sends only that snapshot.
ADS-B uploads are never automatic.

The persistent **Aircraft / ADS-B** header also opens a live aircraft console.
Its table shows flight, ICAO24, altitude, speed, track, vertical rate, receiver
distance/bearing, signal, and age; selecting a row reveals squawk, emergency,
coordinates, autopilot selections, emitter category, and integrity fields. Filters
cover nearby, low, climbing, descending, emergency, and stale aircraft. The live
map draws positioned aircraft in magenta with a heading vector.

The signed ADS-B transport uses the WDGWars-recommended Muninn checkout expected
at `../adsb-to-wdgwars/muninn.py`. Muninn normalizes readsb/dump1090 JSON and posts
it to WDGWars' ADS-B endpoint; the API key is supplied to the child process without
placing it on the command line. Install and enable the local decoder with:

```bash
sudo apt install readsb
sudo systemctl enable --now readsb
```

## Capture history and statistics

The launcher scans up to 200 `.wiglecsv` files in the selected capture directory
on a background thread. The history table shows the capture date, unique AP count,
session duration, file size, and whether that file was uploaded through the
launcher. Select a row for its hidden-network count, strongest RSSI, channel list,
and WPA3/WPA2/WPA/WEP/open breakdown. **Upload selected** sends that specific
historical capture after confirmation.

Successful upload markers are stored in
`~/.config/ciacore-wardrive/uploads.json` with owner-only permissions. They are a
local record of launcher uploads, not a server-side WDGWars history.

Select a history row to open channel/security/signal analytics, compare it with an older
drive, or replay its GPS-tagged observations on a timeline. **Export…** writes mapped
observations as GeoJSON, KML, or GPX for GIS and mapping applications.

## Permissions and GPS

Kismet recommends an installation with its capture helper configured for elevated privileges and your user added to the `kismet` group. This launcher deliberately does not store a password or invoke a shell. If Kismet reports permission errors, follow the Kismet installation instructions for your distribution.

For location-tagged Wigle CSV output, configure a GPS source in Kismet. Wardrive mode enables Wigle CSV logging, but records entries only when GPS data is available.
When a capture stops, the launcher also runs Kismet's converter against the completed session database so the `.wiglecsv` is reliably written to the selected log directory.

The status panel connects to GPSD at its standard local endpoint (`127.0.0.1:2947`) and displays fix quality, coordinates, altitude, speed, and satellites used. It reconnects automatically when GPSD or the receiver is not ready.

The **GPS Source** selector lists every receiver currently exposed by GPSD and labels
common Linux serial paths as **Internal** or **USB**. Choose a specific receiver to
ignore reports from the others, or leave it on **Automatic**. After plugging in a USB
receiver, select **Refresh GPS**. The receiver must already be attached to GPSD; the
launcher does not rewrite system GPSD configuration or request administrator access.

Only capture radio traffic where you are legally authorized to do so.
