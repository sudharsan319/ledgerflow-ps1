# Submission verification — 10 November 2026

## Checks completed

- Local full suite after the initial hosted-access changes, before the public demo option: **439 Python tests passed**, **22 frontend tests passed**, TypeScript checks and Vite production build passed. One existing Starlette TestClient deprecation warning remains; no test failures.
- Source published to [sudharsan319/ledgerflow-ps1](https://github.com/sudharsan319/ledgerflow-ps1), with the initial push at commit `4f67730`. The [GitHub CI run for `392d652`](https://github.com/sudharsan319/ledgerflow-ps1/actions/runs/42873493390) passed the full suite, production build, API rehearsal and protected Docker startup/PDF/duplicate checks. The Render service itself has not yet been verified.

## Public demo configuration — 11 November 2026

The opt-in `LEDGERFLOW_PUBLIC_DEMO=true` setting removes the password requirement for the shared workspace. Protected access remains the default; setting the flag to `false` restores login and requires a hosted password of at least 20 characters. See the [Render guide](render-deployment.md) for environment changes and deployment steps. Focused hosted-access and API verification passed **41 tests**, including passwordless PDF processing, retained origin/host restrictions, and the protected default. The Render service has not been changed through this workspace.

## Invoice upload deletion — 11 November 2026

- Full local suite: **456 Python tests passed**, **25 frontend tests passed**, and TypeScript/Vite production build passed. One existing Starlette deprecation warning remains.
- Deletion checks cover approved PO amount/quantity release, batch and correction families, unrelated invoice/PO source preservation, stale previews, active batches, duplicate history, re-upload, protected/public access, and source rollback/recovery.
- Local rehearsal server on port 8001 was refreshed with the same data directory. Its two existing runs and source files matched the pre-restart fingerprint; no existing invoices were deleted. The new read-only deletion preview and rebuilt frontend were verified.
- GitHub container checks exercise deletion, released PO commitments and re-upload in both protected and public demo modes. Their remote result is reported by the linked repository Actions run after publishing.

## Earlier local rehearsal evidence — 10 November 2026

The following checks were recorded before the hosted-access changes. They document the local rehearsal and source-package verification at that time; they do not establish that a Render deployment is live.

- Thirteen end-to-end rehearsal checks passed through the real API in temporary databases, with **zero model calls**. See [machine-readable evidence](submission-check.json). The scenarios use real queue execution, extraction, policy evaluation and persistence, not prerecorded decisions.
- The refreshed machine-readable PDF approves for USD 1,100. Submitting its JSON representation is rejected as a duplicate. Original download is byte-identical, and JSON/CSV/HTML exports succeed.
- Both split-invoice examples behave as expected: first approval, second review with cumulative monetary and quantity failures. Ambiguous PO and missing identity/conflicting total each route to review. Both invoices in the CSV batch approve in a fresh workspace.
- The scanned image routes to review; submitting its known generated transcription as an audited correction approves the replacement. The correction retains its link to the original. This verifies the correction flow, not universal OCR or handwriting accuracy.
- All five repeatable sample scenarios produce their expected outcomes. The sample PDF/image layouts were rendered and visually checked.
- The source ZIP was unpacked into a new directory. Its manifest hashes, excluded paths and shell executable permissions were verified, and all thirteen API rehearsal checks passed from that unpacked copy. The packaged start script then installed the locked dependencies, built the frontend and served a healthy API/homepage on a temporary loopback port. The test server was shut down afterward. npm reported zero known vulnerabilities for the locked frontend dependencies at installation time.
- During that rehearsal, the main local server returned healthy status and served the exact rebuilt JavaScript/CSS. Existing workspace table fingerprints remained unchanged: four invoice runs, four POs, four PO uploads, four vendors and eight procurement audit events.
- Browser screenshot and interactive layout QA were unavailable: computer use reported no connected browser. Type checking, server-rendered component tests and live endpoint/asset checks were completed. The five-minute recording should include a final manual walkthrough.

## Reproduce

```bash
./scripts/test.sh
uv run python -m scripts.verify_submission
./scripts/start-demo.sh
```

The rehearsal launcher opens a fresh local workspace at port 8001 and keeps the normal workspace intact. Source archive creation is explicit: `uv run python -m scripts.package_submission`. The archive includes locked dependency manifests, source, tests, built frontend, public/fictional sample inputs and submission documentation. `.env`, API credentials, the user's database, private manual invoice images and generated private POs are excluded.

## Outstanding handoff items

The GitHub source is published. Render deployment is pending, so no verified live-process URL is available yet. The user still needs to record the narration and screen walkthrough in their own voice, upload a video of five minutes or less, and provide its recipient-accessible link. Verify the live app, reviewer access and video sharing before submission. Nothing has been sent to the hiring coordinator.
