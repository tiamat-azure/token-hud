UV ?= uv

.DEFAULT_GOAL := help
.PHONY: help install dev sync run test lint format check build clean distclean

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the environment with runtime dependencies only
	$(UV) sync --no-dev

dev sync: ## Create the environment with dev dependencies
	$(UV) sync

run: ## Launch the HUD
	$(UV) run python -m token_hud

test: ## Run the test suite
	$(UV) run pytest -q

lint: ## Static checks
	$(UV) run ruff check .

format: ## Apply formatting and import sorting
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

check: lint test ## Lint then test

build: ## Build the wheel and sdist into dist/
	$(UV) build

clean: ## Remove build and test artefacts
	rm -rf dist build .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean ## Also remove the virtual environment
	rm -rf .venv

.PHONY: gif
gif: ## Rebuild images/demo.gif from the screenshots
	$(UV) run --with pillow python tools/make_demo_gif.py
	magick images/demo.gif -layers OptimizeFrame images/demo.opt.gif
	mv images/demo.opt.gif images/demo.gif
