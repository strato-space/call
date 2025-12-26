# Google Auth Overview

This note captures how Google authentication is handled for:
- `mcp-google-sheets` (Sheets/Drive API, Python)
- `media-gen-mcp` (Vertex AI Veo video generation, TypeScript)

We standardize on **Application Default Credentials (ADC)** via
`GOOGLE_APPLICATION_CREDENTIALS` as the primary, non-interactive path.
OAuth remains a secondary option for local development or one-off troubleshooting.

## Primary method (ADC via GOOGLE_APPLICATION_CREDENTIALS)

Recommended for servers and automation.

- Set `GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json`.
- Both subsystems use Google client libraries that honor ADC.
- For Vertex AI, ensure these are set as well:
  - `GOOGLE_GENAI_USE_VERTEXAI=true`
  - `GOOGLE_CLOUD_PROJECT=<gcp-project-id>`
  - `GOOGLE_CLOUD_LOCATION=<region>`

Notes:
- We do not set any upstream-specific service-account env vars in this repo.
  Our configs rely on ADC via `GOOGLE_APPLICATION_CREDENTIALS`.

## Secondary method (OAuth)

Use only when a service account is not available or for local dev.

### mcp-google-sheets (Sheets/Drive)
OAuth options:
- OAuth client JSON + token file:
  - `CREDENTIALS_PATH=/path/to/credentials.json`
  - `TOKEN_PATH=/path/to/token.json`
- Or ADC user credentials via:
  - `gcloud auth application-default login`
  - Stores user creds in `~/.config/gcloud/application_default_credentials.json`

### media-gen-mcp (Vertex AI)
OAuth option (ADC user creds):
- `gcloud auth application-default login`
- Uses `~/.config/gcloud/application_default_credentials.json` as ADC.

## Current state in this repo

Active credential path:
- `/home/strato-space/call/.env`
  - `GOOGLE_APPLICATION_CREDENTIALS=/etc/fast-agent/gcp/fast-agent-vp.json`
- `/home/strato-space/server/mcp/.env`
  - `GOOGLE_APPLICATION_CREDENTIALS=/etc/fast-agent/gcp/fast-agent-vp.json`

Runtime wiring:
- `mcp@` systemd units read `/home/strato-space/call/.env`
  and `/home/strato-space/server/mcp/<name>.env`.
- `gsh` (Google Sheets) inherits ADC from `/home/strato-space/call/.env`.
- `media-gen` (Vertex AI) adds:
  - `GOOGLE_GENAI_USE_VERTEXAI=true`
  - `GOOGLE_CLOUD_PROJECT=strato-space-ai`
  - `GOOGLE_CLOUD_LOCATION=us-central1`
  via `/home/strato-space/server/mcp/media-gen.env`.

Other local configs:
- `/home/strato-space/ai/.env`
  - `GOOGLE_APPLICATION_CREDENTIALS=C:\Users\Leader\PycharmProjects\ai\wallet\service-account-key.json`
- `call/mcp_config.json` and `agent/mcp_config.sample.json`
  - Local Sheets examples now use `GOOGLE_APPLICATION_CREDENTIALS`.
