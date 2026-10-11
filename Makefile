.PHONY: install lint test eval run api console up down mcp

install:      ## Install dev dependencies
	pip install -r requirements-dev.txt
lint:         ## Static checks
	ruff check rca tests
test:         ## Unit, workflow and API tests
	pytest -q
eval:         ## Labelled evaluation with quality gate
	python -m rca.eval.run --gate
api:          ## Run the API locally (lite mode: SQLite + NetworkX)
	uvicorn rca.api:app --reload
console:      ## Run the operations console locally
	streamlit run streamlit_app.py
mcp:          ## Run the MCP server over stdio
	python -m rca.mcp_server
up:           ## Full stack with PostgreSQL and Neo4j
	cp -n .env.example .env || true; docker compose up --build -d
down:
	docker compose down
