# Trade data service architecture

The deployment keeps two containers. `mt5-server` owns Wine, the MT5 terminal,
and the internal RPyC bridge on port 8001. `trade-data-service` owns the Linux
application and is the only container publishing the trade-data API on port
8090.

```text
mt5-server (Wine + MT5 + internal RPyC :8001)
      |
      +---- trade-data-service
              `-- service.runtime.supervisor (PID 1)
                    |-- service.entrypoints.tick_worker
                    |     `-- realtime -> MT5 + tick IPC publisher
                    |-- service.entrypoints.history_worker
                    |     `-- history -> MT5 + atomic CSV storage
                    `-- service.entrypoints.api_gateway :8090
                          |-- realtime IPC consumer
                          |-- history query service
                          `-- trade query service -> MT5
```

The supervisor always owns exactly these three process groups. A child exiting
for any reason stops its siblings and makes the container exit non-zero. On
SIGTERM or SIGINT it forwards SIGTERM, waits for the configured bounded grace
period, and then kills only processes that remain.

Gateway is the sole external application boundary. Realtime acquisition and
history persistence are independent workers rather than public services.
History degradation remains non-critical to realtime readiness. RPyC port 8001
is not published to the host.

Source dependencies point inward through `service/infrastructure`; each OS
process owns its own MT5 client instance. The compatibility modules at the old
paths are migration shims and are not runtime entrypoints.
