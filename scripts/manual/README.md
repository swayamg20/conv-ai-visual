# Manual integration checks

These scripts call live providers, inspect developer data, or write media output. They are intentionally outside `tests/` and are never collected by the default automated test command.

Run them only from an environment configured with the provider credentials named in each script. Generated output belongs under the ignored `var/` directory.

## Gate 1 live-scene corpus

`probe_live_scene.py` exercises the production scene-authoring service without the browser. It refuses a live provider call unless the operator supplies a positive capped budget, current input/output prices, and the exact paid-provider acknowledgement. It never writes prompts, patch bodies, provider error bodies, or credentials; the ignored report contains prompt hashes, lifecycle types, validity, and latency only.

Preflight the corpus without reading credentials or calling a provider:

    .venv/bin/python scripts/manual/probe_live_scene.py \
      --max-cost-usd 0.10 \
      --input-price-per-million-usd CURRENT_INPUT_PRICE \
      --output-price-per-million-usd CURRENT_OUTPUT_PRICE \
      --dry-run

After verifying current provider pricing and receiving an explicit user budget, run all ten prompts:

    .venv/bin/python scripts/manual/probe_live_scene.py \
      --max-cost-usd USER_APPROVED_CAP \
      --input-price-per-million-usd CURRENT_INPUT_PRICE \
      --output-price-per-million-usd CURRENT_OUTPUT_PRICE \
      --acknowledge-paid-provider I_ACCEPT_PROVIDER_COST \
      --require-server-thresholds

These thresholds cover provider-to-server protocol validity and patch arrival only. The redacted report deliberately stores no authored scene bodies, so it cannot measure browser first-visible latency or judge educational/visual usefulness. Run the same prompts through the enabled product UI, record browser timing separately, and visually review the board before calling Gate 1 passed.
