.PHONY: help build refresh render serve push-surge clean

-include .env
export

SURGE_DOMAIN ?=
PORT ?= 8080

.DEFAULT_GOAL := help

help: ## Show this list of targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Fetch + scrape (uses .cache/ if present), write dist/index.html and dist/data.json
	python3 scripts/build.py

refresh: ## Same as build, but ignores the cache and re-fetches everything
	python3 scripts/build.py --no-cache

render: ## Re-render dist/index.html from the existing dist/data.json, no network calls at all
	python3 scripts/build.py --render-only

dist/index.html:
	$(MAKE) build

serve: | dist/index.html ## Serve dist/ at http://localhost:$(PORT) (builds first if dist/ doesn't exist yet; set PORT=... to change)
	python3 -m http.server $(PORT) --directory dist

push-surge: build ## Build then publish dist/ to surge.sh (uses SURGE_DOMAIN from .env if set)
	npx surge dist $(SURGE_DOMAIN)

clean: ## Remove dist/ and .cache/
	rm -rf dist .cache
