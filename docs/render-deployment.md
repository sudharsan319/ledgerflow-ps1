# Render deployment

The hosted case-study app runs the same FastAPI/React application as the local workspace. It starts with fictional vendors and POs; no local uploads, database or API keys are copied into the image or GitHub repository.

## Blueprint

Create a Render Blueprint from the repository's `render.yaml`. It defines one Docker web service on the **Free** plan in Singapore, a `/healthz` readiness probe, and automatic deployments after repository checks pass. The container binds to `0.0.0.0:$PORT` with one worker. Render's assigned HTTPS origin is trusted explicitly; other browser origins cannot submit changes.

Render generates `LEDGERFLOW_AUTH_PASSWORD` as a secret environment variable. The reviewer username is `reviewer`. Retrieve the generated password from the service's Environment settings and share it privately with the evaluator alongside the live-process URL. The browser prompts for these credentials. All application pages, APIs, original files, previews, exports and API documentation require authentication. Only `/healthz` is public and returns a minimal readiness status. Missing hosted credentials cause startup to fail rather than expose the workspace.

For direct API creation, set the same environment variables as the Blueprint and generate a random password of at least 20 characters. Do not put secrets in Git, documentation, URLs or the Blueprint's literal values.

## Data and cost

The Free plan does not have a persistent disk. SQLite records and uploaded files can be lost when the instance restarts, redeploys or spins down. Fictional starter procurement records are recreated after that reset. Use this service for the demonstration, not durable AP operations. The free instance can take about a minute to wake after an idle period; open it before recording or the interview. See [Render's free-plan documentation](https://render.com/docs/free).

For durable history, explicitly choose a paid service and mount a persistent disk at `/app/data`, or implement an external database and file store. No paid resources are included in this Blueprint.

## Optional AI extraction

The hosted default is local parsing/OCR and `ENABLE_OPENROUTER=false`; clean sample PDFs and structured inputs need no key. To use AI extraction, configure `OPENROUTER_API_KEY` only in Render's secret environment settings and set `ENABLE_OPENROUTER=true`. Model charges then apply to authenticated document extraction. Structured JSON/CSV continue to run locally. Keep access credentials private.

## Verification

1. `/healthz` returns HTTP 200 with only `status: ok`.
2. An unauthenticated request to `/api/runs` returns HTTP 401.
3. Sign in and upload `samples/01-clean-invoice.pdf` to a fresh workspace: Approved, USD 1,100.
4. Upload its JSON equivalent: Rejected as a duplicate.
5. Run Settings → Sample runs → Split PO overage: first invoice Approved, second Needs review.
6. Verify the source preview, original download, audit trail and exports.

This is shared reviewer access for a single case-study workspace. It is not per-user authorization, multi-tenant isolation or a production accounting deployment.
