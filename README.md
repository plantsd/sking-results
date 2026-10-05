# SattaKing results feed

This public repository contains the SattaKing monthly JSON archive, chart-route metadata, and the scheduled updater used by Hari Haryana. It contains only public source data and updater code—never Supabase service-role credentials or other private keys.

## Scheduled behavior

- GitHub Actions runs every 30 minutes using a standard GitHub-hosted runner.
- The updater reads the current month only. It requests a chart page only when at least one due cell for that game is absent or `XX`.
- It checks already-verified values only for today and yesterday, to detect recent source corrections. Older verified values and prior months are not rechecked.
- A numeric source result is accepted only as a two-character value (`00` through `99`). `XX`, blanks, dashes, and malformed cells never settle a result. A blank or `XX` response does not erase a previously accepted numeric value.
- The source archive preserves `00` and leading zeroes as strings. The platform maps `00` to its existing internal result value `100`.
- If the source page changes format or a route/target column cannot be validated, the updater fails closed for that chart; it does not infer a value.

## Required GitHub Actions settings

In **Settings → Secrets and variables → Actions**, set:

- Repository variable `SATTAKING_INGEST_URL` to the Hari Haryana Edge Function endpoint ending in `/functions/v1/hh-api/sattaking-ingest`.
- Repository secret `SATTAKING_INGEST_SECRET` to the dedicated ingest token provisioned on the Supabase Edge Function.

The workflow uses its built-in `GITHUB_TOKEN` only to commit JSON changes to this repository. Do not add the Supabase service-role key here.

## Files

- `data/sattaking/manifest.json` — current archive index and game catalog.
- `data/sattaking/months/YYYY-MM.json` — monthly two-digit result values and `XX` pending markers.
- `data/sattaking/chart-routes.json` — validated source chart routes for the 120-game catalog.
- `scripts/update_sattaking.py` — current-month poller and secure result submission.
- `scripts/build_routes.py` — manual route-map refresh; it parses chart links only.

SattaKing Fast is a third-party source, not a government or regulatory authority; these records are not a basis for resolving disputes.
