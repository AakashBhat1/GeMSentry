# Security and local configuration

## Existing workspace

The security remediation preserves this workspace's company profile in ignored
`config/company_profile.local.json`, vendor names and IDs in ignored
`config/google_sync_config.json`, and finalized records in ignored
`data/finalized_tenders.local.json`. The tracked company profile is a generic
example and finalized records are excluded from Git. Company profile saves always
write the local file. Back up these ignored files separately from Git.

For another existing installation, preserve its company profile and finalized
records under those `.local.json` names before updating to this version.
For a new installation, copy `config/company_profile.json` to
`config/company_profile.local.json` and enter the company's real eligibility
and preferences before using fit scores. The example assumes no registrations,
turnover or company experience; it is not a bidding profile.

## Google Sheets webhook: deployment action required

1. Use a long random secret in local `google_sync_config.json` under
   `webhook_secret`, or set `GEMSENTRY_WEBHOOK_SECRET`. A generated value has
   been saved in this workspace's local configuration if none existed.
2. In the master sheet's Apps Script project, set script property
   `GEMSENTRY_WEBHOOK_SECRET` to exactly the same value. Do not reuse the
   dashboard's access key.
3. Paste the updated `gemsentry/google_sync_script.example.gs` into the project and
   deploy a new version. Retire previous unauthenticated deployments.
4. Use **Finalized Tenders → Settings → Test Webhook**. Only the correct secret
   should succeed. GET requests no longer expose data or mutate sheets.

The client authenticates sync, document uploads and connection tests using a
POST body. Config responses omit the stored secret. A blank secret field keeps
the existing value. Vendor routing uses stable IDs; private display names and
sheet IDs are supplied from local config. The tracked script example contains neither.
The optional local deployment copy `gemsentry/google_sync_script.gs` is ignored.

Google's setup references: [Script properties](https://developers.google.com/apps-script/guides/properties)
and [web app deployment](https://developers.google.com/apps-script/guides/web).

## Remote dashboard access

Set `auth_token` in ignored `config/server_config.json`, or set
`GEMSENTRY_AUTH_TOKEN` in the environment of the server process. Start GeMSentry,
then run `scripts/setup_tunnel.ps1 -Check`. It verifies the running server
requires authentication, rejects an anonymous API request, and accepts the
configured access key. `-Quick` performs the same checks before starting a tunnel.
Environment port and token settings override file settings.

Without an access key, the application accepts only loopback requests with a
loopback Host and no forwarding headers. This also prevents the usual public
tunnel route when someone starts cloudflared manually. Keep authentication
configured for named tunnels and services across restarts.

Only the `static/` directory is served as public static content. Documents are
served through explicit routes with authentication when a key is configured.
The application never needs to expose the repository root or `.git` directory.

## Sources and exports

Sources without a working adapter are disabled and cannot be enabled through
the dashboard. Their **NO ADAPTER** explanation stays visible. A runnable
adapter indicates implemented code, not guaranteed current portal availability.

Excel RFP links prefer the existing remote PDF/Drive URL. When only a local
document exists, its link is labeled **Local PDF (this PC)**. Remote URLs still
depend on the portal's availability and sharing permissions.

## Verification

Run `uv run pytest`, `node --test tests/auth.test.cjs tests/google_sync_script.test.cjs`,
and `node --check static/app.js`. PowerShell preflight tests stub HTTP commands
and never start a tunnel. Webhook tests never contact Google or modify sheets.

Removing private values from the current files does not remove them from prior
commits or copies. Git history and hosted deployments have not been rewritten
or changed by this remediation.
