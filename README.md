# JSM Cloud Project Cloner

A Python script to clone a Jira Service Management (JSM) project on Jira Cloud, including project configuration, components, versions, and request types. Queue and SLA configurations are exported as JSON for manual recreation.

## Prerequisites

- Python 3.7+
- A Jira Cloud instance with admin access
- An Atlassian API token

```bash
pip install requests
```

Generate an API token at: https://id.atlassian.com/manage-profile/security/api-tokens

## Usage

```bash
export JIRA_API_TOKEN="your_api_token"

python clone_jsm_project.py \
  --domain mycompany \
  --email admin@mycompany.com \
  --source SUPPORT \
  --dest-key SUPPORTTEST \
  --dest-name "Support [TEST]" \
  --output-dir ./jsm-clone-output
```

### Arguments

| Argument | Required | Description |
|---|---|---|
| `--domain` | Yes | Atlassian subdomain (e.g. `mycompany` for `mycompany.atlassian.net`) |
| `--email` | Yes | Admin account email address |
| `--token` | No | API token (can use `JIRA_API_TOKEN` env var instead) |
| `--source` | Yes | Source project key to clone (e.g. `SUPPORT`) |
| `--dest-key` | Yes | New project key (e.g. `SUPPORTTEST`) |
| `--dest-name` | Yes | New project display name (e.g. `"Support [TEST]"`) |
| `--output-dir` | No | Directory for exported config files (default: `.`) |

## What gets cloned

| Item | How |
|---|---|
| Project (type, lead, category) | Automated |
| Permission & notification schemes | Automated — reuses source schemes |
| Components | Automated |
| Versions | Automated |
| Request types (portal forms) | Automated |
| Queue configuration | Exported to `queues_config.json` |
| SLA field metadata | Exported to `sla_fields.json` |

## Manual steps required after running

The following cannot be automated due to Jira Cloud API limitations:

1. **SLAs** — recreate using `sla_fields.json` as reference via *Project settings > SLAs*
2. **Queues** — recreate using `queues_config.json` (includes JQL) via *Project settings > Queues*
3. **Automation rules** — copy manually; Atlassian provides no public Cloud API for automations
4. **Portal branding** — logo, theme colour, welcome message, and announcement
5. **Email templates** — if customised from the default
6. **Issue data** — use Jira's CSV import or the built-in Cloud migration tools if needed

## Output files

When queues or SLA metadata cannot be created via API, the script writes reference files to `--output-dir`:

- `queues_config.json` — full queue definitions including names and JQL filters
- `sla_fields.json` — SLA field metadata

## Security note

Never commit your API token. Use the `JIRA_API_TOKEN` environment variable or a secrets manager rather than passing it via `--token` in shell history.
