# Manual integration checks

These scripts call live providers, inspect developer data, or write media output. They are intentionally outside `tests/` and are never collected by the default automated test command.

Run them only from an environment configured with the provider credentials named in each script. Generated output belongs under the ignored `var/` directory.
