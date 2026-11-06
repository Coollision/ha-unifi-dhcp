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
