import anyio
import socket
import sys


platform = ""
if sys.platform.startswith("linux"):
    platform = "linux"
elif sys.platform.startswith("darwin"):
    platform = "darwin"
elif sys.platform.startswith("win"):
    platform = "windows"


async def tcpsocket(address=None, connect_timeout=None, tcp_keepalive=None, tcp_nodelay=None, **kwargs):
    if address is None:
        address = ("localhost", 6379)
    async with anyio.fail_after(connect_timeout):
        sock = await anyio.connect_tcp(address[0], address[1])
    if tcp_nodelay is not None:
        if tcp_nodelay:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        else:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
    if tcp_keepalive:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if platform == "linux":
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, tcp_keepalive)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, tcp_keepalive // 3)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
        elif platform == "darwin":
            sock.setsockopt(socket.IPPROTO_TCP, 0x10, tcp_keepalive // 3)
        elif platform == "windows":
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, tcp_keepalive * 1000, tcp_keepalive // 3 * 1000))
    return sock


async def unixsocket(address=None, connect_timeout=None, **kwargs):
    if address is None:
        address = "/tmp/redis.sock"
    async with anyio.fail_after(connect_timeout):
        sock = await anyio.connect_unix(address)
    return sock


# TODO how do cluster hostname work with SSL ?
# TODO does closing this also close the socket itself ?
async def sslsocket(address=None, ssl_context=None, **kwargs):
    if address is None:
        address = ("localhost", 6379)
    # TODO (correctness) kinda dangerous, check out kwargs maybe ?
    return await tcpsocket(ssl_context=ssl_context, autostart_tls=True, **kwargs)


class SocketWrapper:
    @classmethod
    async def create(cls, socket_factory, buffersize=2 ** 16, socket_timeout=None, **kwargs):
        ret = cls()
        await ret._init(socket_factory, buffersize, socket_timeout, **kwargs)
        return ret

    async def _init(self, socket_factory, buffersize=2 ** 16, socket_timeout=None, **kwargs):
        self._buffersize = buffersize
        self._socket_timeout = socket_timeout
        self._socket = await socket_factory(**kwargs)

    async def aclose(self):
        await self._socket.close()

    async def send(self, data):
        if self._socket_timeout:
            async with anyio.fail_after(self._socket_timeout):
                await self._socket.send_all(data)
        else:
            await self._socket.send_all(data)

    # If you override this, make sure to return an empty bytes for EOF and a None for timeout !
    async def recv(self, timeout=False):
        if timeout is False:
            timeout = self._socket_timeout
        if timeout:
            try:
                async with anyio.fail_after(timeout):
                    return await self._socket.receive_some(self._buffersize)
            except TimeoutError:
                return None
        else:
            return await self._socket.receive_some(self._buffersize)

    def peername(self):
        peername = self._socket.peer_address
        if isinstance(peername, (list, tuple)):
            peername = peername[:2]
        return peername


class OurSemaphore:
    def __init__(self, value):
        self._semaphore = anyio.create_capcity_limiter(value)

    async def release(self):
        await self._semaphore.release()

    async def acquire(self, timeout=None):
        if timeout:
            async with anyio.fail_after(timeout):
                await self._semaphore.acquire()
        else:
            await self._semaphore.acquire()


class AnyIOEnvironment:
    @staticmethod
    async def socket(socket_type="tcp", **kwargs):
        if socket_type == "tcp":
            socket_type = tcpsocket
        elif socket_type == "unix":
            socket_type = unixsocket
        elif socket_type == "ssl":
            socket_type = sslsocket
        else:
            raise NotImplementedError("Unknown socket type: %s" % socket_type)
        return await SocketWrapper.create(socket_type, **kwargs)

    @staticmethod
    def semaphore(limit):
        return OurSemaphore(limit)

    @staticmethod
    def lock():
        return anyio.create_lock()
