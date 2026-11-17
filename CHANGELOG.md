# 0.1.1

- Keep the Home Assistant API connection and other peripherals connected when a device drops during a GATT request.
- Report unexpected disconnect reasons to Home Assistant and log the controller reason code.
- Scan for 30 ms every 320 ms and request a four-second link supervision timeout to give connected peripherals more radio time.
- Cover a disconnect during an outstanding read in the two-peripheral regression test.

# 0.1.0

First experimental release of a generic Bumble-based Bluetooth proxy for Home
Assistant's ESPHome integration.

- Direct USB access through libusb, without BlueZ.
- Scanning, concurrent peripheral connections, GATT operations, and notifications.
- Simulated-radio tests using the real aioesphomeapi client.
- Python wheel/source archives and Linux amd64/arm64 container images.

The API is plaintext and unauthenticated. Use loopback or a protected network.
Pairing, mDNS, and USB hotplug recovery are not implemented.
