# himalaya-remote-pocketid-mcp

One container that gives Claude your mailboxes through a single remote MCP
server, protected by PocketID OAuth.

```
Claude ──HTTPS──▶ Pangolin ──▶ himalaya-remote-pocketid-mcp container
                                 ├─ FastMCP gateway (OAuth via PocketID, /mcp)
                                 ├─ himalaya mcp --account gmail   (child process)
                                 ├─ himalaya mcp --account m365    (child process)
                                 └─ ortie (gets and refreshes OAuth tokens)
```

Everything is declared in files:

| File | What it holds |
|------|---------------|
| `config/himalaya.toml` | One table per mailbox. The gateway reads the names from here. |
| `config/ortie.toml` | One OAuth account per OAuth mailbox, same names. |
| `.env` | Public URL, PocketID client ID, bind address, tool flags. |
| `secrets/` | PocketID client secret, JWT key, storage key, app passwords. |
| `compose.yaml` | The container. |

To add a mailbox, add a table to `himalaya.toml` (and `ortie.toml` for OAuth),
restart, and run `himalaya-login` once. Nothing else changes.

Each account's tools are prefixed with its name: `gmail_envelope_list`,
`m365_message_read`, and so on.

## Setup

### 1. Create the secrets

```sh
mkdir -p secrets && chmod 700 secrets
openssl rand -base64 48 > secrets/jwt_signing_key
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" > secrets/storage_encryption_key
# No local Python? Use: openssl rand -base64 32 | tr '+/' '-_' > secrets/storage_encryption_key
touch secrets/oidc_client_secret        # filled in step 3
chmod 600 secrets/*
```

### 2. Fill in the mailbox configs

Edit `config/himalaya.toml` and `config/ortie.toml`. Replace the example
addresses. Delete the accounts you don't use. Keep the account names the same
in both files.

### 3. Create the PocketID client

In PocketID, create an OIDC client:

- Name: `himalaya-remote-pocketid-mcp`
- Callback URL: `https://<your BASE_URL host>/auth/callback`
- Public client: off
- Allowed user groups: only your own user or group

Put the client secret in `secrets/oidc_client_secret` and the client ID in `.env`.

### 4. Set `.env` and start

```sh
cp .env.example .env    # then edit
docker compose up -d --build
docker compose logs -f himalaya-remote-pocketid-mcp
```

The first build compiles Himalaya from source. It takes several minutes.

### 5. Sign in to each OAuth mailbox (one time)

```sh
docker compose exec himalaya-remote-pocketid-mcp himalaya-login gmail
docker compose exec himalaya-remote-pocketid-mcp himalaya-login m365
```

Open the link, approve, then paste back the address of the localhost page that
fails to load. ortie stores the tokens in the `himalaya-data` volume and refreshes
them on its own after that.

Check each account:

```sh
docker compose exec himalaya-remote-pocketid-mcp himalaya -c /config/himalaya.toml account check -a gmail
```

### 6. Publish through Pangolin

Create an HTTPS resource for `BASE_URL` with the target `BIND_ADDR:HOST_PORT`.
**Turn Pangolin authentication off on this resource.** Claude connects from
Anthropic's servers and can't complete a Pangolin browser login. The PocketID
OAuth in the container controls access instead.

### 7. Add the connector in Claude

Customize → Connectors → Add custom connector:

- URL: `https://<your BASE_URL host>/mcp`
- Authentication: sign in
- OAuth client: "Use Claude's published identity" or "Register automatically"

Claude opens a PocketID login, then a consent page from the gateway. Approve both.

## Operation

| Task | Command |
|------|---------|
| Logs | `docker compose logs -f himalaya-remote-pocketid-mcp` |
| Token status | `docker compose exec himalaya-remote-pocketid-mcp ortie token inspect -a gmail` |
| Force a refresh | `docker compose exec himalaya-remote-pocketid-mcp ortie token refresh -a gmail` |
| Sign in again | `docker compose exec himalaya-remote-pocketid-mcp himalaya-login gmail` |
| Upgrade Himalaya | change `HIMALAYA_REV` in the Dockerfile, then `docker compose up -d --build` |
| Health | `curl http://BIND_ADDR:HOST_PORT/healthz` |

Back up the `himalaya-data` volume with Restic. It holds the OAuth refresh tokens
and the gateway's encrypted client store. Without it you must sign in again.

## Notes

- **Gmail with Thunderbird's client** (the default in `ortie.toml`) works
  without a Google Cloud project. To use your own client instead, set it to
  "In production" status. In "Testing" status Google expires refresh tokens
  after 7 days.
- **Microsoft 365**: if your tenant blocks user consent, a tenant admin must
  approve "Mozilla Thunderbird" once in Entra ID.
- **Tool access**: `HIMALAYA_ALLOW_WRITE` adds flag, move, delete and
  save-draft tools. `HIMALAYA_ALLOW_SEND` adds the send tools.
- **Local test without OAuth**: set `MCP_AUTH=none` and keep the port bound
  to `127.0.0.1`. Never expose it that way.
