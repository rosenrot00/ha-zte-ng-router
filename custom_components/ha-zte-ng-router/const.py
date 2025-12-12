DOMAIN = "zte_ng_router"

CONF_NAME = "name"
CONF_ROUTER_TYPE = "router_type"
CONF_VERIFY_TLS = "verify_tls"
CONF_SCAN_INTERVAL = "scan_interval"

# Single router type for now – extend later if needed
ROUTER_TYPES = {
    "g5tc": "ZTE G5TC",
}

DEFAULT_SCAN_INTERVAL = 60  # seconds
MIN_SCAN_INTERVAL = 5       # lower bound to avoid spamming the router
MAX_SCAN_INTERVAL = 3600    # upper bound (1 hour)
