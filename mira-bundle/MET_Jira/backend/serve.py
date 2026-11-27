"""Configured Uvicorn entry point used by systemd services."""

from __future__ import annotations

import uvicorn

from .config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.web_host,
        port=settings.web_port,
        workers=settings.web_workers,
        proxy_headers=True,
        forwarded_allow_ips=settings.proxy_trusted_ips,
    )


if __name__ == "__main__":
    main()
