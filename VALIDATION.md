# Validation

## Automated coverage

The test suite connects the real aioesphomeapi client over TCP to three simulated
Bumble controllers. It covers API metadata and entity listing, raw advertisements,
two concurrent peripheral connections, service discovery, characteristic reads and
acknowledged writes, notifications, peripheral-initiated disconnects,
cached-service reconnection, invalid-handle errors, and connection cleanup.

CI runs lint, tests, and Python package builds. Releases also build container
images for Linux amd64 and arm64. Simulated-radio tests do not establish physical
adapter compatibility.

## Hardware coverage

An ASUS USB-BT500 has been used with Home Assistant's ESPHome integration on Linux
without BlueZ or a host D-Bus socket. Observed operations include:

- Quran Lamp: lighting, recitation play/pause, and status notifications.
- PETKIT fountain: authenticated status reads while the lamp remained connected.
- Scanner and device recovery after proxy and Home Assistant restarts.

These checks establish a limited compatibility baseline, not certification of
all ESPHome Bluetooth functionality. USB removal/reinsertion recovery, pairing,
and encrypted API connections are not implemented. Descriptor operations and
indications are implemented but do not yet have dedicated automated tests.
