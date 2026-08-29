UV ?= uv

.DEFAULT_GOAL := help
.PHONY: help install dev sync start stop test lint format check build clean distclean shots gif verify-shots

PID_FILE := .token-hud.pid

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Create the environment with runtime dependencies only
	$(UV) sync --no-dev

dev sync: ## Create the environment with dev dependencies
	$(UV) sync

start: ## Launch the HUD in the background, or unhide it if already running
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill -USR1 $$(cat $(PID_FILE)); \
		echo "token-hud already running (pid $$(cat $(PID_FILE))), unhidden"; \
	else \
		nohup $(UV) run python -m token_hud > /tmp/token-hud.log 2>&1 & \
		echo $$! > $(PID_FILE); \
		echo "token-hud started (pid $$!)"; \
	fi

stop: ## Stop the background HUD process
	@if [ -f $(PID_FILE) ] && kill -0 $$(cat $(PID_FILE)) 2>/dev/null; then \
		kill $$(cat $(PID_FILE)) && echo "token-hud stopped"; \
	else \
		echo "token-hud not running"; \
	fi; \
	rm -f $(PID_FILE)

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

shots: ## Capture ticker + dashboard PNGs from the real HUD (offscreen)
	QT_QPA_PLATFORM=offscreen QT_ENABLE_HIGHDPI_SCALING=0 QT_AUTO_SCREEN_SCALE_FACTOR=0 \
		QT_FONT_DPI=96 QT_SCALE_FACTOR=1 \
		$(UV) run python tools/capture_shots.py

gif: shots ## Rebuild images/demo.gif from freshly captured screenshots
	$(UV) run --with pillow python tools/make_demo_gif.py
	@if command -v magick >/dev/null 2>&1; then \
		magick images/demo.gif -layers OptimizeFrame images/demo.opt.gif && mv images/demo.opt.gif images/demo.gif; \
	elif command -v convert >/dev/null 2>&1; then \
		convert images/demo.gif -layers OptimizeFrame images/demo.opt.gif && mv images/demo.opt.gif images/demo.gif; \
	else \
		echo "imagemagick not found; leaving pillow gif as-is"; \
	fi

verify-shots: ## Host-stable shot check (sizes + layout hash, not PNG bytes)
	$(UV) run python tools/verify_shots.py
