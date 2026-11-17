"""ESPHome Native API server backed by Bumble's userspace Bluetooth stack."""

import argparse
import asyncio
import logging
from contextlib import contextmanager, suppress

from aioesphomeapi import api_pb2 as pb
from aioesphomeapi.core import MESSAGE_TYPE_TO_PROTO
from bumble.core import BaseBumbleError
from bumble.device import ConnectionParametersPreferences, Device, Peer
from bumble.hci import HCI_LE_1M_PHY, Address
from bumble.transport import open_transport

LOG = logging.getLogger(__name__)
PROTO_TO_TYPE = {cls: number for number, cls in MESSAGE_TYPE_TO_PROTO.items()}
MAX_FRAME = 65536


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return result


async def read_varint(reader):
    value = 0
    for shift in range(0, 35, 7):
        byte = (await reader.readexactly(1))[0]
        value |= (byte & 127) << shift
        if not byte & 128:
            return value
    raise ValueError("Invalid frame varint")


def address_number(address):
    return int(str(address).split("/")[0].replace(":", ""), 16)


def uuid_parts(uuid):
    value = int.from_bytes(uuid.to_bytes(force_128=True), "little")
    return [value >> 64, value & ((1 << 64) - 1)]


@contextmanager
def handle_link_loss():
    # Bumble cancels the pending ATT future when that peripheral disconnects.
    # Let its disconnect callback report the loss without ending the HA session.
    try:
        yield
    except asyncio.CancelledError:
        if asyncio.current_task().cancelling():
            raise


class Proxy:
    def __init__(self, radio, name="bumble-proxy", slots=3):
        self.radio = radio
        self.name = name
        self.slots = slots
        self.client = None
        self.radio_lock = asyncio.Lock()

    async def start_scanning(self):
        # Leave airtime for connected peripherals sharing the USB controller.
        await self.radio.start_scanning(scan_interval=320, scan_window=30)

    async def accept(self, reader, writer):
        # One HA API owner avoids conflicting writes and connection ownership.
        if self.client is not None:
            writer.close()
            await writer.wait_closed()
            return
        client = Session(self, reader, writer)
        self.client = client
        try:
            await client.run()
        finally:
            await client.close()
            self.client = None


class Session:
    def __init__(self, proxy, reader, writer):
        self.proxy = proxy
        self.reader = reader
        self.writer = writer
        self.peers = {}
        self.advertisements = False
        self.slot_updates = False
        self.ready = False
        self.closing = False

    def send(self, message):
        if self.writer.is_closing():
            return
        payload = message.SerializeToString()
        self.writer.write(
            b"\0"
            + varint(len(payload))
            + varint(PROTO_TO_TYPE[type(message)])
            + payload
        )
        # Notifications must not grow a queue without bounds for a slow client.
        if self.writer.transport.get_write_buffer_size() > 1024 * 1024:
            self.writer.close()

    def slots(self):
        if self.slot_updates:
            self.send(
                pb.BluetoothConnectionsFreeResponse(
                    free=self.proxy.slots - len(self.peers),
                    limit=self.proxy.slots,
                    allocated=list(self.peers),
                )
            )

    def advertisement(self, adv):
        if self.advertisements:
            self.send(
                pb.BluetoothLERawAdvertisementsResponse(
                    advertisements=[
                        pb.BluetoothLERawAdvertisement(
                            address=address_number(adv.address),
                            rssi=adv.rssi,
                            address_type=int(adv.address.address_type),
                            data=adv.data_bytes,
                        )
                    ]
                )
            )

    async def run(self):
        self.proxy.radio.on("advertisement", self.advertisement)
        try:
            while not self.writer.is_closing():
                async with asyncio.timeout(90):
                    if await self.reader.readexactly(1) != b"\0":
                        raise ValueError("Only plaintext ESPHome framing is supported")
                    size = await read_varint(self.reader)
                    kind = await read_varint(self.reader)
                    if size > MAX_FRAME:
                        raise ValueError("Frame exceeds maximum size")
                    payload = await self.reader.readexactly(size)
                cls = MESSAGE_TYPE_TO_PROTO.get(kind)
                if cls is None:
                    continue
                message = cls.FromString(payload)
                if not self.ready and not isinstance(message, pb.HelloRequest):
                    raise ValueError("Hello must precede API requests")
                await self.handle(message)
                await self.writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ValueError):
            LOG.debug("API client disconnected", exc_info=True)

    async def close(self):
        self.closing = True
        self.proxy.radio.remove_listener("advertisement", self.advertisement)
        if self.proxy.radio.is_scanning:
            with suppress(BaseBumbleError, TimeoutError):
                await asyncio.wait_for(self.proxy.radio.stop_scanning(), 5)
        for peer in list(self.peers.values()):
            with suppress(BaseBumbleError, TimeoutError):
                await asyncio.wait_for(peer.connection.disconnect(), 5)
        self.peers.clear()
        self.writer.close()
        with suppress(ConnectionError):
            await self.writer.wait_closed()

    async def handle(self, msg):
        if isinstance(msg, pb.HelloRequest):
            self.ready = True
            self.send(
                pb.HelloResponse(
                    api_version_major=1,
                    api_version_minor=9,
                    name=self.proxy.name,
                    server_info="Bumble HA Proxy 0.1.1",
                )
            )
        elif isinstance(msg, pb.AuthenticationRequest):
            self.send(pb.AuthenticationResponse())
        elif isinstance(msg, pb.PingRequest):
            self.send(pb.PingResponse())
        elif isinstance(msg, pb.DisconnectRequest):
            self.send(pb.DisconnectResponse())
            await self.writer.drain()
            self.writer.close()
        elif isinstance(msg, pb.DeviceInfoRequest):
            mac = str(self.proxy.radio.public_address).split("/")[0]
            self.send(
                pb.DeviceInfoResponse(
                    name=self.proxy.name,
                    friendly_name=self.proxy.name,
                    mac_address=mac,
                    bluetooth_mac_address=mac,
                    model="Bumble USB BLE proxy",
                    manufacturer="Bumble HA Proxy",
                    esphome_version="2025.12.0",
                    project_name="bumble.ha-proxy",
                    project_version="0.1.1",
                    bluetooth_proxy_feature_flags=1 | 2 | 4 | 32,
                )
            )
        elif isinstance(msg, pb.ListEntitiesRequest):
            self.send(pb.ListEntitiesDoneResponse())
        elif isinstance(msg, pb.SubscribeBluetoothConnectionsFreeRequest):
            self.slot_updates = True
            self.slots()
        elif isinstance(msg, pb.SubscribeBluetoothLEAdvertisementsRequest):
            self.advertisements = True
            if not self.proxy.radio.is_scanning:
                await self.proxy.start_scanning()
        elif isinstance(msg, pb.UnsubscribeBluetoothLEAdvertisementsRequest):
            self.advertisements = False
            if self.proxy.radio.is_scanning:
                await self.proxy.radio.stop_scanning()
        elif isinstance(msg, pb.BluetoothDeviceRequest):
            with handle_link_loss():
                await self.device_request(msg)
        elif type(msg).__name__.startswith("BluetoothGATT"):
            with handle_link_loss():
                await self.gatt_request(msg)

    def disconnected(self, address, reason):
        if reason != 0x16:  # Locally requested disconnects are routine after polls.
            LOG.info("BLE disconnect %012X, HCI reason %#04x", address, reason)
        self.peers.pop(address, None)
        if not self.closing:
            self.send(
                pb.BluetoothDeviceConnectionResponse(
                    address=address,
                    connected=False,
                    error=0 if reason == 0x16 else reason,
                )
            )
            self.slots()

    async def device_request(self, msg):
        address = msg.address
        if msg.request_type == pb.BLUETOOTH_DEVICE_REQUEST_TYPE_DISCONNECT:
            if address in self.peers:
                await self.peers[address].connection.disconnect()
            else:
                self.send(
                    pb.BluetoothDeviceConnectionResponse(
                        address=address, connected=False
                    )
                )
            return
        connect_types = (
            pb.BLUETOOTH_DEVICE_REQUEST_TYPE_CONNECT,
            pb.BLUETOOTH_DEVICE_REQUEST_TYPE_CONNECT_V3_WITH_CACHE,
            pb.BLUETOOTH_DEVICE_REQUEST_TYPE_CONNECT_V3_WITHOUT_CACHE,
        )
        if msg.request_type not in connect_types:
            self.send(pb.BluetoothDeviceConnectionResponse(address=address, error=6))
            return
        if address in self.peers:
            peer = self.peers[address]
            self.send(
                pb.BluetoothDeviceConnectionResponse(
                    address=address, connected=True, mtu=peer.connection.att_mtu
                )
            )
            return
        if len(self.peers) >= self.proxy.slots:
            self.send(pb.BluetoothDeviceConnectionResponse(address=address, error=17))
            return
        connection = None
        try:
            async with self.proxy.radio_lock, asyncio.timeout(25):
                if self.proxy.radio.is_scanning:
                    await self.proxy.radio.stop_scanning()
                text = ":".join(f"{address:012X}"[i : i + 2] for i in range(0, 12, 2))
                target = Address(text, msg.address_type)
                for attempt in range(2):
                    try:
                        connection = await self.proxy.radio.connect(
                            target,
                            connection_parameters_preferences={
                                HCI_LE_1M_PHY: ConnectionParametersPreferences(
                                    supervision_timeout=4000
                                )
                            },
                            timeout=8,
                        )
                        break
                    except (BaseBumbleError, TimeoutError):
                        if attempt:
                            raise
                        await asyncio.sleep(0.4)
                peer = Peer(connection)
                connection.on(
                    "disconnection", lambda reason: self.disconnected(address, reason)
                )
                await peer.request_mtu(247)
                self.peers[address] = peer
                self.send(
                    pb.BluetoothDeviceConnectionResponse(
                        address=address, connected=True, mtu=connection.att_mtu
                    )
                )
                self.slots()
        except (BaseBumbleError, TimeoutError):
            if connection is not None:
                with suppress(BaseBumbleError, TimeoutError):
                    await asyncio.wait_for(connection.disconnect(), 5)
            self.send(pb.BluetoothDeviceConnectionResponse(address=address, error=133))
        finally:
            if self.advertisements and not self.proxy.radio.is_scanning:
                await self.proxy.start_scanning()

    async def gatt_request(self, msg):
        peer = self.peers.get(msg.address)
        if peer is None:
            self.send(
                pb.BluetoothGATTErrorResponse(
                    address=msg.address, handle=getattr(msg, "handle", 0), error=-1
                )
            )
            return
        try:
            async with asyncio.timeout(15):
                await self.gatt(peer, msg)
        except (BaseBumbleError, TimeoutError, KeyError) as err:
            self.send(
                pb.BluetoothGATTErrorResponse(
                    address=msg.address,
                    handle=getattr(msg, "handle", 0),
                    error=getattr(err, "error_code", 133),
                )
            )

    async def gatt(self, peer, msg):
        if isinstance(msg, pb.BluetoothGATTGetServicesRequest):
            await peer.discover_all()
            for service in peer.services:
                item = pb.BluetoothGATTService(
                    uuid=uuid_parts(service.uuid), handle=service.handle
                )
                for characteristic in service.characteristics:
                    char = item.characteristics.add(
                        uuid=uuid_parts(characteristic.uuid),
                        handle=characteristic.handle,
                        properties=int(characteristic.properties),
                    )
                    for descriptor in characteristic.descriptors:
                        char.descriptors.add(
                            uuid=uuid_parts(descriptor.type), handle=descriptor.handle
                        )
                self.send(
                    pb.BluetoothGATTGetServicesResponse(
                        address=msg.address, services=[item]
                    )
                )
            self.send(pb.BluetoothGATTGetServicesDoneResponse(address=msg.address))
        elif isinstance(
            msg, (pb.BluetoothGATTReadRequest, pb.BluetoothGATTReadDescriptorRequest)
        ):
            data = await peer.read_value(msg.handle)
            self.send(
                pb.BluetoothGATTReadResponse(
                    address=msg.address, handle=msg.handle, data=data
                )
            )
        elif isinstance(
            msg, (pb.BluetoothGATTWriteRequest, pb.BluetoothGATTWriteDescriptorRequest)
        ):
            response = getattr(msg, "response", True)
            await peer.write_value(msg.handle, msg.data, with_response=response)
            if response:
                self.send(
                    pb.BluetoothGATTWriteResponse(
                        address=msg.address, handle=msg.handle
                    )
                )
        elif isinstance(msg, pb.BluetoothGATTNotifyRequest):
            # HA can reuse its service cache across a new BLE connection.
            if not peer.services:
                await peer.discover_all()
            characteristic = next(
                (
                    c
                    for s in peer.services
                    for c in s.characteristics
                    if c.handle == msg.handle
                ),
                None,
            )
            if characteristic is None:
                raise KeyError(msg.handle)
            if msg.enable:
                await peer.subscribe(
                    characteristic,
                    lambda data: self.send(
                        pb.BluetoothGATTNotifyDataResponse(
                            address=msg.address, handle=msg.handle, data=data
                        )
                    ),
                )
            else:
                await peer.unsubscribe(characteristic)
            self.send(
                pb.BluetoothGATTNotifyResponse(address=msg.address, handle=msg.handle)
            )


async def serve(args):
    async with await open_transport(args.transport) as (source, sink):
        radio = Device.with_hci(args.name, Address(args.random_address), source, sink)
        await asyncio.wait_for(radio.power_on(), 20)
        proxy = Proxy(radio, args.name, args.slots)
        server = await asyncio.start_server(proxy.accept, args.listen, args.port)
        LOG.info("Listening on %s:%s", args.listen, args.port)
        try:
            async with server:
                await server.serve_forever()
        finally:
            if proxy.client:
                await proxy.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport", required=True, help="Bumble transport, e.g. usb:VID:PID"
    )
    parser.add_argument("--name", default="bumble-proxy")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6053)
    parser.add_argument("--slots", type=int, choices=range(1, 17), default=3)
    parser.add_argument("--random-address", default="F2:00:00:00:00:01")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
