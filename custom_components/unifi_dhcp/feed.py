"""Feed UniFi clients into Home Assistant's DHCP discovery."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from aiounifi import AiounifiException
from aiounifi.interfaces.api_handlers import ItemEvent
from homeassistant.components.dhcp import WatcherBase
from homeassistant.components.dhcp.models import DATA_DHCP
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CONF_NETWORKS,
    ISSUE_NETWORKS_MISSING,
    ISSUE_UNIFI_UNAVAILABLE,
    LOGGER,
    RESYNC_INTERVAL,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from aiounifi.models.client import Client
    from homeassistant.components.unifi import UnifiConfigEntry
    from homeassistant.components.unifi.hub import UnifiHub


# A client that keeps changing address or hostname must not grow the memory forever.
MAX_VALUES_PER_CLIENT = 8

# The outcomes double as the FeedStats counter they increment.
OUTCOME_FED = "fed"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_OTHER_NETWORK = "skipped_other_network"
OUTCOME_NO_IPV4 = "skipped_no_ipv4"


def _ipv4(address: str) -> str:
    """Return the address if it is IPv4, else an empty string.

    UniFi reports a single ``ip`` that can alternate between a client's IPv4 and its
    global IPv6 address. DHCP discovery is about DHCPv4, so only IPv4 is handed over.
    """
    try:
        return address if IPv4Address(address) else ""
    except ValueError:
        return ""


@dataclass(slots=True)
class FeedStats:
    """What the last full pass did, plus what live events did since then."""

    clients: int = 0
    fed: int = 0
    unchanged: int = 0
    skipped_other_network: int = 0
    skipped_no_ipv4: int = 0
    events_fed: int = 0
    last_resync: datetime | None = None


class UnifiDhcpFeed(WatcherBase):
    """Hand UniFi clients of the selected networks to the dhcp integration.

    The dhcp integration only discovers hosts it can see on its own layer 2 segment.
    UniFi already knows every client on every network, so its client table is used as
    one more source for ``async_process_client``, which does the actual matching
    against the integration matchers and starts the discovery flows.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        unifi_entry: UnifiConfigEntry,
    ) -> None:
        """Initialize the feed."""
        super().__init__(hass, hass.data[DATA_DHCP])
        self._entry = entry
        self._unifi_entry = unifi_entry
        self._networks: frozenset[str] = frozenset(entry.options[CONF_NETWORKS])
        self._hub: UnifiHub | None = None
        self._hub_unsubs: list[Callable[[], None]] = []
        self._resync_unsub: Callable[[], None] | None = None
        self._available = True
        self._missing_networks: frozenset[str] = frozenset()
        # What was handed over per MAC. UniFi reports a change for every client every
        # few seconds (traffic counters), and some clients alternate between two
        # hostnames or addresses, so a value is only passed on the first time.
        self._fed: dict[str, set[tuple[str, str]]] = {}
        self.stats = FeedStats()

    @property
    def networks(self) -> frozenset[str]:
        """Return the UniFi network ids that are fed."""
        return self._networks

    @property
    def known_clients(self) -> int:
        """Return how many clients have been handed to the dhcp integration."""
        return len(self._fed)

    @property
    def available(self) -> bool:
        """Return whether the UniFi controller is currently usable as a source."""
        return self._available

    async def async_start(self) -> None:
        """Run the first pass and keep feeding on UniFi changes and on a timer."""
        LOGGER.info(
            "Enriching DHCP discovery with UniFi clients from %d network(s)",
            len(self._networks),
        )
        self._resync_unsub = async_track_time_interval(
            self.hass,
            self._async_resync,
            RESYNC_INTERVAL,
            name="UniFi DHCP enrichment resync",
        )
        await self._async_resync()

    @callback
    def async_stop(self) -> None:
        """Stop feeding and remove the issues this entry raised."""
        super().async_stop()
        if self._resync_unsub:
            self._resync_unsub()
            self._resync_unsub = None
        self._unbind_hub()
        for issue in (ISSUE_UNIFI_UNAVAILABLE, ISSUE_NETWORKS_MISSING):
            ir.async_delete_issue(self.hass, self._entry.domain, self._issue_id(issue))

    def _issue_id(self, issue: str) -> str:
        """Return the per-entry issue id."""
        return f"{issue}_{self._entry.entry_id}"

    def _current_hub(self) -> UnifiHub | None:
        """Return the live hub of the linked UniFi entry, if it is loaded."""
        if self._unifi_entry.state is not ConfigEntryState.LOADED:
            return None
        return getattr(self._unifi_entry, "runtime_data", None)

    @callback
    def _unbind_hub(self) -> None:
        """Drop the subscriptions on the current hub."""
        for unsub in self._hub_unsubs:
            unsub()
        self._hub_unsubs.clear()
        self._hub = None

    @callback
    def _bind_hub(self, hub: UnifiHub) -> None:
        """Subscribe to client changes and reachability of a hub."""
        self._unbind_hub()
        self._hub = hub
        self._hub_unsubs = [
            hub.api.clients.subscribe(
                self._async_client_event, (ItemEvent.ADDED, ItemEvent.CHANGED)
            ),
            async_dispatcher_connect(
                self.hass, hub.signal_reachable, self._async_reachability_changed
            ),
        ]

    @callback
    def _async_reachability_changed(self) -> None:
        """Re-evaluate when the UniFi connection goes away or comes back."""
        self.hass.async_create_task(
            self._async_resync(), name="UniFi DHCP enrichment reachability"
        )

    @callback
    def _async_client_event(self, event: ItemEvent, mac: str) -> None:
        """Feed a single client that UniFi reported as added or changed."""
        if self._hub is None or (client := self._hub.api.clients.get(mac)) is None:
            return
        if self._async_feed_client(client, log_skips=False) == OUTCOME_FED:
            LOGGER.debug("UniFi client %s %s, handed over", mac, event.value)
            self.stats.events_fed += 1

    @callback
    def _async_feed_client(self, client: Client, *, log_skips: bool = True) -> str:
        """Hand one client to the dhcp integration if it is in a selected network.

        Returns what happened, so a pass can count it.
        """
        network_id = client.raw.get("network_id")
        if network_id not in self._networks:
            if log_skips:
                LOGGER.debug(
                    "Skipping %s: network %s is not selected", client.mac, network_id
                )
            return OUTCOME_OTHER_NETWORK
        if not (ip_address := _ipv4(client.ip)):
            if log_skips:
                LOGGER.debug("Skipping %s: no IPv4 address (%r)", client.mac, client.ip)
            return OUTCOME_NO_IPV4
        # Only the DHCP hostname is used. The alias (``name``) is free text and would
        # make hostname based matchers fire on arbitrary labels.
        hostname = client.hostname
        handed = self._fed.setdefault(client.mac, set())
        if (ip_address, hostname) in handed:
            return OUTCOME_UNCHANGED
        LOGGER.debug(
            "Feeding %s (ip=%s, hostname=%r) from network %s",
            client.mac,
            ip_address,
            hostname,
            network_id,
        )
        self.async_process_client(ip_address, hostname, client.mac)
        if len(handed) >= MAX_VALUES_PER_CLIENT:
            handed.clear()
        handed.add((ip_address, hostname))
        return OUTCOME_FED

    @callback
    def _async_set_available(self, available: bool, reason: str) -> None:
        """Log and raise a repair issue once per outage, not once per pass."""
        if available == self._available:
            return
        self._available = available
        issue_id = self._issue_id(ISSUE_UNIFI_UNAVAILABLE)
        if available:
            LOGGER.info("UniFi is available again, resuming DHCP enrichment")
            ir.async_delete_issue(self.hass, self._entry.domain, issue_id)
            return
        LOGGER.warning("UniFi is unavailable (%s), DHCP enrichment is paused", reason)
        ir.async_create_issue(
            self.hass,
            self._entry.domain,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_UNIFI_UNAVAILABLE,
            translation_placeholders={"title": self._unifi_entry.title},
        )

    async def _async_check_networks(self, hub: UnifiHub) -> None:
        """Warn once when a selected network no longer exists in UniFi."""
        try:
            await hub.api.networks.update()
        except (AiounifiException, TimeoutError) as err:
            LOGGER.debug("Could not refresh the UniFi networks: %r", err)
        known = {network.id for network in hub.api.networks.values()}
        if not known:
            return
        missing = frozenset(self._networks - known)
        if missing == self._missing_networks:
            return
        self._missing_networks = missing
        issue_id = self._issue_id(ISSUE_NETWORKS_MISSING)
        if not missing:
            LOGGER.info("All selected UniFi networks exist again")
            ir.async_delete_issue(self.hass, self._entry.domain, issue_id)
            return
        LOGGER.warning(
            "Selected UniFi network(s) no longer exist: %s", ", ".join(sorted(missing))
        )
        ir.async_create_issue(
            self.hass,
            self._entry.domain,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NETWORKS_MISSING,
            translation_placeholders={"networks": ", ".join(sorted(missing))},
        )

    async def _async_resync(self, _now: datetime | None = None) -> None:
        """Feed every client of the selected networks and re-check the link."""
        hub = self._current_hub()
        if hub is None:
            self._async_set_available(False, "the UniFi entry is not loaded")
            return
        if hub is not self._hub:
            LOGGER.debug("UniFi hub changed or first bind, (re)subscribing")
            self._bind_hub(hub)
        if not hub.available:
            self._async_set_available(False, "the controller is unreachable")
            return
        self._async_set_available(True, "")

        await self._async_check_networks(hub)

        clients = list(hub.api.clients.values())
        stats = FeedStats(clients=len(clients), last_resync=dt_util.utcnow())
        LOGGER.debug("Pass over %d UniFi client(s)", len(clients))
        for client in clients:
            try:
                outcome = self._async_feed_client(client)
            except Exception:  # noqa: BLE001 - one bad client must not stop the pass
                LOGGER.exception("Failed to feed UniFi client %s", client.mac)
                continue
            setattr(stats, outcome, getattr(stats, outcome) + 1)
        self.stats = stats
        LOGGER.debug(
            "Pass done: clients=%d fed=%d unchanged=%d skipped_other_network=%d "
            "skipped_no_ipv4=%d",
            stats.clients,
            stats.fed,
            stats.unchanged,
            stats.skipped_other_network,
            stats.skipped_no_ipv4,
        )
