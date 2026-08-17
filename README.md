# Ollama Cloud Models with OpenRouter Benchmarks

A static page listing every model on Ollama's cloud tier, marked up with OpenRouter's benchmark scores (Artificial Analysis intelligence / coding / agentic indices), to help pick the right model for a given usage tier on Ollama Pro.

## Setup

```
cp .env.example .env
```

Then edit `.env`:

- `OPENROUTER_API_KEY` — a real key from https://openrouter.ai/settings/keys.
- `SURGE_DOMAIN` — optional, the domain `make push-surge` publishes to (e.g. `your-name.surge.sh`). Leave blank and surge will prompt you interactively instead.

`.env` is gitignored, so it's never committed.
Exporting either variable in your shell instead also works, and takes precedence over `.env`.

The API key is only ever used at build time, from your machine.
It never ships to the browser and is not embedded in `dist/`.

## Usage

```
make build      # fetch + scrape (uses .cache/ if present), writes dist/index.html and dist/data.json
make refresh    # same, but ignores the cache and re-fetches everything
make render     # re-render dist/index.html from the existing dist/data.json, no network calls at all
make serve      # serve dist/ at http://localhost:8080 (set PORT=... to change)
make clean      # remove dist/ and .cache/
```

Use `make render` while iterating on the HTML/CSS/JS template in `scripts/build.py` — it's instant and never touches Ollama or OpenRouter.
Pair it with `make serve` in another terminal to preview changes live in a browser (refresh the page after each `make render`).

## Fixing model matches

Ollama model names (e.g. `gpt-oss:120b-cloud`) don't line up with OpenRouter's `model_permaslug` (e.g. `openai/gpt-oss-120b`), so the build script fuzzy-matches them.
Each run prints every match and its confidence score to stderr.
For anything wrong or missing, add a correction to `overrides.json`:

```json
{
  "gpt-oss": "openai/gpt-oss-120b",
  "some-family-with-no-benchmark": null
}
```

A `null` value forces "no data" instead of a bad guess.
Re-run `make build` (or `make refresh`) after editing overrides.

## Deploying

```
make push-surge
```

This runs `make build` first, then publishes `dist/` with `npx surge`, using `SURGE_DOMAIN` from `.env`.
You can also override it inline: `make push-surge SURGE_DOMAIN=your-name.surge.sh`.
If neither is set, surge will prompt you for a domain interactively.
