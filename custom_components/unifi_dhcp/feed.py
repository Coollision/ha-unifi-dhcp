"""Feed UniFi clients into Home Assistant's DHCP discovery."""

from __future__ import annotations

import asyncio
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
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CLIENT_COOLDOWN,
    CONF_NETWORKS,
    HANDOVERS_PER_SECOND,
    ISSUE_NETWORKS_MISSING,
    ISSUE_UNIFI_UNAVAILABLE,
    LOGGER,
    MAX_PENDING,
    PASS_YIELD_EVERY,
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
OUTCOME_QUEUED = "queued"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_OTHER_NETWORK = "skipped_other_network"
OUTCOME_NO_IPV4 = "skipped_no_ipv4"
OUTCOME_QUEUE_FULL = "skipped_queue_full"


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
    """What the last full pass decided, plus what live events decided since then."""

    clients: int = 0
    queued: int = 0
    unchanged: int = 0
    skipped_other_network: int = 0
    skipped_no_ipv4: int = 0
    skipped_queue_full: int = 0
    events_queued: int = 0
    last_resync: datetime | None = None


@dataclass(slots=True)
class Pending:
    """A client value waiting for its turn to be handed over."""

    ip_address: str
    hostname: str
    force: bool


class UnifiDhcpFeed(WatcherBase):
    """Hand UniFi clients of the selected networks to the dhcp integration.

    The dhcp integration only discovers hosts it can see on its own layer 2 segment.
    UniFi already knows every client on every network, so its client table is used as
    one more source for ``async_process_client``, which does the actual matching
    against the integration matchers and starts the discovery flows.

    Everything handed over goes through one queue with a token bucket, so the load on
    Home Assistant is bounded whatever the controller reports: at most
    ``HANDOVERS_PER_SECOND`` calls per second in total, one call per client per
    ``CLIENT_COOLDOWN``, and at most ``MAX_PENDING`` clients waiting.
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
        self._handed: dict[str, set[tuple[str, str]]] = {}
        self._last_handed: dict[str, datetime] = {}
        self._pending: dict[str, Pending] = {}
        self._drain_unsub: Callable[[], None] | None = None
        self._tokens = float(HANDOVERS_PER_SECOND)
        self._tokens_at = dt_util.utcnow()
        self._queue_full_logged = False
        self.handed_over = 0
        self.stats = FeedStats()

    @property
    def networks(self) -> frozenset[str]:
        """Return the UniFi network ids that are fed."""
        return self._networks

    @property
    def known_clients(self) -> int:
        """Return how many clients have been handed to the dhcp integration."""
        return len(self._handed)

    @property
    def queue_length(self) -> int:
        """Return how many clients are waiting for their turn."""
        return len(self._pending)

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
        if self._drain_unsub:
            self._drain_unsub()
            self._drain_unsub = None
        self._pending.clear()
        self._unbind_hub()
        for issue in (ISSUE_UNIFI_UNAVAILABLE, ISSUE_NETWORKS_MISSING):
            ir.async_delete_issue(self.hass, self._entry.domain, self._issue_id(issue))

    async def async_resync(self, *, force: bool = False) -> int:
        """Run a full pass now and return how many clients were queued.

        With ``force`` every client of the selected networks is handed over again,
        even if it was before and even if the dhcp integration has seen the same data.
        That makes integrations that were set up since pick up their devices, and
        shows in the logs how each client matches. The rate limits still apply.
        """
        return await self._async_pass(force=force)

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
        """Queue a single client that UniFi reported as added or changed."""
        if self._hub is None or (client := self._hub.api.clients.get(mac)) is None:
            return
        if self._async_queue_client(client, log_skips=False) == OUTCOME_QUEUED:
            LOGGER.debug("UniFi client %s %s, queued", mac, event.value)
            self.stats.events_queued += 1

    @callback
    def _async_queue_client(
        self, client: Client, *, force: bool = False, log_skips: bool = True
    ) -> str:
        """Queue one client for hand-over if it is in a selected network.

        Returns what was decided, so a pass can count it.
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
        if not force and (ip_address, hostname) in self._handed.get(client.mac, ()):
            return OUTCOME_UNCHANGED
        pending = self._pending.get(client.mac)
        if pending and (pending.ip_address, pending.hostname) == (ip_address, hostname):
            pending.force = pending.force or force
            return OUTCOME_UNCHANGED
        if pending is None and len(self._pending) >= MAX_PENDING:
            if not self._queue_full_logged:
                self._queue_full_logged = True
                LOGGER.warning(
                    "%d clients are waiting to be handed over, the rest is left for "
                    "the next pass",
                    MAX_PENDING,
                )
            return OUTCOME_QUEUE_FULL
        self._pending[client.mac] = Pending(ip_address, hostname, force)
        self._async_start_draining()
        return OUTCOME_QUEUED

    @callback
    def _async_start_draining(self) -> None:
        """Hand over what the budget allows now, unless a timer already will."""
        if self._drain_unsub is None:
            self._async_drain()

    @callback
    def _async_drain(self, _now: datetime | None = None) -> None:
        """Hand queued clients to the dhcp integration within the rate limits."""
        self._drain_unsub = None
        now = dt_util.utcnow()
        elapsed = (now - self._tokens_at).total_seconds()
        self._tokens = min(
            float(HANDOVERS_PER_SECOND), self._tokens + elapsed * HANDOVERS_PER_SECOND
        )
        self._tokens_at = now

        for mac in list(self._pending):
            if self._tokens < 1:
                break
            last = self._last_handed.get(mac)
            if last is not None and now - last < CLIENT_COOLDOWN:
                continue
            item = self._pending.pop(mac)
            self._tokens -= 1
            LOGGER.debug(
                "Handing over %s (ip=%s, hostname=%r)%s",
                mac,
                item.ip_address,
                item.hostname,
                " [forced]" if item.force else "",
            )
            try:
                self.async_process_client(
                    item.ip_address, item.hostname, mac, force=item.force
                )
            except Exception:  # noqa: BLE001 - one bad client must not stop the rest
                LOGGER.exception("Failed to hand over UniFi client %s", mac)
                continue
            handed = self._handed.setdefault(mac, set())
            if len(handed) >= MAX_VALUES_PER_CLIENT:
                handed.clear()
            handed.add((item.ip_address, item.hostname))
            self._last_handed[mac] = now
            self.handed_over += 1

        if self._pending:
            # Rest of the queue: out of budget, or waiting for a client's cooldown.
            self._drain_unsub = async_call_later(self.hass, 1, self._async_drain)
        else:
            self._queue_full_logged = False

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
        """Periodic or reachability driven pass."""
        await self._async_pass()

    async def _async_pass(self, *, force: bool = False) -> int:
        """Queue every client of the selected networks and re-check the link."""
        hub = self._current_hub()
        if hub is None:
            self._async_set_available(False, "the UniFi entry is not loaded")
            return 0
        if hub is not self._hub:
            LOGGER.debug("UniFi hub changed or first bind, (re)subscribing")
            self._bind_hub(hub)
        if not hub.available:
            self._async_set_available(False, "the controller is unreachable")
            return 0
        self._async_set_available(True, "")

        await self._async_check_networks(hub)

        clients = list(hub.api.clients.values())
        stats = FeedStats(clients=len(clients), last_resync=dt_util.utcnow())
        LOGGER.debug(
            "Pass over %d UniFi client(s)%s", len(clients), " [forced]" if force else ""
        )
        for index, client in enumerate(clients, start=1):
            try:
                outcome = self._async_queue_client(client, force=force)
            except Exception:  # noqa: BLE001 - one bad client must not stop the pass
                LOGGER.exception("Failed to queue UniFi client %s", client.mac)
                continue
            setattr(stats, outcome, getattr(stats, outcome) + 1)
            if index % PASS_YIELD_EVERY == 0:
                await asyncio.sleep(0)
        self.stats = stats
        LOGGER.debug(
            "Pass done: clients=%d queued=%d unchanged=%d skipped_other_network=%d "
            "skipped_no_ipv4=%d skipped_queue_full=%d pending=%d",
            stats.clients,
            stats.queued,
            stats.unchanged,
            stats.skipped_other_network,
            stats.skipped_no_ipv4,
            stats.skipped_queue_full,
            len(self._pending),
        )
        return stats.queued
