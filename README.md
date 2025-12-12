# ZTE NG Router – Home Assistant Custom Integration

Custom integration for Home Assistant to monitor and control recent ZTE “NG” routers (5G/FWA and similar CPEs) via their HTTP API.

It exposes key connection metrics as sensors and (optionally) control functions as services/switches, so you can use your router in dashboards and automations.

> ⚠️ This is an early version. Entity names and supported features may still change between releases.

---

## Features

Typical capabilities include (depending on router model/firmware):

- Connection status and uptime  
- WAN information  
  - External IP address  
  - Connection type (4G/5G/NR, etc.)  
  - Signal quality indicators (RSRP/RSRQ/SINR/PCI, bands, etc.)  
- Traffic and usage  
  - Total upload/download  
  - Current throughput  
- Device information  
  - Router model and firmware version  
  - IMEI/ICCID (where available)  
- Optional controls (if supported by your model)  
  - Reboot router  
  - Toggle mobile data  
  - Other actions exposed as services

Check your Home Assistant instance after setup to see the exact list of entities created for your router.

---

## Requirements

- Home Assistant 2024.XX or newer  
- A supported ZTE router reachable from your Home Assistant instance  
  - Router web UI/API enabled and not blocked by your firewall  
- Local network connectivity between Home Assistant and the router IP

---

## Installation

### 1. Manual installation

1. In your Home Assistant configuration directory, create the folder (if it does not yet exist):

   ```text
   custom_components
