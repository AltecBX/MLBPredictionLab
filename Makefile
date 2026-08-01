# Jerry MLB Prediction Lab
SHELL := /bin/bash
PY    := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
BE    := cd backend && PYTHONPATH=. ../$(PY)
SEASONS ?= 2023,2024,2025,2026

.PHONY: help install migrate bootstrap ingest-reference ingest-schedule ingest-results \
        ingest-history train predict backtest daily check-sources dev api web \
        test test-backend test-frontend e2e lint typecheck build clean

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	 | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-20s\033[0m %s\n",$$1,$$2}'

install:  ## Create the venv and install backend + frontend dependencies
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e "backend[dev]"
	cd frontend && npm install

migrate:  ## Apply database migrations
	cd backend && PYTHONPATH=. ../$(PY) -m alembic upgrade head

bootstrap:  ## Seed a data_source_status row for every category
	$(BE) -m app.cli bootstrap

ingest-reference:  ## Ingest teams and ballparks
	$(BE) -m app.cli ingest-reference

ingest-schedule:  ## Ingest the schedule window (probable pitchers included)
	$(BE) -m app.cli ingest-schedule

ingest-results:  ## Backfill boxscores for final games missing one
	$(BE) -m app.cli ingest-results

ingest-history:  ## Backfill whole seasons: make ingest-history SEASONS=2023,2024
	$(BE) -m app.cli ingest-history --seasons $(SEASONS)

train:  ## Walk-forward fit and register a model version
	$(BE) -m app.cli train

predict:  ## Generate immutable predictions for today's slate
	$(BE) -m app.cli predict

backtest:  ## Full walk-forward evaluation with slices and ablation
	$(BE) -m app.cli backtest

daily:  ## ingest-schedule -> ingest-results -> predict
	$(BE) -m app.cli daily

check-sources:  ## Recompute per-category freshness
	$(BE) -m app.cli check-sources

api:  ## Run the API in development
	cd backend && PYTHONPATH=. ../$(PY) -m uvicorn app.main:app --reload --port 8000

web:  ## Run the web app in development
	cd frontend && npm run dev

dev:  ## Run API and web together
	@$(MAKE) -j2 api web

test: test-backend test-frontend  ## Run every test suite

test-backend:  ## Backend unit, feature, leakage, model and API tests
	cd backend && PYTHONPATH=. ../$(PY) -m pytest -q

test-frontend:  ## Frontend component tests
	cd frontend && npm run test

e2e:  ## End-to-end: daily workflow + iPhone layout contract
	cd frontend && npm run build && npx playwright test

lint:  ## Lint the backend
	cd backend && ../$(PY) -m ruff check app tests

typecheck:  ## Typecheck the frontend
	cd frontend && npm run typecheck

build:  ## Build container images
	docker compose build

clean:  ## Remove caches and build output
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf backend/.pytest_cache frontend/.next frontend/test-results
