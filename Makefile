# Payment Incident Investigator — local development entry points.
# Prerequisites: Docker, Node 20+, Python 3.12+. Copy .env.example to .env first.

PY ?= python3.12
VENV := api/.venv
-include .env
export

.PHONY: setup up down migrate merchant api worker web test test-merchant test-api eval-dev eval-heldout demo

setup: ## install all dependencies
	cd merchant && npm install
	cd web && npm install
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q -e "api[dev]"

up: ## start Postgres and apply migrations
	docker compose up -d --wait
	$(MAKE) migrate

down:
	docker compose down

migrate: ## apply supabase/migrations/*.sql to the local Postgres (or psql $$DATABASE_URL for Supabase)
	for f in supabase/migrations/*.sql; do docker exec -i pii-postgres psql -U pii -d pii -v ON_ERROR_STOP=1 -q < $$f; done

merchant: ## sample checkout + verified webhook collector (port 4100)
	cd merchant && npm run dev

api: ## incident API + investigator + embedded recovery worker (port 8100)
	$(VENV)/bin/uvicorn app.main:app --app-dir api --port $${API_PORT:-8100} --reload

worker: ## optional standalone recovery worker
	cd api && .venv/bin/python -m app.worker

web: ## operations console (port 5173)
	cd web && npm run dev

test: test-merchant test-api

test-merchant: ## needs Postgres
	cd merchant && npm test

test-api: ## needs Postgres + merchant running
	cd api && .venv/bin/python -m pytest -q -p no:logging

eval-dev: ## needs merchant + api running
	$(VENV)/bin/python evals/run_eval.py --set dev

eval-heldout:
	$(VENV)/bin/python evals/run_eval.py --set heldout --seed 7 --repeat 3 --distractors

demo: ## the README demonstration, end to end through the HTTP APIs
	./scripts/demo.sh
