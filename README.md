# n8n Nextcloud ExApp

A Nextcloud External Application (ExApp) that integrates [n8n](https://n8n.io) workflow automation directly into Nextcloud.

## Features

- **400+ Integrations** - Connect to hundreds of services including Nextcloud
- **Visual Workflow Builder** - Drag-and-drop interface for creating automations
- **Webhook Support** - Trigger workflows from external events
- **Self-hosted & Private** - All data stays on your server
- **AI Capabilities** - Integrate with LLMs for intelligent workflows

## Requirements

- Nextcloud 30 or higher
- [AppAPI](https://apps.nextcloud.com/apps/app_api) installed and configured
- Docker with a configured Deploy Daemon (HaRP recommended)

## Installation

### Via Nextcloud App Store

1. Install and enable the **AppAPI** app in Nextcloud
2. Configure a Deploy Daemon (HaRP or Docker Socket Proxy)
3. Search for "n8n" in the External Apps section
4. Click Install

### Manual Installation

```bash
# Register the ExApp with Nextcloud
occ app_api:app:register \
    n8n \
    <your-daemon-name> \
    --info-xml https://raw.githubusercontent.com/ConductionNL/n8n-nextcloud/main/appinfo/info.xml \
    --force-scopes
```

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `N8N_EXTERNAL_DATABASE` | PostgreSQL connection string | SQLite |
| `N8N_ENCRYPTION_KEY` | Encryption key for credentials | Auto-generated |
| `N8N_TIMEZONE` | Timezone for scheduled workflows | Europe/Amsterdam |

## Usage

After installation, access n8n through Nextcloud:

```
https://your-nextcloud/index.php/apps/app_api/proxy/n8n
```

Or use the External Apps section in Nextcloud's admin panel.

## Development

### Building the Docker Image

```bash
docker build -t n8n-exapp:dev .
```

### Running Locally

```bash
docker run -it --rm \
    -e APP_ID=n8n \
    -e APP_SECRET=dev-secret \
    -e NEXTCLOUD_URL=http://localhost:8080 \
    -p 9000:9000 \
    -p 5678:5678 \
    n8n-exapp:dev
```

### Testing Endpoints

```bash
# Health check
curl http://localhost:9000/heartbeat

# Initialize
curl -X POST http://localhost:9000/init
```

## Architecture

This ExApp wraps n8n with a FastAPI application that implements the Nextcloud AppAPI lifecycle:

```
┌─────────────────────────────────────┐
│         Nextcloud + AppAPI          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│     n8n ExApp Container             │
│  ┌───────────────────────────────┐  │
│  │  FastAPI Wrapper (port 9000)  │  │
│  │  - /heartbeat                 │  │
│  │  - /init                      │  │
│  │  - /enabled                   │  │
│  │  - /* (proxy to n8n)          │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────▼───────────────┐  │
│  │    n8n Server (port 5678)     │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

## License

AGPL-3.0 - See [LICENSE](LICENSE) for details.

## Links

- [n8n Documentation](https://docs.n8n.io)
- [Nextcloud AppAPI Documentation](https://docs.nextcloud.com/server/stable/developer_manual/exapp_development/Introduction.html)
- [Conduction](https://conduction.nl)
