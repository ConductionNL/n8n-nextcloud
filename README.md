<p align="center">
  <img src="img/app.svg" alt="n8n for Nextcloud logo" width="80" height="80">
</p>

<h1 align="center">n8n for Nextcloud</h1>

<p align="center">
  <strong>Workflow automation with 400+ integrations, embedded directly in your Nextcloud</strong>
</p>

<p align="center">
  <a href="https://github.com/ConductionNL/n8n-nextcloud/releases"><img src="https://img.shields.io/github/v/release/ConductionNL/n8n-nextcloud" alt="Latest release"></a>
  <a href="https://github.com/ConductionNL/n8n-nextcloud/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-EUPL--1.2-blue" alt="License"></a>
</p>

---

> **IMPORTANT DISCLAIMER**
>
> **n8n** is developed and maintained by [**n8n GmbH**](https://n8n.io/). This Nextcloud app is a community wrapper that packages n8n as a Nextcloud External Application (ExApp). **Conduction B.V. does not provide support, licensing, guarantees, or services for n8n itself.**
>
> - For **n8n support, licensing, and pricing**, contact [n8n GmbH](https://n8n.io/pricing/) directly.
> - For **n8n documentation**, see [docs.n8n.io](https://docs.n8n.io/).
> - n8n has its **own licensing model** (the [Sustainable Use License](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)) which is separate from this wrapper's license. Please review n8n's license terms before use.
>
> This wrapper is licensed under **EUPL-1.2** and covers only the Nextcloud integration code (the FastAPI wrapper, AppAPI lifecycle, and user provisioning logic).

## What is n8n?

[n8n](https://n8n.io/) (pronounced "nodemation") is a workflow automation platform built by [n8n GmbH](https://n8n.io/). It enables you to connect apps and automate processes through a visual drag-and-drop interface.

- **400+ integrations** -- connect to hundreds of services, APIs, and databases
- **Visual workflow builder** -- design automations without writing code
- **Code when you need it** -- add JavaScript or Python for custom logic
- **Self-hosted** -- all data stays on your infrastructure
- **AI capabilities** -- integrate with LLMs for intelligent, AI-powered workflows
- **Webhook support** -- trigger workflows from external events in real time

For more information about n8n, visit the [n8n website](https://n8n.io/) or the [n8n GitHub project](https://github.com/n8n-io/n8n).

## What This App Does

This app is a **Nextcloud ExApp wrapper** around n8n. It does not modify n8n itself -- it packages the n8n server as a Docker container managed by Nextcloud's AppAPI. The wrapper adds Nextcloud-specific integration:

- **Automatic user provisioning** -- Nextcloud users are auto-created in n8n on first visit, no separate account setup needed
- **Seamless authentication** -- Nextcloud session is mapped to an n8n session via cookie injection, no separate login required
- **Container lifecycle management** -- Nextcloud handles starting, stopping, and updating the n8n container
- **Embedded UI** -- n8n is accessible directly within the Nextcloud interface via iframe integration
- **Persistent storage** -- user mappings and n8n data survive container restarts through AppAPI persistent storage

## Requirements

| Requirement | Details |
|-------------|---------|
| **Nextcloud** | 30 or higher |
| **AppAPI** | [Nextcloud AppAPI](https://apps.nextcloud.com/apps/app_api) installed and enabled |
| **Deploy Daemon** | Docker with a configured Deploy Daemon (HaRP recommended) |

## Installation

### Via Nextcloud App Store

1. Install and enable the **AppAPI** app in Nextcloud
2. Configure a Deploy Daemon (HaRP or Docker Socket Proxy)
3. Navigate to the **External Apps** section in Nextcloud admin
4. Search for "n8n" and click **Install**

### Manual Registration

```bash
# Register the ExApp with Nextcloud
docker exec -u www-data nextcloud php occ app_api:app:register \
    n8n \
    <your-daemon-name> \
    --info-xml https://raw.githubusercontent.com/ConductionNL/n8n-nextcloud/main/appinfo/info.xml \
    --force-scopes

# Enable the ExApp
docker exec -u www-data nextcloud php occ app_api:app:enable n8n
```

After installation, access n8n through Nextcloud at:

```
https://your-nextcloud/index.php/apps/app_api/proxy/n8n
```

## Configuration

Configure via Nextcloud Admin Settings or environment variables defined in the ExApp manifest:

| Variable | Description | Default |
|----------|-------------|---------|
| `N8N_EXTERNAL_DATABASE` | PostgreSQL connection string (e.g., `postgres://user:pass@host:5432/n8n`) | SQLite (built-in) |
| `N8N_ENCRYPTION_KEY` | Encryption key for stored credentials | Auto-generated |
| `N8N_TIMEZONE` | Timezone for scheduled workflows | `Europe/Amsterdam` |

## Architecture

This ExApp wraps n8n with a FastAPI application that implements the Nextcloud AppAPI lifecycle:

```
+-----------------------------------------+
|         Nextcloud + AppAPI              |
+-----------------+-----------------------+
                  | AUTHORIZATION-APP-API
                  v
+-----------------------------------------+
|     n8n ExApp Container                 |
|  +-----------------------------------+  |
|  |  FastAPI Wrapper (port 23000)     |  |
|  |  - /heartbeat                     |  |
|  |  - /enabled                       |  |
|  |  - /js/n8n-iframe-loader.js       |  |
|  |  - /* (proxy + auth to n8n)       |  |
|  |                                   |  |
|  |  Auth Layer:                      |  |
|  |  - Auto-setup n8n owner account   |  |
|  |  - Map NC users -> n8n users      |  |
|  |  - Inject n8n-auth cookie         |  |
|  +----------------+------------------+  |
|                   |                     |
|  +----------------v------------------+  |
|  |    n8n Server (port 5678)         |  |
|  +-----------------------------------+  |
+-----------------------------------------+
```

The FastAPI wrapper handles three responsibilities:

1. **AppAPI lifecycle** -- responds to `/heartbeat`, `/init`, and `/enabled` for Nextcloud container management
2. **User provisioning** -- on each proxied request, resolves the Nextcloud user, creates a matching n8n account if needed, and injects an authentication cookie
3. **Request proxying** -- forwards all other requests to the n8n server running on port 5678, rewriting URLs to account for the Nextcloud proxy prefix

## Links

| Resource | URL |
|----------|-----|
| **n8n Website** | [n8n.io](https://n8n.io/) |
| **n8n Documentation** | [docs.n8n.io](https://docs.n8n.io/) |
| **n8n GitHub** | [github.com/n8n-io/n8n](https://github.com/n8n-io/n8n) |
| **n8n Support & Pricing** | [n8n.io/pricing](https://n8n.io/pricing/) |
| **This Wrapper (GitHub)** | [ConductionNL/n8n-nextcloud](https://github.com/ConductionNL/n8n-nextcloud) |
| **Nextcloud AppAPI Docs** | [Nextcloud Developer Manual](https://docs.nextcloud.com/server/latest/developer_manual/exapp_development/) |
| **Conduction** | [conduction.nl](https://conduction.nl) |

## License

This **wrapper application** is licensed under **EUPL-1.2** (European Union Public License). See [LICENSE](LICENSE) for the full text.

**n8n itself** is licensed under the [Sustainable Use License](https://github.com/n8n-io/n8n/blob/master/LICENSE.md) by n8n GmbH. The Sustainable Use License is not an open-source license -- it permits self-hosting but restricts certain commercial uses. Review the n8n license terms at [github.com/n8n-io/n8n](https://github.com/n8n-io/n8n/blob/master/LICENSE.md) before deploying.

## Authors

**Wrapper application:** [Conduction B.V.](https://conduction.nl) -- info@conduction.nl

**n8n platform:** [n8n GmbH](https://n8n.io/) -- Berlin, Germany
