# Apple iPhone Stock Watcher (UAE)

## What this does

Every five minutes, a GitHub Actions workflow polls Apple UAE's store-pickup
availability endpoint for the iPhone(s) you specify and pushes an
[ntfy](https://ntfy.sh) notification to your phone the moment one becomes
collectable at a watched store. There is no UI and no automated purchasing:
the notification links straight to Apple's own buy page, and you take it
from there by hand.

## Quick start

1. Fork this repository.
2. Pick an ntfy topic (see [ntfy setup](#ntfy-setup) below for how to choose
   one that can't be guessed) and set it as a repository secret:
   ```bash
   gh secret set NTFY_TOPIC --body "<your-topic>"
   ```
   or, in the GitHub UI, **Settings -> Secrets and variables -> Actions ->
   New repository secret**.
3. Enable Actions on your fork (the Actions tab prompts you the first time).
   The `check-stock` workflow then polls every five minutes on its own.
4. Edit `watches.yml` to describe the phone(s) and store(s) you want (see
   [Configuring watches](#configuring-watches) below), and validate the
   change with `python -m applewatch --dry-run` (see
   [Validating a change](#validating-a-change)) before committing it.
5. **Mandatory: verify delivery end to end, not just that the secret is
   set.** ntfy silently auto-creates a topic the first time anything posts
   to it, so a typo'd `NTFY_TOPIC` secret still returns success from ntfy,
   every scheduled run still shows green in Actions, and you get permanent
   silence with nothing anywhere telling you it is wrong. Prove the whole
   path works, from this repo to your phone, using the exact topic value
   you just set as the secret:
   ```bash
   export NTFY_TOPIC="<your-topic>"
   python -m applewatch --force-notify
   ```
   This resolves your real `watches.yml` down to its first watched pair and
   sends one real alert for it. You should feel your phone buzz within a
   few seconds. If nothing arrives, recheck the topic spelling in both
   places (the exported value and the repository secret) before trusting
   any run to actually notify you.

## Configuring watches

`watches.yml` has three optional top-level settings and one required list:

| Key | Default | Meaning |
|---|---|---|
| `ntfy_topic_env` | `NTFY_TOPIC` | Name of the environment variable holding the ntfy topic. You will not normally need to change this. |
| `poll_location` | `Abu Dhabi` | The `location` string sent to Apple's endpoint, the same field the store-locator search box takes. With only five stores in the whole country, it does not filter which stores come back in the response; `stores`/`cities`/`emirates` on each watch do that filtering locally, after the fetch. |
| `reminder_minutes` | `30` | How often, in minutes, a "still in stock" reminder repeats while a watched pair stays available. See [How alerts behave](#how-alerts-behave). |
| `watches` | *(none)* | The list of watch rules below. At least one is required. |

Each entry under `watches` is one rule:

| Field | Type | Omitted means | Matches against |
|---|---|---|---|
| `name` | string | required | label shown in notifications |
| `model` | string | any model | `iphone18pro`, `iphone18promax` |
| `capacity` | list | any capacity | `256gb`, `512gb`, `1tb`, `2tb` |
| `color` | list | any colour | `burgundy`, `black`, `silver`, `glacier` |
| `stores` | list | see below | store number or exact name |
| `cities` | list | see below | `Abu Dhabi`, `Dubai`, `Al Ain` |
| `emirates` | list | see below | `Abu Dhabi`, `Dubai` |
| `priority` | enum | `urgent` | `urgent`, `high`, `default`, `low` |

There is deliberately no `screen` field. `model` already implies screen
size (`iphone18pro` is 6.3", `iphone18promax` is 6.9"), so a separate
`screen` filter would only ever be redundant with `model` or silently
contradict it. Config loading rejects a `screen` key outright rather than
accept it and ignore it.

Every value is validated against the live catalog and the store list at
startup. A typo (`burgandy` instead of `burgundy`) is a hard config error
that stops the run, not a rule that silently matches nothing, because a
watch that matches nothing and a watch that matches something with no
current stock look identical from the outside.

### The five UAE stores

| Number | Name | City | Emirate |
|---|---|---|---|
| R706 | Al Maryah Island | Abu Dhabi | Abu Dhabi |
| R595 | Yas Mall | Abu Dhabi | Abu Dhabi |
| R596 | Mall of the Emirates | Dubai | Dubai |
| R597 | Dubai Mall | Dubai | Dubai |
| R785 | Al Jimi Mall | Al Ain | Abu Dhabi |

### Matching rules

- Fields **AND** together: a watch with both `model` and `capacity` set
  requires both to match.
- Values within one field **OR** together: `capacity: [512gb, 1tb]` matches
  either.
- An omitted field is a wildcard: no `color` means every colour.
- `stores`, `cities` and `emirates` are three ways of selecting the same
  thing (which stores this watch applies to) and are **mutually
  exclusive**. Setting more than one on the same watch is a config error at
  startup, not a union or an intersection. Set at most one; omitting all
  three watches every store.

### The Al Jimi worked example

This is the one detail in this file that will cost you real driving
distance if you skim past it. Apple's own data files **Al Jimi Mall (R785)**
under the city **"Al Ain"**, even though the mall itself sits inside the
**Abu Dhabi emirate**. `city` and `emirate` are not the same axis, and
Apple's `city` string is the one that gets compared literally, so the two
selectors genuinely disagree here:

- `cities: [Abu Dhabi]` -> **R706 Al Maryah Island** and **R595 Yas Mall**
  only. Al Jimi Mall is filed under the city Al Ain, so a city filter for
  "Abu Dhabi" silently excludes it.
- `emirates: [Abu Dhabi]` -> the same two stores, **plus R785 Al Jimi
  Mall**, because `emirate` is the field this repository maintains
  specifically to capture that Al Jimi Mall is administratively in Abu
  Dhabi despite Apple's city label. (Apple's payload sends an empty
  `state` field for every UAE store, so `emirate` cannot be read from
  Apple at all; it is hand-maintained in `stores.yml`.)

Choose deliberately, not by habit. Al Jimi Mall is roughly 133 km from
central Abu Dhabi, farther than Dubai Mall is. If you only ever wanted the
two Abu Dhabi city stores, `emirates: [Abu Dhabi]` will alert you to a
pickup that is not the convenient one you expected. If you were relying on
"Abu Dhabi" to also cover Al Jimi, `cities: [Abu Dhabi]` will quietly never
alert you to it at all, and nothing about that failure is visible unless
you already know this paragraph.

### Four recipes

**The strict default:** one exact phone, one city.

```yaml
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30

watches:
  - name: "Burgundy 512GB Pro Max, Abu Dhabi city"
    model: iphone18promax
    capacity: [512gb]
    color: [burgundy]
    cities: [Abu Dhabi]
    priority: urgent
```

**Any colour, one store:** drop `color` entirely rather than list every
colour; an omitted field is already a wildcard.

```yaml
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30

watches:
  - name: "Any colour 256GB Pro, Dubai Mall"
    model: iphone18pro
    capacity: [256gb]
    stores: [R597]
    priority: high
```

**A whole emirate**, including Al Jimi Mall:

```yaml
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30

watches:
  - name: "Any Pro Max, Abu Dhabi emirate (incl. Al Jimi Mall)"
    model: iphone18promax
    emirates: [Abu Dhabi]
    priority: urgent
```

**Every 512GB, nationwide:** omit `model`, `color` and every location
selector; only `capacity` constrains it.

```yaml
ntfy_topic_env: NTFY_TOPIC
poll_location: Abu Dhabi
reminder_minutes: 30

watches:
  - name: "Every 512GB, nationwide"
    capacity: [512gb]
    priority: default
```

## Validating a change

```bash
pip install -e .          # once, to make `python -m applewatch` importable
python -m applewatch --dry-run
```

`--dry-run` fetches live availability, resolves every watch to its SKUs and
store pairs, prints how many of each it is watching, prints any alert it
would have sent (`[would send] <title>` followed by the body), and then
exits, **without sending anything to ntfy and without writing `state.json`**.
Run it after any `watches.yml` edit, before committing:

```
watching 1 SKU(s) across 2 store pair(s)
no changes
```

`--dry-run` and `--force-notify` are mutually exclusive: `--dry-run`
promises nothing is sent, `--force-notify` exists only to send something
real, so combining them is refused rather than silently picking one. That
combination, and any other configuration problem, exits with status `2`.

Exit codes, for scripting or for reading a failed Actions run:

| Code | Meaning |
|---|---|
| `0` | Ran to completion, whether or not anything changed. |
| `1` | The request to Apple failed outright, or Apple's response stopped reporting one or more watched pairs, partial miss or total (see [Troubleshooting](#troubleshooting)). Also used if a real ntfy delivery fails mid-run. |
| `2` | Configuration error, `NTFY_TOPIC` not set, no watch matched any SKU or store, or `--dry-run` combined with `--force-notify`. |

## ntfy setup

1. Install [ntfy](https://apps.apple.com/app/ntfy/id1625396347) from the
   App Store.
2. Choose an unguessable topic, for example `apple-ae-burgundy-k7f2q9x`.
   Topics on `ntfy.sh` are just URL paths: anyone who knows (or guesses) the
   name can read and post to it, so a long random suffix is the only thing
   protecting yours.
3. In the app, **+ -> Subscribe to topic**, enter the name, and leave the
   server as `ntfy.sh`.
4. Allow notifications when iOS asks.
5. In **Settings -> Notifications -> ntfy**, enable **Time Sensitive
   Notifications**.
6. Add ntfy to the allowed apps for your sleep Focus (**Settings -> Focus
   -> Sleep -> Apps**). Do both 5 and 6: iOS Focus modes silence everything
   by default except apps you explicitly allow, and a restock at 4am is
   exactly when this project is supposed to work. Skip either step and a
   4am alert gets suppressed by the phone before you ever see it, which
   defeats the entire point of running this.
7. Set the secret on your fork:
   ```bash
   gh secret set NTFY_TOPIC --body "<topic>"
   ```
8. Verify delivery end to end:
   ```bash
   curl -d "test" ntfy.sh/<topic>
   ```
   You should feel your phone buzz within a few seconds.

## How alerts behave

State persists across runs in `state.json` (committed back to the repo by
the workflow), so alerts fire on *transitions*, not on every poll that
happens to see stock:

- **Flips to in stock:** fires once, at the watch's own `priority` (default
  `urgent`; if more than one watch covers the same pair, the highest of
  their priorities is used).
- **Still in stock:** a quieter reminder repeats every `reminder_minutes`
  (default 30) for as long as it stays available, always at `default`
  priority regardless of the watch's own setting. The reminder body reports
  total time in stock so far, not time since the last reminder.
- **Goes out of stock again:** fires once, at `low` priority, reporting how
  long the window lasted.

Tapping any of these opens the phone's page on `apple.com/ae`.

## Polling cadence, and the local fast lane

The workflow asks for `*/5 * * * *`, but GitHub throttles scheduled
workflows hard. Measured on this repo over seven hours: **three runs, an
average gap of 3 hours 42 minutes, roughly 3% of the configured rate.**
GitHub queues cron best-effort and deprioritises it, and the `[skip ci]`
state commits do not count as activity that would help.

That blind spot is wide enough to straddle a restock window entirely, so
the cloud cron alone is a safety net rather than a fast lane.

`scripts/local_check.ps1` closes the gap by running the same checker
locally on a short interval while your machine is awake. Register it once:

```powershell
# NTFY_TOPIC must exist as a USER environment variable (never in this repo)
setx NTFY_TOPIC "your-topic-here"

powershell -ExecutionPolicy Bypass -File scripts\register_local_task.ps1
# optional: -IntervalMinutes 2
```

Useful afterwards:

```powershell
Get-ScheduledTaskInfo -TaskName AppleStockWatcher   # last result, next run
Get-Content "$env:TEMP\applewatch-local.log" -Tail 20
Start-ScheduledTask   -TaskName AppleStockWatcher   # run immediately
Unregister-ScheduledTask -TaskName AppleStockWatcher -Confirm:$false
```

Both runners share `state.json` through git, so whichever sees a
transition first records it and the other stays quiet. The local script
treats every git step as best-effort and only the check itself as
load-bearing, so a sync failure degrades to a possible duplicate alert,
never a missed one.

The task runs only while you are logged on (a locked session counts).
Running it otherwise would require storing your account password with the
task, which is not worth it for a fast lane that the cloud cron already
backstops.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No alerts ever arrive, even when you know stock changed | iOS Focus is silencing ntfy | Check the Time Sensitive and sleep-Focus allowlist steps above, then confirm the pipe still works with `python -m applewatch --force-notify` (sends one real alert for the first watched pair and exits). |
| Run refuses to start with `config error:` followed by `unknown color [...]` (or `model`/`store`/`city`/`emirate`/`priority`) | Typo in `watches.yml` | Every valid value is enumerated in the field table above; compare byte-for-byte, including case. |
| `apple request failed: Apple did not report N of M watched pair(s)...` and exit code `1` | Apple rotated one or more part numbers, or otherwise stopped reporting a watched pair. Any missing pair, partial or total, is what a rotated part number or a broken response shape looks like, not what "nobody has stock" looks like, so this is always treated as a broken checker, never as a quiet, permanent absence of stock | Run `python scripts/refresh_catalog.py` to regenerate `catalog.json`, `git diff catalog.json` to see what changed, then `--dry-run` to confirm the watch resolves to the new SKU. Note that state keys are part-number based, so if a pair was already in stock under the old part number, it will re-alert once after the rotation: the new part number starts with no history of its own. |
| The scheduled workflow stopped running | GitHub disables a repository's scheduled workflows after 60 days with no commits | Re-enable it from the Actions tab, or push any commit. |
| `ntfy POST failed (<SomeExceptionType>)` with no further detail, in stdout/stderr/Actions logs | Deliberate: ntfy failures are reported by exception type only, never by message. The underlying `requests` error embeds the full `https://ntfy.sh/<topic>` URL in its text, and this repository's Actions logs are public, so letting that string reach a log would leak the one secret this project has. | Don't go looking for more detail in the logs, there isn't any, by design. Confirm the topic still exists with `curl -d "test" ntfy.sh/<topic>`, and confirm `NTFY_TOPIC` is still set as a secret. |

A repeat "stock checker is broken" health alert (for the missing-pair row
above, or for any other unexpected failure inside a run) is itself
rate-limited to once per 6 hours, so an extended outage sends one push, not
one every five minutes.

## How it works

One `GET` to `https://www.apple.com/ae/shop/retail/pickup-message` returns
every UAE store's availability for the requested part numbers in a single
response, so store filtering is entirely a local concern; there is no
per-store request. A store counts as in stock when its `pickupDisplay`
value is anything other than `unavailable` or `ineligible`, a denylist
rather than a check for a known positive string, because the positive
value Apple actually sends has never been directly observed; see
[design spec 3.4](docs/superpowers/specs/2026-09-21-apple-iphone-stock-watcher-design.md#34-observed-pickupdisplay-states-and-why-the-match-rule-is-a-denylist)
for why matching this way was chosen over hard-coding a guess.

## Scope

This reads a public availability endpoint, the same one Apple's own buy
page calls when you check pickup, at roughly the rate a person manually
refreshing that page every few minutes would produce. It does not log in,
does not touch a cart, and does not automate purchasing in any way: the
notification's tap target is Apple's own buy page, and everything from
there on is you, by hand.
