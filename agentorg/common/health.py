"""Health probe wiring for FastMCP agent servers (mirrors astrolabe)."""


def register_health(mcp):
    """Register a GET /health readiness route on a FastMCP server."""

    @mcp.custom_route("/health", methods=["GET"])
    async def _health(_request):  # pragma: no cover - trivial
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok"})

    return mcp
