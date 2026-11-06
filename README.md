# UniFi DHCP enrichment

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Validate](https://github.com/Coollision/ha-unifi-dhcp/actions/workflows/validate.yml/badge.svg)](https://github.com/Coollision/ha-unifi-dhcp/actions/workflows/validate.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

Makes Home Assistant's built-in **DHCP discovery** see devices on networks it is not connected to, by feeding it the clients your **UniFi Network** integration already knows.

> **Unofficial.** Not affiliated with or endorsed by Ubiquiti, Home Assistant, Nabu Casa or the Open Home Foundation. See [NOTICE](NOTICE).

## Why

DHCP discovery only finds devices it can see itself: broadcasts on, and hosts reachable on, the network Home Assistant is attached to. Anything on another VLAN never shows up as a discovered device, even though your UniFi controller knows every client with its IP, MAC and hostname.

This integration hands those clients to the same matching code the built-in `dhcp` integration uses. Integrations that declare a DHCP matcher then offer themselves for devices on the networks you choose, without giving Home Assistant a leg on every VLAN.

It **enriches** the built-in discovery. It does not replace it, does not create any entities, and needs **no extra credentials**: it reuses the connection of the UniFi Network integration.

## Requirements

| | |
|---|---|
| Home Assistant | 2026.10 or newer |
| Integrations | The built-in **UniFi Network** integration, set up and loaded |

## Installation

1. Add this repository to HACS as a **custom repository** of type **Integration** (`https://github.com/Coollision/ha-unifi-dhcp`), download it and restart Home Assistant.
2. **Settings → Devices & services → Add integration → UniFi DHCP enrichment**.

## Setup

1. Pick the UniFi Network integration to read clients from.
2. Pick the **networks** whose clients should be fed to DHCP discovery. **Nothing is selected by default**, and at least one is required. Networks are listed by name and VLAN id. If the controller does not hand out its network list, the networks the known clients are attached to are offered instead.

Change the selection later under **Configure** on the integration. The integration reloads by itself.

## What it does

- On setup, and every 15 minutes, every UniFi client in a selected network that has an IPv4 address is queued for the `dhcp` integration (see [Safety limits](#safety-limits)).
- Changes are fed live: a client that connects is checked as soon as UniFi reports it.
- Only the **DHCP hostname** is used. The alias you give a client in UniFi is free text and would make hostname based matchers fire on labels.
- Only **IPv4** addresses are handed over (this is DHCP discovery). UniFi reports one address per client and it can alternate between a client's IPv4 and IPv6 address.
- Each IP and hostname combination is passed on once per client. UniFi reports every client as changed every few seconds, and some clients alternate between two hostnames, so repeats are ignored.
- The `dhcp` integration also ignores data it has already processed, so repeating this does not create duplicate discoveries.

## Safety limits

Home Assistant's own sweep does one burst per hour. This integration is built so it can never flood Home Assistant, however many clients a controller reports. Everything goes through one queue:

| Limit | Value |
|---|---|
| Hand-overs to DHCP discovery, over all clients | at most **20 per second** (a token bucket; thousands of clients are spread over minutes) |
| Hand-overs of the same client | at most **one per minute**, however often UniFi reports a change |
| Clients waiting in the queue | at most **5000**; the rest is left for the next pass and one warning is logged |
| Each IP and hostname combination | handed over **once** per client |
| A pass over a big client table | gives the event loop back every 250 clients |

The limits are constants in `const.py`. Diagnostics show the queue length and how many clients were handed over, queued or skipped.

## Try it with your own devices

Only clients that match a DHCP matcher of some integration (by hostname or by the vendor part of the MAC address) can start a discovery, so most devices, such as phones and laptops, never will. To see it work:

1. Pick a device on a selected network that you have **not** set up in Home Assistant and whose integration offers DHCP discovery.
2. Turn on debug logging for **both** this integration and **DHCP discovery** (the `dhcp` integration).
3. Call the service **UniFi DHCP enrichment: Resync clients** (`unifi_dhcp.resync`) with **Force** on.
4. In the log, `custom_components.unifi_dhcp` shows `Handing over <mac> ... [forced]`, and `homeassistant.components.dhcp` shows `Processing updated address data` and `Matched ...` for that device. A new card then appears under **Settings → Devices & services → Discovered**.

The service also helps after you set up a new integration: DHCP discovery ignores data it has already seen, so a forced resync hands every client over again.

## Logging

Out of the box this integration is quiet. Following Home Assistant's conventions:

| Level | What you see |
|---|---|
| `warning` | UniFi became unavailable (once per outage); selected networks that no longer exist; a client that failed to process |
| `info` | Setup summary; UniFi available again; selected networks exist again |
| `debug` | Every pass and every client: what is fed, and why a client is skipped |

To read the details, use **Settings → Devices & services → UniFi DHCP enrichment → ⋮ → Enable debug logging**, reproduce the problem, then disable it and download the log. Or in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.unifi_dhcp: debug
```

> **Privacy.** Debug logs contain MAC addresses, IP addresses and hostnames of your devices. Look them over before you share them.

## Problems and repairs

- **UniFi unavailable.** If the UniFi integration is not loaded or cannot reach the controller, a repair issue is raised and a warning is logged once. Enrichment resumes by itself and the issue clears.
- **Selected networks no longer exist.** If a network was deleted or recreated in UniFi, a repair issue lists it. Open **Configure** and update the selection.
- **Download diagnostics** shows the selected networks and the counters of the last pass (fed, skipped). It never contains client data.

## Limits

- Only clients UniFi currently reports are seen. A device that is offline is discovered once it connects.
- Devices that UniFi does not manage as clients are not visible to it, so they cannot be fed.
- It builds on the internals of the `dhcp` and `unifi` integrations (`WatcherBase`, the UniFi hub). A Home Assistant release can change them; the weekly CI run against the latest Home Assistant is there to notice that early.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
python scripts/install_ha_requirements.py   # packages that dhcp and unifi install at runtime
pytest --cov=custom_components.unifi_dhcp
ruff check . && ruff format --check .
```

Commits follow [Conventional Commits](https://www.conventionalcommits.org). Merging the release PR that release-please keeps up to date tags a release; HACS installs from that tag. Dependencies of the workflows are kept current by Renovate.
