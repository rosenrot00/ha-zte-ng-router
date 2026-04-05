DOMAIN = "zte_ng_router"

CONF_NAME = "name"
CONF_ROUTER_TYPE = "router_type"
CONF_VERIFY_TLS = "verify_tls"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_FAST_SCAN_INTERVAL = "fast_scan_interval"

# Supported router types (internal value -> user-facing label)
ROUTER_TYPES = {
    "g5tc": "ZTE G5TC",
    "g5ts": "ZTE G5TS",
    "g5c": "ZTE G5C",
    "g5max": "ZTE G5 Max/Ultra",
}

# Slow (full) update interval – used for most sensors
DEFAULT_SCAN_INTERVAL = 60      # seconds
MIN_SCAN_INTERVAL = 5           # lower bound to avoid spamming the router
MAX_SCAN_INTERVAL = 3600        # upper bound (1 hour)

# Fast update interval – used for throughput / connected-time
DEFAULT_FAST_SCAN_INTERVAL = 5  # seconds
MIN_FAST_SCAN_INTERVAL = 2      # allow near-realtime without overload
MAX_FAST_SCAN_INTERVAL = 3600   # cap fast polling to 1 hour

# Default helper text for SMS compose input (number,message)
SMS_COMPOSE_DEFAULT = "number,message"
