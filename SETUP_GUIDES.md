# OMNIME — Detailed setup guides

This document covers the optional integrations. The bot works without them;
follow the relevant section only if you want that feature.

---

## Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a display name, then a unique username ending in `bot`.
3. Copy the API token printed at the end. Put it in `.env` as `TELEGRAM_BOT_TOKEN`.
4. Send `/setcommands` to BotFather and paste the command list from the README.
5. Get your numeric user ID by messaging [@userinfobot](https://t.me/userinfobot).
   Put it in `.env` as `TELEGRAM_USER_ID`.

The bot rejects every message that does not come from `TELEGRAM_USER_ID`, so
nobody else can interact with your data.

---

## Anthropic / OpenAI / Ollama

OMNIME supports three providers, picked via `LLM_PROVIDER`:

| Provider  | Setup                                                           |
|-----------|-----------------------------------------------------------------|
| anthropic | Generate a key at https://console.anthropic.com/. Set `ANTHROPIC_API_KEY`. |
| openai    | Get a key at https://platform.openai.com/. Set `OPENAI_API_KEY`. |
| ollama    | Run `ollama serve` locally; set `OLLAMA_HOST` (default `http://localhost:11434`) and `OLLAMA_MODEL`. |

You can also configure a fallback provider with `LLM_FALLBACK_PROVIDER` and
`LLM_FALLBACK_MODEL`. OMNIME will retry the primary, then switch automatically.

---

## Gmail integration

1. Go to https://console.cloud.google.com/, create or pick a project.
2. Enable the **Gmail API** under "APIs & Services" → "Enable APIs".
3. Configure OAuth consent screen:
   - User type: External
   - Scopes: add `gmail.readonly`, `gmail.send`, `gmail.compose`, `gmail.modify`.
   - Test users: add your own Google account.
4. Create credentials → OAuth client ID → Application type: **Desktop app**.
5. Download the JSON. Note the `client_id` and `client_secret`.
6. Get a refresh token using a one-off script (you can use `google-auth-oauthlib`'s
   `InstalledAppFlow.run_local_server`). Save the `refresh_token`.
7. Fill in `.env`:
   ```
   GMAIL_CLIENT_ID=...
   GMAIL_CLIENT_SECRET=...
   GMAIL_REFRESH_TOKEN=...
   ```

Test with `/briefing` — if Gmail is configured, you'll see your unread.

---

## Google Calendar integration

Same flow as Gmail, but enable the **Calendar API** and add the
`https://www.googleapis.com/auth/calendar` scope. Variables:

```
GCAL_CLIENT_ID=...
GCAL_CLIENT_SECRET=...
GCAL_REFRESH_TOKEN=...
```

---

## GitHub integration (for self-evolution)

1. Create a personal access token at
   https://github.com/settings/tokens with the `repo` scope.
2. Create a private repo to host generated skills (e.g. `you/omnime-skills`).
3. Set `GITHUB_TOKEN` and `GITHUB_REPO=you/omnime-skills` in `.env`.

When you `/evolve`, OMNIME will optionally commit the new skill to that repo
once you approve.

---

## Encryption key

Generate a Fernet key for encrypting sensitive fields at rest:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the value in `.env` as `ENCRYPTION_KEY`.

---

## Production deployment

The Dockerfile and `docker-compose.yml` are production-ready. Recommended:

```bash
ssh user@your-vps
sudo apt update && sudo apt install -y docker.io docker-compose-v2
git clone <your repo>
cd omnime
cp .env.example .env  # fill it in
docker compose pull   # ensures latest postgres + chroma
docker compose up -d
docker compose logs -f omnime  # tail the bot
```

For webhook mode (recommended in production), set:
```
TELEGRAM_MODE=webhook
WEBHOOK_URL=https://your.domain
```
and put a TLS-terminating proxy (Caddy, nginx, Traefik) in front.

---

## Backups

Run `python -m scripts.backup` (or `/backup` in Telegram) to dump everything
to `data/backups/backup_<timestamp>.json`. Restore with
`python -m scripts.restore data/backups/backup_<timestamp>.json`.

For Postgres-level backups, schedule `pg_dump` against the `omnime-db`
container externally.
