.PHONY: up down logs lint fmt test test-unit eval eval-offline smoke
up:
	docker compose up --build -d
down:
	docker compose down
logs:
	docker compose logs -f api worker
lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
fmt:
	cd backend && uv run ruff format . && uv run ruff check --fix .
test:
	cd backend && uv run pytest
test-unit:
	cd backend && uv run pytest -m "not integration"
eval:
	cd backend && uv run python -m insights_eval.run --llm bedrock
eval-offline:
	cd backend && uv run python -m insights_eval.run --llm stub
smoke:
	./scripts/smoke.sh
