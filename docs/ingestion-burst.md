# One-day ingest burst mode (temporary override)

A toggleable override that makes the scheduler pull **aggressively** for one day —
firing the **full pool grid** in fast rotation until roughly the day's request
budget is spent — so the DB fills quickly with real volume for testing. It then
**stops for the day** and reverts to the normal tiered schedule at UTC midnight.

Normal mode: ~324 requests/day across 88 tiered pools (Tier 1 hourly … Tier 3 daily).
Burst mode: up to `INGEST_DAILY_HARD_CAP` (default **1000**) requests on the burst
day, front-loaded at ~5/minute (~3–4h to spend the cap), covering every pool.

## How to trigger it

Set these env vars on the backend service (Railway → the API service → Variables),
then redeploy/restart so the scheduler picks them up:

```
ENABLE_SCHEDULED_INGEST=true    # the scheduler must be running at all
INGEST_BURST_MODE=true          # turn the burst override ON
```

Optional tuning (sensible defaults shown — usually leave as-is):

```
INGEST_BURST_BATCH=5            # pools fired per tick
INGEST_BURST_INTERVAL_SEC=60    # seconds between ticks  → ~5/min
INGEST_DAILY_HARD_CAP=1000      # hard ceiling on scheduler requests per UTC day
```

On start you'll see: `scheduler: BURST MODE active for <date> UTC — full grid …`,
then `scheduler: burst fired 5 pools (N/1000 today)` lines, and finally
`scheduler: burst cap reached (1000/1000 today) — idling …h until midnight`.

## How it reverts

- **Automatically at UTC midnight (one-shot).** The burst applies only on the UTC
  day it was enabled. After midnight it logs `burst day … elapsed — reverting to
  normal tiered schedule` and the normal tiered loop resumes — **even if you leave
  the flag on**.
- **Manually, anytime.** Set `INGEST_BURST_MODE=false` (or remove it) and redeploy.
  If the burst hasn't finished, this stops it immediately; the next scheduled tier
  fire proceeds as normal.

**Recommended:** flip `INGEST_BURST_MODE=false` once the fill looks good, so a later
redeploy on a new day can't kick off another burst.

## Guardrails (won't blow the budget / go dark)

- **Hard daily cap.** The scheduler never emits more than `INGEST_DAILY_HARD_CAP`
  requests in a UTC day. When the cap is hit it idles until midnight.
- **Restart-safe.** The day's spend is seeded from the authoritative
  `data.ingestion_runs` rows (`source='scheduler'`, `created_at >= UTC midnight`),
  so a mid-day restart resumes the count instead of starting over — it can't
  re-spend a fresh 1000.
- **Caveat:** if you leave `INGEST_BURST_MODE=true` **and** the service restarts on
  a *later* day, it will burst that day too (still capped at ≤1000/day). Turn the
  flag off when done to avoid this.

Scope: `INGEST_DAILY_HARD_CAP` counts scheduler requests only. Manual/dev ingests
(`source != 'scheduler'`) are not counted against it.
