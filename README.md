# Unified Wishlist Monitor (Amazon + Throne)

This project monitors **Amazon** and **Throne** wishlists, detects changes, and sends HTML email reports whenever items are:

- Added  
- Removed  
- Have significant price changes  

It provides:

- A unified, normalized item model  
- A shared change-detection engine  
- A single SQLite database for persistent state  
- Card-based HTML email reports with:
  - Thumbnails  
  - Price deltas  
  - Color-coded percentage changes  
  - Logical handling of unavailable prices  
- Per-wishlist polling frequency  
- Per-wishlist recipients with global fallbacks  
- A clean Docker deployment workflow  

---

## Features

### Multi-platform monitoring

- Amazon wishlists (using the mobile site with retry logic and CAPTCHA handling)
- Throne wishlists (parsing Next.js JSON, JSON-LD, and HTML grid layouts)

### HTML email reports

Each report summarizes:

- How many items are added, removed, or changed
- For each added item:
  - Name
  - Price (or "Unavailable")
  - Link
- For each removed item:
  - Name
- For each price change:
  - Before and after prices
  - Percentage change:

    - Increases shown in red, e.g. `(+25.0%)`
    - Decreases shown in green, e.g. `(-30.0%)`

If either price is unavailable, the report shows "Unavailable" without a fake percentage.  
For example:

- `$25.00 -> Unavailable` (in red, no percent)  
- `Unavailable -> $12.00` (neutral, no percent)

### Per-wishlist polling frequency

Each wishlist can specify its own polling interval:

```json
"poll_minutes": 180
```

- If `poll_minutes` is omitted, the wishlist uses the global `POLL_MINUTES` value.
- Values less than 1 are treated as 1 minute.
- In `MODE=once`, all wishlists are processed once regardless of `poll_minutes`.

### Per-wishlist recipients

Each wishlist can define its own email recipients. If it does not:

- The monitor falls back to the global `EMAIL_TO` list.
- If both are missing/empty, the email for that wishlist is skipped and an error is logged.

This allows you to:

- Send notifications for "Rin" wishlists to both you and Rin
- Send notifications for some personal wishlists only to yourself
- Disable emails for specific wishlists by leaving recipients empty and not setting a global default

---

## Project Structure

```text
wishlist_monitor/
  monitor.py
  core/
    logger.py
    models.py
    storage.py
    diff.py
    emailer.py
    report_html.py
  fetchers/
    amazon.py
    throne.py
  config.json
  requirements.txt
  requirements-dev.txt
  Dockerfile
  docker-compose.yml
  README.md
  LICENSE
```

---

## Configuration: config.json

The monitor is configured via a single JSON file, typically mounted at `/data/config.json`.

### Example

```json
{
  "wishlists": [
    {
      "platform": "amazon",
      "name": "Rin Birthday",
      "identifier": "https://www.amazon.com/hz/wishlist/ls/ABC123",
      "recipients": ["you@example.com", "rin@example.com"],
      "poll_minutes": 10
    },
    {
      "platform": "throne",
      "name": "Rin Throne",
      "identifier": "rinusername",
      "recipients": ["you@example.com", "rin@example.com"],
      "poll_minutes": 180
    },
    {
      "platform": "amazon",
      "name": "Morgan Personal Deals",
      "identifier": "https://www.amazon.com/hz/wishlist/ls/XYZ987"
      // Uses global EMAIL_TO and global POLL_MINUTES
    }
  ]
}
```

### Required fields

- `platform`: `"amazon"` or `"throne"`
- `name`: a human-readable label used in logs and emails
- `identifier`:
  - Amazon: wishlist URL or ID
  - Throne: username or full URL

### Optional fields

- `recipients`: array of email addresses for this wishlist  
- `poll_minutes`: integer polling interval in minutes (per wishlist)  
- `enabled`: boolean, defaults to `true` (set to `false` to skip this entry)

If `recipients` is omitted or empty, the monitor uses the global `EMAIL_TO`.  
If both `recipients` and `EMAIL_TO` are effectively empty, no email is sent and a log entry describes the situation.

---

## Environment Variables

Environment variables control email, logging, and global defaults.

### Email

```bash
EMAIL_FROM="wishlist-bot@example.com"
EMAIL_TO="you@example.com"  # comma or semicolon separated; can be empty
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="wishlist-bot@example.com"
SMTP_PASS="your-password"
SMTP_USE_SSL="false"
```

- `EMAIL_TO` is the global fallback recipients list.
- If a wishlist has its own `recipients`, they override `EMAIL_TO`.
- If both are missing or empty for a wishlist, that wishlist will never send mail.

### Email rendering

```bash
EMAIL_THEME="dark"  # "dark" or "light" email template theme
```

Invalid values fall back to `dark`.

### Polling and mode

```bash
POLL_MINUTES="10"      # global default polling interval
MODE="daemon"          # "daemon" or "once"
```

- In `daemon` mode, the monitor runs in a loop:
  - Each wishlist is considered each cycle.
  - For each wishlist, its effective poll interval is:
    - `poll_minutes` from config.json if present and valid (>=1)
    - Otherwise, the global `POLL_MINUTES`
- In `once` mode, all wishlists are processed one time and the program exits.
- Default poll interval is 10 minutes if `POLL_MINUTES` is unset.

### Price change notifications

```bash
PRICE_NOTIFY_THRESHOLD="20"   # percent change needed before price alerts are sent
```

If either the previous or current price is unknown, changes are always included.

### Amazon-specific tuning

```bash
AMAZON_MIN_SPACING="45"        # minimum seconds between any two Amazon wishlist fetches
AMAZON_MAX_PAGES="50"          # maximum number of Amazon wishlist pages to process
AMAZON_MAX_PAGE_RETRIES="3"    # number of retries per page before aborting
PAGE_SLEEP="5"                 # delay after each fetched page (seconds)
FAIL_SLEEP="30"                # delay after non-200 responses (seconds)
CAPTCHA_SLEEP="900"            # backoff when CAPTCHA is encountered (seconds)
```

- `AMAZON_MIN_SPACING` spaces out Amazon wishlist fetches globally to reduce CAPTCHA and rate limiting issues.
- `AMAZON_MAX_PAGES` caps how many Amazon wishlist pages are crawled, preventing infinite pagination loops.
- `PAGE_SLEEP`, `CAPTCHA_SLEEP`, and `FAIL_SLEEP` control per-page delays, CAPTCHA backoff, and error backoff respectively.

### Throne fetcher

```bash
THRONE_USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
THRONE_PROXY_URL=""              # optional HTTP(S) proxy for Throne requests
THRONE_DEBUG_LOG_SAMPLES="true"  # log a few parsed items when debug logging is enabled
```

### Debug output

```bash
DEBUG_DIR="/data/debug_dumps"    # shared directory for HTML debug dumps (Amazon & Throne)
```

- When `LOG_LEVEL=DEBUG`, both Amazon and Throne dump the raw fetched HTML into `DEBUG_DIR`.

### Paths and logging

```bash
CONFIG_PATH="/data/config.json"
DB_PATH="/data/wishlist_state.sqlite3"
LOG_FILE="/data/wishlist_monitor.log"
LOG_LEVEL="INFO"
LOG_TO_FILE="true"
LOG_TO_STDOUT="true"
LOG_MAX_BYTES="2097152"   # rotate logs after ~2MB
LOG_BACKUPS="3"           # number of rotated log files to keep
```

The SQLite database and log file should be on a persistent volume (such as `/data`). Log rotation is controlled by `LOG_MAX_BYTES` and `LOG_BACKUPS`.

---

## Running Locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt

export EMAIL_FROM="wishlist-bot@example.com"
export EMAIL_TO="you@example.com"
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="wishlist-bot@example.com"
export SMTP_PASS="your-password"
export CONFIG_PATH="$(pwd)/config.json"
export DB_PATH="$(pwd)/wishlist_state.sqlite3"
export MODE="once"

python monitor.py
```

This will:

1. Load `config.json`.
2. Run through each configured wishlist once.
3. Send any necessary email notifications.
4. Exit.

---

## Development

### Installing development dependencies

```bash
pip install -r requirements-dev.txt
```

This installs:
- `mypy` for static type checking
- `ruff` for linting and code formatting

### Running type checks

```bash
mypy .
```

### Running linter

```bash
ruff check .
```

To automatically fix issues:

```bash
ruff check --fix .
```

---

## Running with Docker

### Build

```bash
docker build -t wishlist-monitor .
```

### Run

```bash
mkdir -p data
cp config.json data/config.json

docker run -d   --name wishlist-monitor   -v "$(pwd)/data:/data"   -e EMAIL_FROM="wishlist-bot@example.com"   -e EMAIL_TO="you@example.com"   -e SMTP_HOST="smtp.gmail.com"   -e SMTP_PORT="587"   -e SMTP_USER="wishlist-bot@example.com"   -e SMTP_PASS="your-password"   -e POLL_MINUTES="30"   -e MODE="daemon"   wishlist-monitor
```

In this setup:

- `config.json` lives at `./data/config.json` on the host and `/data/config.json` in the container.
- SQLite DB and log file are also stored under `./data`.

---

## Docker Compose Example

```yaml
version: "3.9"

services:
  wishlist-monitor:
    build: .
    restart: unless-stopped
    environment:
      EMAIL_FROM: "wishlist-bot@example.com"
      EMAIL_TO: "you@example.com"
      SMTP_HOST: "smtp.gmail.com"
      SMTP_PORT: "587"
      SMTP_USER: "wishlist-bot@example.com"
      SMTP_PASS: "your-password"
      POLL_MINUTES: "30"
      MODE: "daemon"
      CONFIG_PATH: "/data/config.json"
      DB_PATH: "/data/wishlist_state.sqlite3"
      LOG_FILE: "/data/wishlist_monitor.log"
    volumes:
      - ./data:/data
```

Bring it up with:

```bash
docker compose up -d
```

---

## Database Layout

The monitor uses SQLite for persistence.

### Table: `items`

Tracks the current known state of each item.

Columns include:

- `platform` (amazon, throne)
- `wishlist_id` (identifier from config.json)
- `item_id` (stable item key)
- `name`
- `price_cents` (integer; -1 for unavailable)
- `currency`
- `product_url`
- `image_url`
- `available`
- `first_seen` (UTC timestamp)
- `last_seen` (UTC timestamp)

### Table: `events`

Tracks all changes:

- `added`
- `removed`
- `price_change`

With fields for before/after prices and timestamps.

---

## License

This project is licensed under the MIT License. See `LICENSE` for details.
