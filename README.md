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

- On setup, and every 15 minutes, every UniFi client in a selected network that has an IP address is handed to the `dhcp` integration.
- Changes are fed live: a client that connects is checked as soon as UniFi reports it.
- Only the **DHCP hostname** is used. The alias you give a client in UniFi is free text and would make hostname based matchers fire on labels.
- The `dhcp` integration ignores data it has already processed, so repeating this is cheap and does not create duplicate discoveries.

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
