# Flight Monitor

Python monitor for TLV -> ICN flight prices.

Default route:

- route: `TLV` to `ICN`
- trip type: `round-trip`
- departure: `2026-09-15`
- return: `2026-09-21`
- passengers: `1` adult
- currency: `USD`
- Telegram alert threshold: `1000 USD`

The default source is `kiwi_browser`: a headless browser opens the public Kiwi.com search page and extracts the visible price. It does not use Google Flights or airline websites, and it does not require API keys.

This is less stable than an official API. If Kiwi changes the page or shows anti-bot protection, extraction can fail.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.env .env
```

On this Mac the project is configured to use system Google Chrome:

```dotenv
BROWSER_EXECUTABLE_PATH=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

Fill in Telegram values in `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

No flight API key is needed while:

```dotenv
PRICE_SOURCE=kiwi_browser
```

If Kiwi renders prices in Israeli shekels in headless mode, the monitor converts them to USD with:

```dotenv
ILS_PER_USD=3.60
```

Adjust that value occasionally if you want tighter USD estimates.

## Run One Check

```bash
python -m flight_monitor --once
```

The first successful check for a route writes a baseline price to `prices.csv`.
With `NOTIFY_EVERY_CHECK=true`, Telegram receives a status message after every check, even when the price did not change.
A special drop alert is still sent when the new minimum price is below the previous saved price and below the route threshold.

Send only a Telegram test message:

```bash
python -m flight_monitor --test-telegram
```

## Run Continuously

```bash
python -m flight_monitor
```

Default interval:

```dotenv
CHECK_INTERVAL_SECONDS=7200
```

## Cron

Run one check every 2 hours:

```cron
0 */2 * * * cd /Users/kasimov/Documents/Codex/2026-06-17/flight-monitor && .venv/bin/python -m flight_monitor --once
```

Compatibility wrapper:

```cron
0 */2 * * * cd /Users/kasimov/Documents/Codex/2026-06-17/flight-monitor && .venv/bin/python flight_monitor.py --once
```

## GitHub Actions

The repository includes `.github/workflows/flight-monitor.yml`.
It runs automatically every 2 hours:

```cron
0 */2 * * *
```

Important: GitHub Actions works only after this folder is pushed to a GitHub repository. A local folder on the Mac does not run on GitHub by itself.

Scheduled GitHub cron uses UTC. With Israel summer time, `0 */2 * * *` runs around `03:00`, `05:00`, `07:00`, `09:00`, and so on. GitHub can also delay scheduled workflows by a few minutes.

Start it manually from GitHub to test immediately:

1. Open the repository on GitHub.
2. Go to `Actions`.
3. Select `Flight Monitor`.
4. Click `Run workflow`.
5. To test Telegram only, enable `telegram_test`.

Add secrets in GitHub:

1. Open `Settings` -> `Secrets and variables` -> `Actions`.
2. Click `New repository secret`.
3. Add these secrets:

```text
AMADEUS_CLIENT_ID
AMADEUS_CLIENT_SECRET
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

The current project runs with `PRICE_SOURCE=kiwi_browser`, so it does not use the Amadeus values right now. They are still passed from GitHub Secrets, not stored in the repository.

GitHub Actions also restores and saves `prices.csv` with the Actions cache. The first run creates the baseline price; later scheduled runs can compare against that saved price and send Telegram only when the price drops.
Because `NOTIFY_EVERY_CHECK=true` is set in the workflow, every scheduled run also sends a Telegram status message after the check.

If a scheduled run fails before the Python monitor can send its own message, the workflow sends a Russian failure message to Telegram with a link to the GitHub Actions logs.

If no Telegram message arrives after 2 hours, check this in order:

1. The project is pushed to GitHub, including `.github/workflows/flight-monitor.yml`.
2. `Actions` are enabled for the repository.
3. `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` exist in `Settings` -> `Secrets and variables` -> `Actions`.
4. The workflow has at least one run in the `Actions` tab.
5. The run logs do not show missing secrets or Kiwi browser extraction errors.

## Routes

Routes are configured in `routes.json`.

```json
{
  "routes": [
    {
      "id": "tlv-icn-2026-09",
      "name": "Tel Aviv to Seoul, September 2026",
      "enabled": true,
      "trip_type": "round-trip",
      "origin": "TLV",
      "destination": "ICN",
      "departure_date": "2026-09-15",
      "return_date": "2026-09-21",
      "adults": 1,
      "currency": "USD",
      "alert_threshold": 1000,
      "max_results": 20
    }
  ]
}
```

For `one-way`, omit `return_date`. For `round-trip`, set `return_date`.

Optional route field:

- `search_url`: custom Kiwi search URL. If omitted, the project builds one from the route details.

## Optional Kiwi API Mode

If you later get a Kiwi Tequila API key, you can switch to the official API:

```dotenv
PRICE_SOURCE=kiwi_api
KIWI_API_KEY=your_kiwi_tequila_api_key
```

## Price History

Each successful route check appends the found minimum ticket to `prices.csv`.

The CSV includes route details, price, currency, flight details when available, stops, and the Kiwi search link.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `PRICE_SOURCE` | `kiwi_browser` | `kiwi_browser` without API keys, or `kiwi_api` with Tequila |
| `KIWI_API_KEY` | empty | Optional, required only for `PRICE_SOURCE=kiwi_api` |
| `KIWI_BASE_URL` | `https://api.tequila.kiwi.com` | Kiwi Tequila API base URL |
| `BROWSER_EXECUTABLE_PATH` | Chrome path | Browser executable for Playwright |
| `BROWSER_HEADLESS` | `true` | Run browser without a visible window |
| `BROWSER_TIMEOUT_SECONDS` | `60` | Browser wait timeout |
| `ILS_PER_USD` | `3.60` | Manual conversion rate when Kiwi renders ILS in browser mode |
| `TELEGRAM_BOT_TOKEN` | required | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | required | Chat or user ID for alerts |
| `ROUTES_FILE` | `routes.json` | Routes configuration file |
| `PRICES_CSV` | `prices.csv` | Price history CSV file |
| `ADULTS` | `1` | Default number of adults for route config |
| `CURRENCY` | `USD` | Default currency for routes that omit it |
| `ALERT_THRESHOLD` | `1000` | Default Telegram alert threshold |
| `MAX_RESULTS` | `20` | Maximum offers requested in API mode |
| `NOTIFY_EVERY_CHECK` | `true` | Send a Telegram status message after every check |
| `CHECK_INTERVAL_SECONDS` | `7200` | Delay between checks in monitoring mode |
| `REQUEST_TIMEOUT_SECONDS` | `30` | HTTP request timeout for API/Telegram |

## Notes

- `kiwi_browser` is practical but brittle because it reads the rendered public page.
- The extraction prefers visible labels like `Cheapest` / `Самый дешевый`.
- Telegram messages include price, previous price, dates, departure time when extracted, stops when extracted, and a Kiwi link.
- If the browser sees ILS while the route is configured as USD, the alert shows the estimated USD price plus the original Kiwi ILS price.
