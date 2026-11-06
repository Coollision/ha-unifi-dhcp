"""Config flow for the UniFi DHCP enrichment integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.unifi import DOMAIN as UNIFI_DOMAIN
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    ConfigEntrySelector,
    ConfigEntrySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
import voluptuous as vol

from .const import CONF_NETWORKS, CONF_UNIFI_ENTRY_ID, DOMAIN, LOGGER

if TYPE_CHECKING:
    from homeassistant.components.unifi import UnifiConfigEntry


async def _async_network_options(
    unifi_entry: UnifiConfigEntry,
) -> list[SelectOptionDict]:
    """List the networks of a UniFi site as select options.

    The networks are asked from the controller. If that fails, they are derived from
    the networks the known clients are attached to, so a flow can still be completed
    with a controller that does not hand out its network configuration.
    """
    from aiounifi import AiounifiException  # noqa: PLC0415

    api = unifi_entry.runtime_data.api
    try:
        await api.networks.update()
    except (AiounifiException, TimeoutError) as err:
        LOGGER.debug("Could not list the UniFi networks, using clients: %r", err)

    labels: dict[str, str] = {}
    for network in api.networks.values():
        if network.is_wan or not network.id:
            continue
        vlan = network.raw.get("vlan")
        label = network.name or network.id
        labels[network.id] = f"{label} (VLAN {vlan})" if vlan else label
    if not labels:
        for client in api.clients.values():
            if network_id := client.raw.get("network_id"):
                labels.setdefault(network_id, client.raw.get("network") or network_id)
    return [
        SelectOptionDict(value=network_id, label=label)
        for network_id, label in sorted(labels.items(), key=lambda item: item[1])
    ]


def _networks_schema(options: list[SelectOptionDict]) -> vol.Schema:
    """Return the multi select. Nothing is pre-selected."""
    return vol.Schema(
        {
            vol.Required(CONF_NETWORKS): SelectSelector(
                SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            )
        }
    )


class UnifiDhcpConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._unifi_entry_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> UnifiDhcpOptionsFlow:
        """Return the options flow."""
        return UnifiDhcpOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the UniFi entry to read clients from."""
        loaded = [
            entry
            for entry in self.hass.config_entries.async_entries(UNIFI_DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            return self.async_abort(reason="no_unifi_entry")

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_UNIFI_ENTRY_ID])
            self._abort_if_unique_id_configured()
            self._unifi_entry_id = user_input[CONF_UNIFI_ENTRY_ID]
            return await self.async_step_networks()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_UNIFI_ENTRY_ID): ConfigEntrySelector(
                        ConfigEntrySelectorConfig(integration=UNIFI_DOMAIN)
                    )
                }
            ),
        )

    async def async_step_networks(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the networks whose clients are fed to DHCP discovery."""
        assert self._unifi_entry_id is not None
        unifi_entry = self.hass.config_entries.async_get_entry(self._unifi_entry_id)
        if unifi_entry is None or unifi_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="no_unifi_entry")

        errors: dict[str, str] = {}
        options = await _async_network_options(unifi_entry)
        if not options:
            return self.async_abort(reason="no_networks")

        if user_input is not None:
            if user_input[CONF_NETWORKS]:
                return self.async_create_entry(
                    title=unifi_entry.title,
                    data={CONF_UNIFI_ENTRY_ID: self._unifi_entry_id},
                    options={CONF_NETWORKS: user_input[CONF_NETWORKS]},
                )
            errors["base"] = "no_networks_selected"

        return self.async_show_form(
            step_id="networks",
            data_schema=_networks_schema(options),
            errors=errors,
        )


class UnifiDhcpOptionsFlow(OptionsFlowWithReload):
    """Change the selected networks. The entry reloads when they change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the selected networks."""
        unifi_entry = self.hass.config_entries.async_get_entry(
            self.config_entry.data[CONF_UNIFI_ENTRY_ID]
        )
        if unifi_entry is None or unifi_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="no_unifi_entry")

        errors: dict[str, str] = {}
        options = await _async_network_options(unifi_entry)

        if user_input is not None:
            if user_input[CONF_NETWORKS]:
                return self.async_create_entry(data=user_input)
            errors["base"] = "no_networks_selected"

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                _networks_schema(options), self.config_entry.options
            ),
            errors=errors,
        )
