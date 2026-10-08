# Service configuration

Gateway, Tick Worker, History Worker, and Execution Service load typed public
settings through `service.config.load_settings`. Each composition root passes
the loaded settings to its MT5 client; reconnects reuse those settings instead
of selecting another YAML file.

Set `TRADE_DATA_CONFIG` to a YAML path visible inside the container to choose
an alternate configuration. The legacy `MT5_SETTINGS_PATH` selector remains
supported. Compose passes both selectors to the data and execution services.
Unset or empty selectors use `/app/service/config/settings.yaml` when present,
otherwise the repository configuration beside the loader.

An explicit Python `config_path`, `TRADE_DATA_CONFIG`, and `MT5_SETTINGS_PATH`
must identify the same file when supplied together. Paths are normalized,
including relative paths and symlinks. Conflicts stop startup with a sanitized
error containing selector names only. Do not put credentials in public YAML.

`MT5_CONNECTION_MODE` overrides the YAML connection mode consistently across
the stack. The default is `terminal`: the account selected in the MT5 desktop
is authoritative, and connector construction does not open account profiles.
`managed` is explicit opt-in and loads the configured private profile. Keep
private profiles and API keys outside version control; never copy their values
into diagnostics or issue reports.

The History Worker and Gateway use `history_service.data_path` for CSV
publication and reads. Worker status includes an opaque storage identifier to
detect a configured read/write-root mismatch without exposing paths. History
availability is reported separately from the realtime path.

After changing YAML or process environment, restart the affected services so
each process loads the same version. Configuration is not hot-reloaded.
Preserve persistent MT5 state and execution idempotency storage when recreating
containers. Configuration changes do not authorize order, cancellation, or
position-close probes; deployment verification uses GET requests.
