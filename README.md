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
- SMS inbox readout (count, unread, storage capacity + latest message preview)  
- SMS compose + send (via Home Assistant text field and button)  
- Device information (model, firmware, IMEI/ICCID where available)  
- Optional controls (e.g. reboot, mobile data on/off)

If a feature is missing, please open an issue to report it.

## Requirements

- Local network connectivity between Home Assistant and the router IP
