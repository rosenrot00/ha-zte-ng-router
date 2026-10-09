[![HACS Default](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)
![Installation Count](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.zte_ng_router.total)

# ZTE NG Router – Home Assistant Custom Integration

This integration targets **recent ZTE NG router platforms** with a shared firmware and API structure.

### Supported models
- **ZTE G5TC** – 5G FWA / Indoor CPE  
- **ZTE G5TS** – 5G Indoor CPE (Wi-Fi 6)  
- **ZTE G5C**  
- **ZTE G5 Max**  
- **ZTE G5 Ultra**

<img width="241" height="340" alt="image" src="https://github.com/user-attachments/assets/5c20d64b-420c-4eb6-9755-0bcd7ee9628e" />
<img width="232" height="317" alt="image" src="https://github.com/user-attachments/assets/f106729c-4dea-4360-8d36-cc292df123d0" />

> ⚠️ Support depends on **firmware version and operator customizations**. Please keep in mind that while this integration is running, you will be logged out from the webui on every poll as only a single login is allowed on these devices.

If you face any issue with this integration, consider contributing that information via an issue.

## Installation

Just click here to open HACS directly in your Homeassistant to install this custom integration.

[![HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=rosenrot00&repository=ha-zte-ng-router&category=integration)

## Features

Feature availability depends on router model and firmware.

- Connection status and uptime  
- Access technology (4G / 5G / NR)  
- Radio signal metrics (RSRP, RSRQ, SINR, PCI, bands)  
- WAN / external IP information  
- Traffic counters and current throughput  
- Daily download/upload and lifetime packet error/drop counters
- Read-only monthly limit settings, warning threshold, exceeded status and traffic reset schedule
- RAM usage matching the router WebUI percentage (available memory, rounded to whole percent)
- Firmware update status, advertised version and download progress
- Manual firmware check and a separate update-start button
- GNSS latitude/longitude with fix status, source and the router's original fix time
- Optional WLAN/LAN client trackers with MAC/IP/hostname and presence history
- SMS inbox readout (count, unread, storage capacity + latest message preview)  
- SMS compose + send (via Home Assistant text field and button)  
- Device information (model, firmware, IMEI/ICCID where available)  
- Optional controls (e.g. reboot, mobile data on/off)

If a feature is missing, please open an issue to report it.

### Polling Groups

- **Fast** (default 5 seconds): download/upload rates, connected-since timestamp,
  CPU/RAM usage, daily download/upload, packet errors and packet drops.
- **Slow/full** (default 60 seconds): other sensors, including monthly traffic,
  data limit settings/status, reset schedule, firmware status/progress and GNSS.

Both intervals are configurable. Daily and packet counters are already returned
by the fast traffic request; RAM adds `meminfo` to the existing CPU request.
These sensors therefore do not add HTTP requests to the fast polling cycle.
Manual firmware actions request a full refresh without moving firmware metadata
into permanent fast polling.

### Network Client Tracking

1. In the integration's **Configure** dialog, enable **Track network clients (Wi-Fi and LAN)**.
2. After discovery, open **Settings > Devices & services > Entities**, filter by
   this integration and include disabled entities. Enable the trackers you want.
3. Add those `device_tracker` entities to a dashboard, an automation, or a person
   under **Settings > People**. New trackers are disabled by default; your later
   enable/disable choices are preserved.

Tracking is off by default and adds no requests while off. When enabled, client
lists are read during slow/full polling, not fast polling. UBUS routers exposing
`router_wireless_access_list` and `router_lan_access_list` are supported; no GoForm
client API is guessed. Both active WLAN and wired LAN clients are tracked, not
offline/DHCP lease lists. Guest/mesh/switch coverage depends on what the router reports.
The LAN call shares the client batch with WLAN pages and the final count check,
so it does not add another HTTP request to that slow-poll cycle.

Each tracker is identified by the router entry and normalized MAC, so changing
IP addresses or hostnames do not create duplicate entities. It exposes hostname,
IPv4, IPv6 (when supplied), raw `interface_type`, and UTC `last_seen`. No band/SSID
mapping is invented. `connection_type` is `wifi`, `lan`, or `wifi+lan` if a MAC
appears in both lists; `connection_types` also exposes these as a list. The same
MAC retains one tracker when switching connection type. Separate wired/wireless
MACs remain separate trackers, since their physical identity cannot be inferred.
IP addresses on disconnected trackers are last-known values.
MAC/IP/hostnames from the client list are not dumped into raw polling logs.

`home` means seen on this router's WLAN or LAN, not proof that a person is physically
home. A return is reported on the next successful poll. Absence is reported only
after the **Client absence grace period** (default 180 seconds, configurable
0-3600), starting with the first complete snapshot missing that client. With the
default slow interval, departure detection therefore takes roughly 3-4 minutes.
All pages and the final device counts must agree before absence is confirmed.
If one client list fails, sightings from the other remain usable, but missing
clients become unavailable instead of being reported away. Changing counts or
failure of both lists makes all trackers unavailable; unrelated sensors remain
usable. Polling pauses freeze the last state and reset the absence
grace period. Restarts restore existing tracker identities and enabled trackers'
last-seen metadata, but do not restore an assumed presence state. Offline clients
can remain unknown until the grace period has been confirmed after restart.

Phones using rotating private MAC addresses can appear as new clients. This is
read-only tracking: it never blocks, disconnects or changes any client/router.

### Firmware and GNSS

Firmware metadata and GNSS are read during normal full polling, not fast polling.
Reading firmware status does not search for updates or start downloads. Use
**Check Firmware Update** to ask the router to search; later polls show its result.
**Start Firmware Update** is available only when the reported state offers an
update or a downloaded package is ready. It rechecks that state before approving
the download or starting installation. Depending on firmware, approving a download
may also initiate installation and reboot the router, interrupting Internet access.
No firmware update is started automatically by this integration. These controls
use the UBUS methods defined by the router WebUI; unsupported APIs are not guessed.
Installation has not been live-tested, to avoid disrupting the connection.

GNSS coordinates can be a saved position rather than a current fix. Check
**GNSS Fix** and the `fix_time` attribute before treating them as current.
Coordinates are not included in raw polling debug logs. Daily traffic uses the
router's day boundary, not a separately calculated Home Assistant midnight reset.
Optional firmware/GNSS read failures do not prevent other sensors from updating.

### Data Limits

Normal full polling also reads `get_wwandst_monthlimit` and
`get_wwandst_clearday` in the existing monthly batch. Sensors show whether the
limit is enabled, its type and configured value, the warning percentage, exceeded
status, automatic counter reset and the monthly reset day (1-31).
Disabled limits retain their configured values. Missing/unsupported fields are
unknown, not assumed to be zero or off. These sensors never change settings.

**Data Limit Value** preserves the router's integer value with no statistics or
assumed byte/GB conversion. Its `value_unit` attribute is `s` for a connection-time
limit (as defined by the WebUI service) and `router_native` for a volume limit;
the volume unit has not been verified. The reset day is a monthly day number,
not a timestamp.

## Requirements

- Local network connectivity between Home Assistant and the router IP

## SMS encryption

Some firmware, including the G51F / MC7510 reported in [issue #8](https://github.com/rosenrot00/ha-zte-ng-router/issues/8),
requires AES-GCM encryption for SMS recipients and message bodies. The integration
automatically uses firmware/model identifiers from normal polling and the SMS
format actually returned by the router. AES-GCM fields are recognized only after
successful authenticated decryption; existing plain-field routers such as G5TC
retain their handling. The G51F / MC7510 model hint is based on the issue report
and has not been tested live on a G51F.

No manual router-model or encryption selection is needed. Existing installations
keep working without reconfiguration; old stored model selections are ignored.
If neither metadata nor inbox messages identify the SMS format (for example, an
unknown model with an empty inbox), sending is blocked with an explanatory error
rather than guessing or sending a test SMS. SMS reads remain batched. Failed
sends are not retried with a different encryption mode.
