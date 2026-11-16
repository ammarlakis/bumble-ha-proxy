import asyncio
from contextlib import suppress

import pytest
from aioesphomeapi import APIClient
from bumble.controller import Controller
from bumble.device import Device
from bumble.gatt import Characteristic, Service
from bumble.hci import Address
from bumble.link import LocalLink

from bumble_ha_proxy.server import Proxy, address_number


@pytest.mark.asyncio
async def test_real_client_over_simulated_radio():
    link = LocalLink()
    controllers = [
        Controller(str(i), link=link, public_address=f"F0:F1:F2:F3:F4:0{i}")
        for i in range(3)
    ]
    radios = [
        Device.with_hci(str(i), Address(f"F2:00:00:00:00:0{i}"), c, c)
        for i, c in enumerate(controllers)
    ]
    for radio in radios:
        await radio.power_on()
    value = Characteristic(
        "AE01",
        Characteristic.Properties.READ
        | Characteristic.Properties.WRITE
        | Characteristic.Properties.NOTIFY,
        Characteristic.Permissions.READABLE | Characteristic.Permissions.WRITEABLE,
        b"initial",
    )
    radios[1].add_service(Service("AE00", [value]))
    await radios[1].start_advertising()
    await radios[2].start_advertising()
    proxy = Proxy(radios[0], slots=2)
    server = await asyncio.start_server(proxy.accept, "127.0.0.1", 0)
    client = APIClient("127.0.0.1", server.sockets[0].getsockname()[1], "")
    try:
        await client.connect(login=True)
        info = await client.device_info()
        assert info.bluetooth_proxy_feature_flags == 39
        assert len(info.project_name.split(".")) == 2
        assert await client.list_entities_services() == ([], [])
        assert (await asyncio.wait_for(client.device_info_and_list_entities(), 3))[
            0
        ].name == "bumble-proxy"
        seen = asyncio.Event()
        client.subscribe_bluetooth_le_raw_advertisements(lambda _: seen.set())
        await asyncio.wait_for(seen.wait(), 5)
        address = address_number(radios[1].random_address)
        disconnected = asyncio.Event()
        await client.bluetooth_device_connect(
            address,
            lambda connected, mtu, error: disconnected.set() if not connected else None,
            address_type=1,
            feature_flags=39,
        )
        second_address = address_number(radios[2].random_address)
        await client.bluetooth_device_connect(
            second_address, lambda *args: None, address_type=1, feature_flags=39
        )
        second_services = await client.bluetooth_gatt_get_services(second_address)
        assert second_services.services
        services = await client.bluetooth_gatt_get_services(address)
        characteristic = next(
            c
            for s in services.services
            for c in s.characteristics
            if c.uuid == "0000ae01-0000-1000-8000-00805f9b34fb"
        )
        handle = characteristic.handle
        assert await client.bluetooth_gatt_read(address, handle) == b"initial"
        await client.bluetooth_gatt_write(address, handle, b"changed", True)
        assert await client.bluetooth_gatt_read(address, handle) == b"changed"
        notified = asyncio.Event()
        received = []
        stop, _ = await client.bluetooth_gatt_start_notify(
            address, handle, lambda _, data: (received.append(data), notified.set())
        )
        await radios[1].notify_subscribers(value, b"notification")
        await asyncio.wait_for(notified.wait(), 3)
        assert received == [b"notification"]
        await stop()
        # The peripheral drops the link independently of Home Assistant.
        await next(iter(radios[1].connections.values())).disconnect()
        await asyncio.wait_for(disconnected.wait(), 3)
        await radios[1].start_advertising()
        await client.bluetooth_device_connect(
            address,
            lambda *args: None,
            address_type=1,
            feature_flags=39,
            has_cache=True,
        )
        notified.clear()
        stop, _ = await client.bluetooth_gatt_start_notify(
            address, handle, lambda _, data: notified.set()
        )
        await radios[1].notify_subscribers(value, b"after reconnect")
        await asyncio.wait_for(notified.wait(), 3)
        await stop()
        await client.bluetooth_device_disconnect(second_address)
        # A missing attribute returns a GATT error without dropping the API.
        with pytest.raises(Exception, match="Invalid handle"):
            await client.bluetooth_gatt_read(address, 65535, timeout=2)
        assert (await client.device_info()).name == "bumble-proxy"
        await client.bluetooth_device_disconnect(address)
    finally:
        await client.disconnect()
        await asyncio.sleep(0.05)
        server.close()
        await server.wait_closed()
        for radio in radios:
            with suppress(Exception):
                await radio.power_off()
