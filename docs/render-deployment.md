# Render deployment

The hosted case-study app runs the same FastAPI/React application as the local workspace. It starts with fictional vendors and POs; no local uploads, database or API keys are copied into the image or GitHub repository.

## Blueprint

Create a Render Blueprint from the repository's `render.yaml`. It defines one Docker web service on the **Free** plan in Singapore, a `/healthz` readiness probe, and automatic deployments after repository checks pass. The container binds to `0.0.0.0:$PORT` with one worker. Render's assigned HTTPS origin is trusted explicitly; other browser origins cannot submit changes.

By default, Render generates `LEDGERFLOW_AUTH_PASSWORD` as a secret environment variable and the reviewer username is `reviewer`. Retrieve the generated password from the service's Environment settings and share it privately with the evaluator alongside the live-process URL. The browser prompts for these credentials. In this protected mode, all application pages, APIs, original files, previews, exports and API documentation require authentication. Only `/healthz` is public and returns a minimal readiness status. Missing hosted credentials cause startup to fail unless public demo mode is explicitly enabled.

For direct API creation, set the same environment variables as the Blueprint and generate a random password of at least 20 characters. Do not put secrets in Git, documentation, URLs or the Blueprint's literal values.

## Public demo without a password

Public demo mode is optional; protected access remains the default. To enable it on an existing Render service:

1. Open the service's **Environment** settings and add `LEDGERFLOW_PUBLIC_DEMO=true`.
2. Save the environment changes, then rebuild and deploy the latest GitHub commit so the service includes support for this setting.
3. Open the deployed URL in a new private browser window and confirm that the app opens without a login prompt.

Existing `LEDGERFLOW_AUTH_USERNAME` and `LEDGERFLOW_AUTH_PASSWORD` settings may remain in Render; they are ignored while `LEDGERFLOW_PUBLIC_DEMO=true`. To restore protected access, set the flag to `false`, ensure the password has at least 20 characters, and save and redeploy.

This makes the same shared workspace available to everyone who can reach the app. Visitors can view uploaded documents and edit invoices, vendors and purchase orders. Use fictional samples and keep `ENABLE_OPENROUTER=false` for the public demonstration. If AI extraction is enabled, visitors' extraction requests use the owner's configured OpenRouter key.

These are configuration instructions, not confirmation that any Render service has been changed or verified.

## Data and cost

The Free plan does not have a persistent disk. SQLite records and uploaded files can be lost when the instance restarts, redeploys or spins down. Fictional starter procurement records are recreated after that reset. Use this service for the demonstration, not durable AP operations. The free instance can take about a minute to wake after an idle period; open it before recording or the interview. See [Render's free-plan documentation](https://render.com/docs/free).

For durable history, explicitly choose a paid service and mount a persistent disk at `/app/data`, or implement an external database and file store. No paid resources are included in this Blueprint.

## Optional AI extraction

The hosted default is local parsing/OCR and `ENABLE_OPENROUTER=false`; clean sample PDFs and structured inputs need no key. To use AI extraction, configure `OPENROUTER_API_KEY` only in Render's secret environment settings and set `ENABLE_OPENROUTER=true`. Document extraction then uses that key and incurs its normal model charges, including requests from visitors when public demo mode is enabled. Structured JSON/CSV continue to run locally. Keep provider keys and any reviewer credentials private.

## Verification

1. `/healthz` returns HTTP 200 with only `status: ok`.
2. In protected mode, an unauthenticated request to `/api/runs` returns HTTP 401. With `LEDGERFLOW_PUBLIC_DEMO=true`, it returns HTTP 200 without credentials.
3. Open the app, signing in if protected mode is enabled, and upload `samples/01-clean-invoice.pdf` to a fresh workspace: Approved, USD 1,100.
4. Upload its JSON equivalent: Rejected as a duplicate.
5. Run Settings → Sample runs → Split PO overage: first invoice Approved, second Needs review.
6. Verify the source preview, original download, audit trail and exports.

Both access modes use one shared case-study workspace. Neither provides per-user authorization or multi-tenant isolation.
