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

This makes the same shared workspace available to everyone who can reach the app. Visitors can view uploaded documents, edit invoices, vendors and purchase orders, and delete invoice uploads after confirmation. Use fictional samples and keep `ENABLE_OPENROUTER=false` for the public demonstration. If AI extraction is enabled, visitors' extraction requests use the owner's configured OpenRouter key.

These are configuration instructions, not confirmation that any Render service has been changed or verified.

## Persistent uploads on Render Free

Render Free erases its local filesystem after a restart, redeploy or idle shutdown. Without `DATABASE_URL`, this includes the SQLite database, invoice originals and uploaded PO files. Fictional starter records then reappear in a fresh database. See [Render's free-plan documentation](https://render.com/docs/free).

To retain records and source documents on a Free web service, use an external PostgreSQL database. A Neon Free project is suitable for a small demonstration; its database quota also counts the uploaded document bytes.

1. Create a Neon project and copy its **pooled PostgreSQL connection string**, including SSL settings.
2. In the Render service's **Environment** page, set `DATABASE_URL` to that string as a secret. Never add a real connection string to GitHub or `render.yaml`.
3. Redeploy. In the app's **Settings**, verify **Upload storage: External database**. The authenticated `/api/health` endpoint also reports `storage_mode: postgres`.
4. Upload a new invoice and PO, record their identifiers, redeploy once more, and check that both history entries and source previews remain available.

With `DATABASE_URL`, records and original invoice/PO bytes live in PostgreSQL. Files under `/app/data` are disposable preview caches. Data already lost from the old temporary Render instance cannot be recovered by adding the URL; reupload the files or restore an independent backup. Back up important data separately. If **Upload storage** still says **Local files**, uploads can still disappear.

The alternative is a paid Render web service with a persistent disk mounted at `/app/data`, which retains both local SQLite and sources. No paid resources are included in this Blueprint. The Free instance can take about a minute to wake after an idle period.

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
