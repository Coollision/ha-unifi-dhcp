"""Install the requirements of the Home Assistant integrations this one imports.

The pytest plugin ships Home Assistant itself but not the packages that individual
integrations (here ``dhcp`` and ``unifi``) install at runtime. They are read from the
installed Home Assistant's own manifests, so they always match the version under test.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from homeassistant import components

ROOTS = ("dhcp", "unifi")


def collect(domain: str, seen: set[str], requirements: set[str]) -> None:
    """Collect the requirements of a domain and of everything it depends on."""
    if domain in seen:
        return
    seen.add(domain)
    manifest_path = Path(components.__path__[0]) / domain / "manifest.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text())
    requirements.update(manifest.get("requirements", []))
    for dependency in (
        *manifest.get("dependencies", []),
        *manifest.get("after_dependencies", []),
    ):
        collect(dependency, seen, requirements)


def main() -> None:
    """Install the collected requirements."""
    requirements: set[str] = set()
    seen: set[str] = set()
    for domain in ROOTS:
        collect(domain, seen, requirements)
    print(f"Installing {len(requirements)} requirement(s): {sorted(requirements)}")  # noqa: T201
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", *sorted(requirements)],
        check=True,
    )


if __name__ == "__main__":
    main()
