"""Constants for the UniFi DHCP enrichment integration."""

from datetime import timedelta
import logging

LOGGER = logging.getLogger(__package__)

DOMAIN = "unifi_dhcp"

CONF_UNIFI_ENTRY_ID = "unifi_entry_id"
CONF_NETWORKS = "networks"

# How often the whole UniFi client table is re-fed to the dhcp integration and the
# link to the UniFi entry is re-checked. Feeding is idempotent (the dhcp integration
# ignores data it has already processed), so this is cheap.
RESYNC_INTERVAL = timedelta(minutes=15)

ISSUE_UNIFI_UNAVAILABLE = "unifi_unavailable"
ISSUE_NETWORKS_MISSING = "networks_missing"

# Safety limits on what is handed to the dhcp integration. The built-in sweep does
# one burst per hour; these keep this integration from ever flooding Home Assistant,
# however many clients a controller reports.
#
# Hand-overs per second, over all clients (a token bucket that also allows a burst of
# this size). Thousands of clients are therefore spread over minutes, not milliseconds.
HANDOVERS_PER_SECOND = 20
# Most clients waiting for their turn. Beyond this a client is left for the next pass.
MAX_PENDING = 5000
# The shortest time between two hand-overs of the same client, however often UniFi
# reports a change for it.
CLIENT_COOLDOWN = timedelta(seconds=60)
# A pass over a large client table gives the event loop back this often.
PASS_YIELD_EVERY = 250

SERVICE_RESYNC = "resync"
ATTR_FORCE = "force"
