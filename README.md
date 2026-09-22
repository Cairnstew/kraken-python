# kraken-python

A thin, friendly wrapper around the [Kraken Spot API](https://docs.kraken.com/api)
— REST **and** WebSocket v2 — focused on the operations a trading/investing
script most often needs. It just needs your API key-pair, then gives you
small, readable functions for market data, balances, and orders — without
touching request signing, pair-name dialects, response envelopes, or
string-vs-decimal conversions yourself.

## Why this exists

The Kraken API is powerful but fiddly: HMAC-SHA512 request signing with
strictly-increasing nonces, three different spellings of every market
(`XXBTZUSD` on public REST, `XBTUSD` on private endpoints, `BTC/USD` on the
WebSocket), single `{error, result}` envelopes, prices as *strings* (never
floats), and a rate limit you must self-throttle. This package is a
*foundation* for projects that expand on Kraken — **your** strategy on top,
this wrapper underneath.

## Features

- **One-line authentication** — `KrakenManager.from_env()` reads
  `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` (and a `.env` file if present).
  No browser flow, no token refreshes: the secret signs every request.
- **Any pair spelling, everywhere** — pass a market as `BTC/USD`, `XBTUSD`,
  or `XXBTZUSD`; the pair catalog resolves it to the right dialect per
  endpoint (public REST, private orders, WebSocket).
- **Market data** — server time, ticker, OHLC candles (all intervals),
  order book, recent trades, and a cached pair catalog.
- **Account & trading** — balances, trade balance, open/closed orders,
  order query, order placement (market/limit/stop variants), cancels
  (single, all, dead-man's-switch), trade history, and the ledger.
- **Real-time** — a sync WebSocket v2 client (`SpotWebSocket`) for ticker /
  book / trade / private channels, plus `watch_ticker` / `blocks_until_tick`
  helpers that turn the feed into change-detecting generators.
- **Typed models** — `Ticker`, `Candle`, `OrderBook`, `Balance`, `Order`,
  `Trade`, `LedgerEntry`, `TradeBalance`, `SpreadPoint`, `Asset`, plus the
  WebSocket v2 message models (`WsTicker`, `WsTrade`, `WsBook`) — dataclasses
  with `from_kraken()` / `from_ws()` conversions, `to_dict()` JSON output,
  and *raw string* prices so no precision is lost.
- **JSON extraction infra** — a resource registry
  (`kraken_api.export.py`) makes every endpoint pull as clean, typed JSON in
  one call: `extract(mgr, "tickers", pairs=[...])`, `extract_many(...)`,
  `extract_snapshot(...)`, plus `write_json` / `write_jsonl` (NDJSON) writers
  and an `iter_ws_jsonl` stream helper for the WebSocket feed.
- **Batteries included** — a tiny CLI (`cli.py` / `kraken-python`), a Nix
  dev shell, an offline test suite (including the official Kraken signing
  vector), and a live-verification script
  (`scripts/verify_live.py`) that exercises the real API before you trust it.

## Setup

Generate an API key-pair at <https://pro.kraken.com/app/settings/api>.
**Public market data needs no key at all**; balance/orders need the key-pair
(create the key with *Query funds* / *Query open orders* / *Create & modify
orders* permissions as needed).

Then:

```bash
cp .env.example .env        # fill in KRAKEN_API_KEY and KRAKEN_API_SECRET
```

### Nix (recommended here)

```bash
nix develop                # drops you into a shell with python + deps
```

As a NixOS module (package on PATH + optional credentials):

```nix
# flake.nix
inputs.kraken-python.url = "github:Cairnstew/kraken-python";
# ...
imports = [ inputs.kraken-python.nixosModules.default ];
services.kraken-python = {
  enable = true;
  credentials = {
    # agenix-style keyfiles — resolved at activation time, never in the
    # store.  Defaults to waiting on agenix-activation.service; override
    # credentials.after for sops-nix or other secret managers.
    apiKeyFile = "/run/secrets/kraken_api_key";
    apiSecretFile = "/run/secrets/kraken_api_secret";
  };
  settings = {
    # optional KRAKEN_* overrides (mirrors .env.example)
    # minInterval = "0.08";
  };
};
# Optional: run your own unit with the same credentials:
#   systemd.services.foo.serviceConfig.EnvironmentFile =
#     [ config.services.kraken-python.envFilePath ];
```

`nix flake check` runs a module check (`.#checks.<system>.kraken-module`)
that evaluates the module against keyfile / plain / envFile configs and
asserts the generated env-writer.

### Or plain pip

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```python
from kraken_api import KrakenManager

mgr = KrakenManager.from_env()          # reads KRAKEN_API_KEY / SECRET

print(mgr.server_time())                # exchange UTC time

# Market data — any pair spelling works
ticker = mgr.ticker("BTC/USD")          #   ws name
ticker = mgr.ticker("XBTUSD")           #   private altname
ticker = mgr.ticker("XXBTZUSD")         #   public REST name
print(ticker.last_price)                # Decimal("27000.1")

candles, last = mgr.ohlc("BTC/USD", interval=60)
for candle in candles[-5:]:
    print(candle)                       # "BTC/USD 2026-.. O=.. C=.. V=.."

book = mgr.order_book("BTC/USD")
print(book.best_bid(), book.best_ask(), book.spread())
```

### Account & orders (requires credentials)

```python
for balance in mgr.balances():
    print(balance)                      # "0.50000000 XXBT"

# Market or limit, by pair spelling of your choice
result = mgr.buy("BTC/USD", volume="0.001", price="27000.00")   # limit
result = mgr.sell("BTC/USD", volume="0.001")                    # market
txid = result["txid"][0]
print(mgr.order(txid))                  # typed Order

mgr.cancel(txid)
mgr.cancel_all()
```

### Real-time (WebSocket v2)

```python
from kraken_api import KrakenManager
from kraken_api.websocket import SpotWebSocket
from kraken_api.watch import watch_ticker

mgr = KrakenManager.from_env()

ws = SpotWebSocket.connect_public()          # wss://ws.kraken.com/v2
ws.subscribe("ticker", ["BTC/USD", "ETH/USD"])
with ws:
    for tick in watch_ticker(ws, "BTC/USD"):
        print(tick["last"], tick["_changes"])
```

Or block in a script:

```python
from kraken_api.watch import blocks_until_tick

tick = blocks_until_tick(ws, "BTC/USD", timeout=30)
```

Private channels (e.g. `balances`, `executions`) need a token and the auth
endpoint:

```python
token = mgr.ws_token()
private = SpotWebSocket(url="wss://ws-auth.kraken.com/v2", token=token)
private.connect()
private.subscribe("balances")
```

### JSON extraction (typed, one clean call)

The extraction registry turns any resource into JSON-ready structures —
always `json.dumps`-able, whatever the endpoint returns:

```python
from kraken_api import KrakenManager
from kraken_api.export import extract, extract_many, extract_snapshot, write_json, write_jsonl

mgr = KrakenManager.from_env()

# One resource...
doc = extract(mgr, "tickers", pairs=["BTC/USD", "ETH/USD"])
doc = extract(mgr, "book", pair="BTC/USD")            # adds best_bid/best_ask/spread
doc = extract(mgr, "trade-balance")                   # typed TradeBalance fields

# ...or a whole envelope: schema + exported_at + resources
feed = extract_many(mgr, ["server-time", "book", "tickers"], pairs=["BTC/USD"])
snap = extract_snapshot(mgr, pairs=["BTC/USD"], include_account=True)

write_json(snap, "snapshot.json")                     # pretty JSON
write_jsonl(iter_ws_jsonl(ws, channel="ticker"), "ticks.jsonl")   # NDJSON stream
```

Registry: `server-time`, `tickers`, `ohlc`, `book`, `trades`, `spread`,
`balance`, `trade-balance`, `orders`, `closed-orders`, `order`, `history`,
`ledger`, `assets`, `pairs` — every extractor returns only strings, ints,
lists and dicts, and the WebSocket `ticker`/`book`/`trade` data items decode
through the typed `Ws*` models too.

### Auth (it's just two environment variables)

| Variable                     | Needed for                        |
|------------------------------|-----------------------------------|
| `KRAKEN_API_KEY`             | private endpoints (optional for public data) |
| `KRAKEN_API_SECRET`          | private endpoints (optional for public data) |

There is no OAuth: every private request is signed with
`HMAC-SHA512(base64decode(secret), path + sha256(nonce + body))` and a
strictly-increasing millisecond nonce — handled for you in
`kraken_api/transport.py`. Public market data works with no credentials.

## CLI

Every command accepts `--json` for clean JSON on stdout (compact or pretty);
`ws` emits one JSON object per line (NDJSON) and can write to a file.

```bash
python cli.py ticker BTC/USD ETH/USD             # market data
python cli.py ticker BTC/USD --json              # same, as JSON
python cli.py ohlc BTC/USD --interval 60
python cli.py book BTC/USD --json                # book + best bid/ask/spread
python cli.py spread BTC/USD
python cli.py pairs --json                       # full catalog records
python cli.py assets
python cli.py snapshot out.json --pair BTC/USD --pair ETH/USD --include-account

# Extraction registry
python cli.py extract book --pair BTC/USD --output book.json
python cli.py extract all --pair BTC/USD --output snapshot.json
python cli.py extract tickers --pair BTC/USD --jsonl --output ticks.jsonl

# Authenticated (requires credentials)
python cli.py balance --json
python cli.py trade-balance
python cli.py orders
python cli.py closed --limit 5
python cli.py order <txid>
python cli.py history --limit 10
python cli.py ledger
python cli.py buy BTC/USD 0.001 27000             # limit buy
python cli.py sell BTC/USD 0.001                  # market sell
python cli.py cancel <txid>
python cli.py cancel-all

# Real-time (NDJSON on stdout by default)
python cli.py ws ticker BTC/USD --timeout 15
python cli.py ws ticker BTC/USD --timeout 15 --output feed.jsonl
```

## Project layout

```
kraken_api/
    auth.py        # credentials -> KrakenClient (env or explicit)
    transport.py   # signing, nonces, HTTP, {error,result} envelope, throttle
    client.py      # KrakenClient — the raw REST surface (spotipy analogue)
    manager.py     # KrakenManager — the friendly high-level facade
    catalog.py     # asset/pair resolution (XXBTZUSD / XBTUSD / BTC/USD)
    models.py      # typed dataclasses (with to_dict() for JSON)
    websocket.py   # SpotWebSocket v2 client (public + private channels)
    watch.py       # watch_ticker / blocks_until_tick real-time helpers
    export.py      # JSON extraction registry: extract / extract_many /
                   #   extract_snapshot, write_json / write_jsonl, iter_ws_jsonl
    utils.py       # decimal + timestamp + batching helpers
    errors.py      # exception hierarchy
    logging_config.py  # structured JSON logging + secret redaction
tests/             # no-network unit tests (incl. the official signing vector)
cli.py             # tiny terminal tool (delegates to the package CLI)
scripts/verify_live.py  # live sanity check against the real API
flake.nix          # Nix dev shell + package
pyproject.toml     # pip-installable package metadata
```

## Extending

Because `KrakenManager` wraps a plain `KrakenClient` which wraps the signed
transport, everything Kraken's API offers is reachable via `mgr.client` (raw
endpoints) or `mgr.client.transport` (HTTP + signing) when you need to go
beyond the friendly surface. Suggested next steps:

- a strategy runner: poll `ticker`/`ohlc`, compute a signal, `buy`/`sell`
- a paper-trading layer: snapshot orders before mutating, replay history
- a stream consumer: keep a live order book / trades record from `ws`
- richer private channels: balances/executions push from `ws-auth`

## Tests

```bash
pytest                              # offline unit tests (fast, no network)
RUN_LIVE=1 python scripts/verify_live.py   # live API sanity check
```

`tests/test_signing.py` reproduces the official API-Sign example from the
Kraken docs — if that ever fails, request signing is broken.

## License

MIT