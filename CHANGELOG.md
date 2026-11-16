# 0.1.0

First experimental release of a generic Bumble-based Bluetooth proxy for Home
Assistant's ESPHome integration.

- Direct USB access through libusb, without BlueZ.
- Scanning, concurrent peripheral connections, GATT operations, and notifications.
- Simulated-radio tests using the real aioesphomeapi client.
- Python wheel/source archives and Linux amd64/arm64 container images.

The API is plaintext and unauthenticated. Use loopback or a protected network.
Pairing, mDNS, and USB hotplug recovery are not implemented.
