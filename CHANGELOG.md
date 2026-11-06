# Changelog

## [0.3.0](https://github.com/Coollision/ha-unifi-dhcp/compare/v0.2.0...v0.3.0) (2026-11-06)


### Features

* add Dutch translation, field descriptions and a translation completeness test ([c50bd86](https://github.com/Coollision/ha-unifi-dhcp/commit/c50bd86a0ff748d99f245bca8d109aea2b4a4599))
* rate limit hand-overs and add a resync service ([8ea5f2f](https://github.com/Coollision/ha-unifi-dhcp/commit/8ea5f2fab483b3b3123c2175c8baa55f5b942528))


### Bug Fixes

* hand over IPv4 only and each address/hostname pair once ([a999e9f](https://github.com/Coollision/ha-unifi-dhcp/commit/a999e9f9cb7b811dc66d4fffc986bc21d4f7fb54))
* only hand over new or changed clients and count per pass ([2938294](https://github.com/Coollision/ha-unifi-dhcp/commit/293829441308c43f4e6fef773dca31c3fb924126))
* pick the UniFi entry from a dropdown instead of the config_entry selector ([74708ff](https://github.com/Coollision/ha-unifi-dhcp/commit/74708ff28e322ea355fc321b1b4d1ad24ee7d97b))
* skip the UniFi picker for a single entry and say what is being chosen ([c61b987](https://github.com/Coollision/ha-unifi-dhcp/commit/c61b987c744b1297815490b19f25b7c15b96b937))

## [0.2.0](https://github.com/Coollision/ha-unifi-dhcp/compare/v0.1.0...v0.2.0) (2026-11-06)


### Features

* feed UniFi clients into Home Assistant DHCP discovery ([6b4ab59](https://github.com/Coollision/ha-unifi-dhcp/commit/6b4ab59f980409736b1323709700e32a6265497e))


### Bug Fixes

* add brand icons and manifest category for HACS validation ([4b55e2e](https://github.com/Coollision/ha-unifi-dhcp/commit/4b55e2e95317f0a5639a9e571c0056bdb2d8d9a2))
* use probatio for flow schemas on Home Assistant 2026.11 ([4aaac8e](https://github.com/Coollision/ha-unifi-dhcp/commit/4aaac8ed1bcaae0b4e90e990287704341a518e14))
