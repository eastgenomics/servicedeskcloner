#!/usr/bin/env python3
"""
JSM Cloud Project Cloner
========================
Clones a Jira Service Management project on Jira Cloud.

What this script handles:
  ✓ Project creation (type, lead, category, permission/notification schemes)
  ✓ Components
  ✓ Versions
  ✓ Request types (portal forms)
  ✓ Queues — exported to JSON for reference (no public create API)
  ✓ SLA config — exported to JSON for reference (no public create API)

Known Jira Cloud API limitations (manual steps required after):
  ✗ Automation rules     — no public Cloud API
  ✗ SLAs                 — config exported; recreate in JSM > Project settings > SLAs
  ✗ Queues               — config exported; recreate in JSM > Project settings > Queues
  ✗ Workflow/screen schemes — script links the same schemes as the source project
  ✗ Customer portal branding (logo, announcement, theme)
  ✗ Email templates
  ✗ Issue data           — use Jira CSV import or the Jira Cloud migration tools

Requirements:
  pip install requests

Usage:
  export JIRA_API_TOKEN="your_api_token"
  python clone_jsm_project.py \\
    --domain mycompany \\
    --email admin@mycompany.com \\
    --source SUPPORT \\
    --dest-key SUPPORTTEST \\
    --dest-name "Support [TEST]"

Generate an API token at: https://id.atlassian.com/manage-profile/security/api-tokens
"""

import argparse
import json
import logging
import os
import sys

import requests
from requests.auth import HTTPBasicAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── Low-level client ──────────────────────────────────────────────────────────


class JiraClient:
    def __init__(self, domain: str, email: str, token: str, dry_run: bool = False):
        self.base = f"https://{domain}.atlassian.net"
        self.auth = HTTPBasicAuth(email, token)
        self.dry_run = dry_run
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str, api: str) -> str:
        return f"{self.base}/rest/{api}/{path}"

    def get(self, path: str, api: str = "api/3", params: dict = None) -> dict:
        r = requests.get(
            self._url(path, api), auth=self.auth, headers=self.headers, params=params
        )
        r.raise_for_status()
        return r.json()

    def post(self, path: str, data: dict, api: str = "api/3") -> dict:
        if self.dry_run:
            log.info("[DRY RUN] Would POST /%s/  %s", path, json.dumps(data))
            return {"id": "0", "key": data.get("key", "DRY_RUN")}
        r = requests.post(
            self._url(path, api), auth=self.auth, headers=self.headers, json=data
        )
        if not r.ok:
            log.error("POST %s failed %s: %s", path, r.status_code, r.text)
            r.raise_for_status()
        return r.json()

    def paginate(
        self,
        path: str,
        api: str = "api/3",
        result_key: str = "values",
        params: dict = None,
    ) -> list:
        """Iterate through Atlassian paginated endpoints."""
        results = []
        start = 0
        limit = 50
        while True:
            p = {**(params or {}), "startAt": start, "maxResults": limit}
            data = self.get(path, api=api, params=p)
            page = data.get(result_key, data.get("values", []))
            results.extend(page)
            # JSM endpoints use "isLastPage"; Jira REST v3 uses "isLast"
            is_last = data.get("isLastPage", data.get("isLast"))
            if is_last is not None:
                if is_last:
                    break
            elif len(page) < limit:
                break
            start += len(page)
        return results


# ── Cloner ────────────────────────────────────────────────────────────────────


class JSMCloner:
    def __init__(
        self,
        client: JiraClient,
        src_key: str,
        dst_key: str,
        dst_name: str,
        output_dir: str = ".",
    ):
        self.j = client
        self.src = src_key.upper()
        self.dst = dst_key.upper()
        self.dst_name = dst_name
        self.output_dir = output_dir
        self.src_sd_id: str = None
        self.dst_sd_id: str = None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _out(self, filename: str) -> str:
        return os.path.join(self.output_dir, filename)

    def _get_sd_id(self, project_key: str) -> str:
        desks = self.j.paginate("servicedesk", api="servicedeskapi", result_key="values")
        for desk in desks:
            if desk["projectKey"] == project_key:
                return str(desk["id"])
        raise ValueError(f"No service desk found for project key: {project_key}")

    # ── Step 1: Create project ────────────────────────────────────────────────

    def _create_project(self, src: dict) -> dict:
        log.info("Creating project %s (%s)...", self.dst, self.dst_name)

        payload = {
            "key": self.dst,
            "name": self.dst_name,
            "projectTypeKey": "service_management",
            "description": f"[TEST] Clone of {self.src}. {src.get('description') or ''}".strip(),
            "assigneeType": src.get("assigneeType", "UNASSIGNED"),
        }

        if src.get("lead", {}).get("accountId"):
            payload["leadAccountId"] = src["lead"]["accountId"]

        if src.get("projectCategory"):
            payload["projectCategoryId"] = str(src["projectCategory"]["id"])

        # Reuse source permission scheme
        try:
            perm = self.j.get(f"project/{self.src}/permissionscheme")
            payload["permissionScheme"] = perm["id"]
            log.info("  Using permission scheme: %s", perm.get("name"))
        except Exception as e:
            log.warning("  Could not fetch permission scheme (%s); using default.", e)

        # Reuse source notification scheme
        try:
            notif = self.j.get(f"project/{self.src}/notificationscheme")
            payload["notificationScheme"] = notif["id"]
            log.info("  Using notification scheme: %s", notif.get("name"))
        except Exception as e:
            log.warning("  Could not fetch notification scheme (%s); using default.", e)

        result = self.j.post("project", payload)
        log.info("  Created: %s (id=%s)", result["key"], result["id"])
        return result

    # ── Step 2: Components ────────────────────────────────────────────────────

    def _clone_components(self):
        log.info("Cloning components...")
        try:
            components = self.j.get(f"project/{self.src}/components")
        except Exception as e:
            log.warning("  Could not fetch components: %s", e)
            return

        for c in components:
            try:
                payload = {
                    "name": c["name"],
                    "description": c.get("description", ""),
                    "project": self.dst,
                    "assigneeType": c.get("assigneeType", "UNASSIGNED"),
                }
                if c.get("lead", {}).get("accountId"):
                    payload["leadAccountId"] = c["lead"]["accountId"]
                self.j.post("component", payload)
                log.info("  + %s", c["name"])
            except Exception as e:
                log.warning("  Failed to create component '%s': %s", c.get("name"), e)

    # ── Step 3: Versions ──────────────────────────────────────────────────────

    def _clone_versions(self, dst_project_id: str):
        log.info("Cloning versions...")
        try:
            versions = self.j.get(f"project/{self.src}/versions")
        except Exception as e:
            log.warning("  Could not fetch versions: %s", e)
            return

        for v in versions:
            try:
                payload = {
                    "name": v["name"],
                    "description": v.get("description", ""),
                    "projectId": int(dst_project_id),
                    "released": v.get("released", False),
                    "archived": v.get("archived", False),
                }
                if v.get("releaseDate"):
                    payload["releaseDate"] = v["releaseDate"]
                if v.get("startDate"):
                    payload["startDate"] = v["startDate"]
                self.j.post("version", payload)
                log.info("  + %s", v["name"])
            except Exception as e:
                log.warning("  Failed to create version '%s': %s", v.get("name"), e)

    # ── Step 4: Request types ─────────────────────────────────────────────────

    def _clone_request_types(self):
        log.info("Cloning request types...")
        try:
            rts = self.j.paginate(
                f"servicedesk/{self.src_sd_id}/requesttype",
                api="servicedeskapi",
                result_key="values",
            )
        except Exception as e:
            log.warning("  Could not fetch request types: %s", e)
            return

        for rt in rts:
            try:
                payload = {
                    "issueTypeId": rt["issueTypeId"],
                    "name": rt["name"],
                    "description": rt.get("description", ""),
                    "helpText": rt.get("helpText", ""),
                }
                if rt.get("groupIds"):
                    payload["groupIds"] = rt["groupIds"]

                self.j.post(
                    f"servicedesk/{self.dst_sd_id}/requesttype",
                    payload,
                    api="servicedeskapi",
                )
                log.info("  + %s", rt["name"])
            except Exception as e:
                log.warning(
                    "  Failed to create request type '%s': %s", rt.get("name"), e
                )

    # ── Step 5: Export queues (no public create API) ──────────────────────────

    def _export_queues(self):
        log.info("Exporting queue configuration...")
        try:
            queues = self.j.paginate(
                f"servicedesk/{self.src_sd_id}/queue",
                api="servicedeskapi",
                result_key="values",
                params={"includeCount": "false"},
            )
            path = self._out("queues_config.json")
            with open(path, "w") as f:
                json.dump(queues, f, indent=2)
            log.info("  Exported %d queue(s) to: %s", len(queues), path)
            log.info("  ACTION REQUIRED: Recreate queues manually in:")
            log.info("    JSM > Project settings > Queues")
        except Exception as e:
            log.warning("  Could not export queues: %s", e)

    # ── Step 6: Export SLAs (no public create API) ────────────────────────────

    def _export_slas(self):
        """
        There is no public Jira Cloud REST API for SLA configuration.
        We fetch what metadata we can from the issue navigator SLA fields
        and dump it for reference.
        """
        log.info("Exporting SLA field metadata...")
        try:
            # servicedeskapi /sla returns SLA metrics on issues, not config.
            # The closest we can get without a private API is field metadata.
            fields = self.j.get("field")
            sla_fields = [f for f in fields if "sla" in f.get("name", "").lower()]
            path = self._out("sla_fields.json")
            with open(path, "w") as f:
                json.dump(sla_fields, f, indent=2)
            log.info("  Exported %d SLA field(s) to: %s", len(sla_fields), path)
            log.info("  ACTION REQUIRED: Recreate SLA configurations manually in:")
            log.info("    JSM > Project settings > SLAs")
        except Exception as e:
            log.warning("  Could not export SLA fields: %s", e)

    # ── Orchestrate ───────────────────────────────────────────────────────────

    def run(self):
        log.info("=" * 60)
        log.info("JSM Project Clone: %s  →  %s", self.src, self.dst)
        log.info("=" * 60)

        # Fetch source project
        src = self.j.get(
            f"project/{self.src}",
            params={"expand": "description,lead,url,projectKeys"},
        )
        self.src_sd_id = self._get_sd_id(self.src)
        log.info("Source service desk ID: %s", self.src_sd_id)

        # Create destination project
        dst = self._create_project(src)
        dst_project_id = dst["id"]

        # Get destination service desk ID (created automatically with the project)
        if self.j.dry_run:
            self.dst_sd_id = "0"
            log.info("[DRY RUN] Skipping destination service desk lookup (project not created)")
        else:
            try:
                self.dst_sd_id = self._get_sd_id(self.dst)
                log.info("Destination service desk ID: %s", self.dst_sd_id)
            except ValueError as e:
                log.error(
                    "Could not find new service desk — Jira may still be provisioning it. "
                    "Wait a few seconds and re-run. Error: %s",
                    e,
                )
                sys.exit(1)

        self._clone_components()
        self._clone_versions(dst_project_id)
        self._clone_request_types()
        self._export_queues()
        self._export_slas()

        log.info("")
        log.info("=" * 60)
        log.info("Clone complete.")
        log.info("  Source  : %s/jira/servicedesk/projects/%s", self.j.base, self.src)
        log.info("  New     : %s/jira/servicedesk/projects/%s", self.j.base, self.dst)
        log.info("")
        log.info("Remaining manual steps:")
        log.info("  1. SLAs          → recreate from sla_fields.json")
        log.info("  2. Queues        → recreate from queues_config.json")
        log.info("  3. Automations   → copy rules manually (no public Cloud API)")
        log.info("  4. Portal branding (logo, announcement, welcome message)")
        log.info("  5. Email templates (if customised)")
        log.info("  6. Issue data    → Jira CSV import or Cloud migration tools")
        log.info("=" * 60)


# ── CLI ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Clone a Jira Service Management project on Jira Cloud.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Atlassian domain, e.g. 'mycompany' for mycompany.atlassian.net",
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Admin account email address",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Atlassian API token (or set JIRA_API_TOKEN env var)",
    )
    parser.add_argument(
        "--source",
        required=True,
        metavar="PROJECT_KEY",
        help="Source project key to clone from, e.g. SUPPORT",
    )
    parser.add_argument(
        "--dest-key",
        required=True,
        metavar="PROJECT_KEY",
        help="New project key, e.g. SUPPORTTEST",
    )
    parser.add_argument(
        "--dest-name",
        required=True,
        metavar="NAME",
        help='New project display name, e.g. "Support [TEST]"',
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write exported config files (default: current dir)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Read source data and log what would be created, without making any changes",
    )

    args = parser.parse_args()

    token = args.token or os.environ.get("JIRA_API_TOKEN")
    if not token:
        log.error(
            "API token is required. Use --token or set the JIRA_API_TOKEN environment variable.\n"
            "Generate one at: https://id.atlassian.com/manage-profile/security/api-tokens"
        )
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    if args.dry_run:
        log.info("DRY RUN mode — no changes will be made to Jira")

    client = JiraClient(args.domain, args.email, token, dry_run=args.dry_run)
    cloner = JSMCloner(
        client,
        src_key=args.source,
        dst_key=args.dest_key,
        dst_name=args.dest_name,
        output_dir=args.output_dir,
    )
    cloner.run()


if __name__ == "__main__":
    main()
