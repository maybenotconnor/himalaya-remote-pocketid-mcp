"""himalaya-remote-pocketid-mcp gateway.

Reads the account names from himalaya.toml, starts one `himalaya mcp` child
process per account, mounts each under its account name, and serves them all
over streamable HTTP at /mcp behind a PocketID (OIDC) OAuth proxy.

Everything is set by config files and environment variables. This file does
not change when you add or remove an account.
"""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.client.transports import StdioTransport
from fastmcp.server import create_proxy
from starlette.requests import Request
from starlette.responses import PlainTextResponse

log = logging.getLogger("himalaya-remote-pocketid-mcp")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")

# Environment names that hold gateway secrets. They are not passed to Himalaya.
GATEWAY_SECRET_PREFIXES = ("POCKETID_", "JWT_", "STORAGE_ENCRYPTION_")


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read NAME, or the file named by NAME_FILE (Docker secrets)."""
    value = os.environ.get(name)
    if value is None and (path := os.environ.get(f"{name}_FILE")):
        value = Path(path).read_text().strip()
    if value is None or value == "":
        value = default
    if required and not value:
        sys.exit(f"himalaya-remote-pocketid-mcp: {name} (or {name}_FILE) must be set")
    return value


def flag(name: str, default: bool) -> bool:
    return (env(name, str(default)) or "").strip().lower() in {"1", "true", "yes", "on"}


def csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in (env(name, default) or "").split(",") if item.strip()]


def load_accounts(config_path: str) -> list[str]:
    try:
        data = tomllib.loads(Path(config_path).read_text())
    except FileNotFoundError:
        sys.exit(f"himalaya-remote-pocketid-mcp: Himalaya config not found at {config_path}")
    accounts = list(data.get("accounts", {}))
    only = csv("HIMALAYA_ACCOUNTS", "")
    if only:
        missing = sorted(set(only) - set(accounts))
        if missing:
            sys.exit(f"himalaya-remote-pocketid-mcp: HIMALAYA_ACCOUNTS names unknown accounts: {', '.join(missing)}")
        accounts = [a for a in accounts if a in only]
    if not accounts:
        sys.exit(f"himalaya-remote-pocketid-mcp: no [accounts.<name>] tables in {config_path}")
    return accounts


def build_auth():
    mode = (env("MCP_AUTH", "oidc") or "oidc").lower()
    if mode == "none":
        log.warning("MCP_AUTH=none: the server has NO authentication. Use this for local tests only.")
        return None
    if mode != "oidc":
        sys.exit(f"himalaya-remote-pocketid-mcp: MCP_AUTH must be 'oidc' or 'none', not {mode!r}")

    from cryptography.fernet import Fernet
    from fastmcp.server.auth.oidc_proxy import OIDCProxy
    from key_value.aio.stores.filetree import FileTreeStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    # Client registrations and upstream tokens survive restarts, encrypted at rest.
    storage = FernetEncryptionWrapper(
        FileTreeStore(data_directory=env("OAUTH_STORAGE_DIR", "/data/oauth")),
        fernet=Fernet(env("STORAGE_ENCRYPTION_KEY", required=True)),
    )

    # Same PocketID settings as opencloud-pocketid-mcp: request openid, profile
    # and email, and verify PocketID's ID token rather than its access token.
    return OIDCProxy(
        config_url=env("POCKETID_CONFIG_URL", required=True),
        client_id=env("POCKETID_CLIENT_ID", required=True),
        client_secret=env("POCKETID_CLIENT_SECRET", required=True),
        base_url=env("MCP_BASE_URL", required=True),
        required_scopes=csv("POCKETID_SCOPES", "openid,profile,email"),
        verify_id_token=True,
        allowed_client_redirect_uris=csv(
            "ALLOWED_CLIENT_REDIRECT_URIS",
            "https://claude.ai/api/mcp/auth_callback,http://localhost:*,http://127.0.0.1:*",
        ),
        client_storage=storage,
        jwt_signing_key=env("JWT_SIGNING_KEY", required=True),
    )


def himalaya_args(account: str, config_path: str) -> list[str]:
    args = ["-c", config_path, "mcp", "--account", account]
    if flag("HIMALAYA_ALLOW_WRITE", True):
        args.append("--allow-write")
    if flag("HIMALAYA_ALLOW_SEND", False):
        args.append("--allow-send")
    return args


def child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith(GATEWAY_SECRET_PREFIXES)}


def build_server() -> FastMCP:
    config_path = env("HIMALAYA_CONFIG", "/config/himalaya.toml")
    accounts = load_accounts(config_path)

    mcp = FastMCP(name=env("MCP_SERVER_NAME", "himalaya"), auth=build_auth())

    for account in accounts:
        transport = StdioTransport(
            command=env("HIMALAYA_BIN", "himalaya"),
            args=himalaya_args(account, config_path),
            env=child_env(),
            keep_alive=True,  # one Himalaya process per account, reused across requests
        )
        mcp.mount(create_proxy(transport, mode="auto", name=f"himalaya-{account}"), namespace=account)
        log.info("mounted account %r as tools prefixed %r", account, f"{account}_")

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return mcp


if __name__ == "__main__":
    build_server().run(
        transport="http",
        host=env("HOST", "0.0.0.0"),
        port=int(env("PORT", "8000")),
        path=env("MCP_PATH", "/mcp"),
        show_banner=False,
    )
