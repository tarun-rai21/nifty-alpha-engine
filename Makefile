.PHONY: up down build logs ps clean \
        shell-api shell-db \
        migrate migrate-down migrate-history seed \
        test lint format

# ── Docker ─────────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

ps:
	docker compose ps

clean:
	docker compose down -v --remove-orphans

# ── Shell access ────────────────────────────────────────────────
shell-api:
	docker compose exec api bash

shell-db:
	docker compose exec postgres psql -U nifty_alpha -d nifty_alpha

# ── Database ────────────────────────────────────────────────────
migrate:
	docker compose exec api alembic upgrade head

migrate-down:
	docker compose exec api alembic downgrade -1

migrate-history:
	docker compose exec api alembic history

seed:
	docker compose exec api python -m scripts.seed_universe

# ── Testing ─────────────────────────────────────────────────────
test:
	docker compose exec api pytest -v

test-cov:
	docker compose exec api pytest -v --cov=app --cov-report=term-missing

# ── Code quality ────────────────────────────────────────────────
lint:
	docker compose exec api black app/ --check
	docker compose exec api isort app/ --check-only

format:
	docker compose exec api black app/
	docker compose exec api isort app/