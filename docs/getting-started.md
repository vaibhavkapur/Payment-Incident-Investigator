# Getting Started

[Documentation](index.md) · [Getting Started](getting-started.md) · [Repository README](../README.md)

## Clone

```bash
git clone https://github.com/vaibhavkapur/Payment-Incident-Investigator.git
cd Payment-Incident-Investigator
```

## Local Setup

Prerequisites: Docker, Node.js 20.19+ or 22.12+, Python 3.12+. Everything below was verified on macOS.

```bash
cp .env.example .env            # defaults work for local development
make setup                      # npm install (merchant, web) + Python venv for the API
make up                         # Postgres on :54329 + migrations + seed (two organizations, three operators)

# in three terminals
make merchant                   # http://localhost:4100  sample checkout + webhook collector (simulated provider)
make api                        # http://localhost:8100  incident API, investigator, embedded recovery worker
make web                        # http://localhost:5173  operations console

make demo                       # the end-to-end demonstration, through the HTTP APIs
make test                       # merchant tests + API integration tests (needs Postgres and the merchant running)
make eval-dev                   # evaluation on the development set (needs merchant + api running)
make eval-heldout               # held-out set, 3 repeats, with distractor orders
```

Seeded operators (dev auth mode, selected with the `X-User-Id` header or the console's operator switcher):

| Organization | Operator | Role |
|---|---|---|
| Northwind Outfitters | Nia | analyst (can investigate, cannot approve) |
| Northwind Outfitters | Omar | approver |
| Acme Tickets | Ada | admin |

`make setup` defaults to `python3.12`; if a different compatible executable is installed, run `make setup PY=python3.13` (substitute its actual name).

The local PostgreSQL container is named `pii-postgres` and uses port `54329`. `make down` stops the stack while retaining the Docker data volume. Use the seeded database only for development and tests.

See [Configuration](configuration.md) for optional services and [Testing](testing.md) for the prerequisites of each check.
