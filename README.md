# BatteryHistory

!(demo.png)

Local macOS battery-history dashboard backed by the private powerlog SQLite database.

```bash
./refresh_battery_history.py
```

Then open `index.html` in a browser.

The generator reads:

```text
/private/var/db/powerlog/Library/BatteryLife/CurrentPowerlog.PLSQL
```

and writes `data.js` for the static page. It shows battery level history, charging intervals, and estimated discharge per 30 minutes.

See `POWERLOG_SQLITE_NOTES.md` for database exploration notes.
