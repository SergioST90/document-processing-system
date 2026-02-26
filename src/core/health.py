"""Minimal HTTP health check server for Kubernetes probes."""

from aiohttp import web


class HealthServer:
    """Lightweight HTTP server exposing /health and /ready endpoints."""

    def __init__(self, port: int = 8080):
        self._port = port
        self._app = web.Application()
        self._app.router.add_get("/health", self._health)
        self._app.router.add_get("/ready", self._ready)
        self._runner: web.AppRunner | None = None
        self._is_ready = False

    def set_ready(self, ready: bool = True) -> None:
        self._is_ready = ready

    async def _health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def _ready(self, _request: web.Request) -> web.Response:
        if self._is_ready:
            return web.json_response({"status": "ready"})
        return web.json_response({"status": "not_ready"}, status=503)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
