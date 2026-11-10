# Submission verification — 10 November 2026

## Checks completed

- Full suite: **414 Python tests passed**, **22 frontend tests passed**, TypeScript checks and Vite production build passed. One existing Starlette TestClient deprecation warning remains; no test failures.
- Thirteen end-to-end rehearsal checks passed through the real API in temporary databases, with **zero model calls**. See [machine-readable evidence](submission-check.json). The scenarios use real queue execution, extraction, policy evaluation and persistence, not prerecorded decisions.
- The refreshed machine-readable PDF approves for USD 1,100. Submitting its JSON representation is rejected as a duplicate. Original download is byte-identical, and JSON/CSV/HTML exports succeed.
- Both split-invoice examples behave as expected: first approval, second review with cumulative monetary and quantity failures. Ambiguous PO and missing identity/conflicting total each route to review. Both invoices in the CSV batch approve in a fresh workspace.
- The scanned image routes to review; submitting its known generated transcription as an audited correction approves the replacement. The correction retains its link to the original. This verifies the correction flow, not universal OCR or handwriting accuracy.
- All five repeatable sample scenarios produce their expected outcomes. The sample PDF/image layouts were rendered and visually checked.
- The source ZIP was unpacked into a new directory. Its manifest hashes, excluded paths and shell executable permissions were verified, and all thirteen API rehearsal checks passed from that unpacked copy. The packaged start script then installed the locked dependencies, built the frontend and served a healthy API/homepage on a temporary loopback port. The test server was shut down afterward. npm reported zero known vulnerabilities for the locked frontend dependencies at installation time.
- The main local server returns healthy status and serves the exact rebuilt JavaScript/CSS. Existing workspace table fingerprints remain unchanged: four invoice runs, four POs, four PO uploads, four vendors and eight procurement audit events.
- Browser screenshot and interactive layout QA were unavailable: computer use reported no connected browser. Type checking, server-rendered component tests and live endpoint/asset checks were completed. The five-minute recording should include a final manual walkthrough.

## Reproduce

```bash
./scripts/test.sh
uv run python -m scripts.verify_submission
./scripts/start-demo.sh
```

The rehearsal launcher opens a fresh local workspace at port 8001 and keeps the normal workspace intact. Source archive creation is explicit: `uv run python -m scripts.package_submission`. The archive includes locked dependency manifests, source, tests, built frontend, public/fictional sample inputs and submission documentation. `.env`, API credentials, the user's database, private manual invoice images and generated private POs are excluded.

## Outstanding handoff items

The user will record the narration and screen walkthrough. A recording and recipient-accessible process/video links have not been created. Nothing has been hosted, published or sent to the hiring coordinator. Local acceptance of the source-package format must be confirmed with the coordinator if no live process URL is provided.
