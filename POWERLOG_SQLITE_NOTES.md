# macOS Powerlog SQLite Notes

## What this is

macOS keeps a battery and power telemetry database at:

```bash
/private/var/db/powerlog/Library/BatteryLife/CurrentPowerlog.PLSQL
```

Despite the `.PLSQL` suffix, this is a normal SQLite database. It is much more useful than `pmset -g log` for battery-history reconstruction because it records battery level while the Mac is both awake and asleep.

This appears to be part of the data family backing System Settings battery history. It is not well documented publicly, so treat table semantics as observed behavior rather than a stable public API.

## Why it matters

`pmset -g log` is event-driven:

- very dense while the machine is sleeping and waking
- sparse while the machine is awake and in ordinary use

`CurrentPowerlog.PLSQL` fills that gap. On this machine, `PLBatteryAgent_EventBackward_BatteryUI` continued recording roughly every 5-6 minutes while awake and also retained samples during sleep.

Observed retention on `2026-05-17`:

- live DB battery-level history: `2026-04-15` to `2026-05-17`
- archived DBs under `Archives/` reached back to `2026-04-13`

## First commands

```bash
DB=/private/var/db/powerlog/Library/BatteryLife/CurrentPowerlog.PLSQL
sqlite3 "$DB"
```

Inside `sqlite3`:

```sql
.tables
.headers on
.mode column
.schema PLBatteryAgent_EventBackward_BatteryUI
```

Latest battery-level rows:

```sql
select
  datetime(timestamp, 'unixepoch', 'localtime') as local_time,
  IsCharging,
  Level
from PLBatteryAgent_EventBackward_BatteryUI
order by timestamp desc
limit 20;
```

History range:

```sql
select
  min(datetime(timestamp, 'unixepoch', 'localtime')) as first_time,
  max(datetime(timestamp, 'unixepoch', 'localtime')) as last_time,
  count(*) as rows
from PLBatteryAgent_EventBackward_BatteryUI;
```

## High-value tables

### `PLBatteryAgent_EventBackward_BatteryUI`

Best starting point for battery-level history.

Observed schema:

```sql
timestamp REAL,
IsCharging INTEGER,
Level REAL
```

Use for:

- battery percentage over time
- charging vs discharging periods
- unified awake + asleep history

### `PLBatteryAgent_EventPoint_BatterySample`

Lower-level periodic samples.

Observed fields include:

```sql
timestamp,
Amperage,
AppleRawMaxCapacity,
CycleCount,
Iss,
Temperature
```

Use for:

- current draw
- thermal context
- lower-level battery telemetry

### `PLBatteryAgent_Aggregate_UILevel`

Hourly aggregates.

Observed fields:

```sql
timestamp,
timeInterval,
Level,
energyConsumed
```

Use for:

- coarse rollups
- hourly discharge summaries
- exploring how System Settings may build aggregate charts

### Related context tables

These are useful when correlating battery behavior with system state:

```text
PLScreenStateAgent_EventForward_ScreenState
PLSleepWakeAgent_EventForward_PowerState
```

## Archives

Archived databases live at:

```bash
/private/var/db/powerlog/Library/BatteryLife/Archives
```

They are gzipped SQLite files such as:

```text
powerlog_2026-05-13_3FE8ADC0.PLSQL.gz
```

To inspect one:

```bash
gunzip -c archive.PLSQL.gz > /tmp/archive.sqlite
sqlite3 /tmp/archive.sqlite
```

## Practical guidance

- Prefer this SQLite DB over `pmset -g log` when reconstructing battery history.
- Keep `pmset` only for complementary sleep/wake explanation, not as the primary battery source.
- Do not assume long-term retention. If multi-month analysis matters, archive extracted summaries or copies yourself.
- Be careful with semantics: table names are suggestive, but this is reverse-engineered behavior, not a documented interface.
