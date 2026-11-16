# Bumble HA Proxy

An experimental Bluetooth Low Energy proxy for Home Assistant, powered by
[Bumble](https://github.com/google/bumble). It accesses a USB Bluetooth controller
through libusb and implements the Bluetooth subset of the ESPHome Native API.
Linux hosts without BlueZ or the kernel Bluetooth stack can use it.

Device protocols stay in Home Assistant integrations. Integrations using Home
Assistant's Bluetooth APIs can use the proxy without a custom relay client.

## Install

Requires Linux, Python 3.12 or newer, libusb, and exclusive access to a supported
USB Bluetooth controller. Download the wheel from [Releases](https://github.com/ammarlakis/bumble-ha-proxy/releases), then:

```sh
python -m venv .venv
. .venv/bin/activate
pip install ./bumble_ha_proxy-0.1.0-py3-none-any.whl
bumble-ha-proxy --transport usb:VID:PID
```

Replace `VID:PID` with the adapter's hexadecimal vendor/product IDs from `lsusb`.
The transport argument accepts [Bumble transport specifications](https://google.github.io/bumble/transports/index.html).
Release the adapter from other software before starting the proxy.

In Home Assistant, add an **ESPHome** integration using the proxy host and port
(default `6053`). Automatic discovery is not implemented.

The API has **no authentication or encryption** and binds to `127.0.0.1` by default.
For a separate Home Assistant host, use a protected tunnel or a trusted, isolated
network and explicitly set `--listen` to a reachable address. Do not expose the
port to the internet or an untrusted network.

## Container

Images are published for Linux amd64 and arm64:

```sh
docker run --rm --name bumble-ha-proxy \
  --device /dev/bus/usb/001/002 \
  -p 127.0.0.1:6053:6053 \
  ghcr.io/ammarlakis/bumble-ha-proxy:0.1.0 \
  --transport usb:VID:PID --listen 0.0.0.0
```

Replace the USB path and transport with your adapter's values. The example exposes
TCP only on the Docker host's loopback address; a remote Home Assistant instance
needs a protected network path. USB device numbers may change after reconnecting.
No host D-Bus socket is required.

Some adapters require firmware. For Realtek adapters, mount a firmware directory
and set `BUMBLE_RTK_FIRMWARE_DIR` to its path inside the container. Firmware is not
included; obtain firmware appropriate for your adapter and its license.

## Capabilities and limits

- BLE scanning with raw advertisements and address types.
- Multiple peripheral connections (`--slots`, default 3), GATT discovery,
  characteristic/descriptor reads and writes, notifications, and indications.
- Cached-service reconnects and connection cleanup on API disconnect.
- One Home Assistant API client at a time.
- No BLE pairing/bonding, mDNS, API encryption, or USB hotplug recovery yet.
- Requests run serially; a GATT operation can delay other requests up to 15 seconds.
- BLE GATT only; Bluetooth audio streaming is not supported.

An ASUS USB-BT500 has been tested with Quran Lamp and PETKIT fountain integrations.
Compatibility with other adapters and devices is not guaranteed. See
[VALIDATION.md](VALIDATION.md) for the validation scope.

## Development

Install [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/), then:

```sh
just check
just package
just image
```

Tests use an actual aioesphomeapi client over TCP with simulated Bumble radios;
no USB hardware is needed. `just package` builds a wheel and source archive in
`dist/`. Tagged releases run validation and publish those archives and the image.

## License

MIT. Uses Bumble (Apache-2.0) and aioesphomeapi (MIT), including its generated
ESPHome protocol messages. Independently developed; not affiliated with ESPHome,
Home Assistant, Google, or PETKIT.
