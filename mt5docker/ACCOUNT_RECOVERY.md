# MT5 native account recovery

Four independent states matter: the non-secret bootstrap marker records a past
bootstrap; `Config/accounts.dat` (or `config/accounts.dat`) is opaque native MT5
account storage; a private INI selects one credential profile; and the running
GUI session determines the active account. None establishes the contents or
validity of the others. Do not count private profiles as saved native accounts.

A valid marker with missing or empty native storage is an inconsistent state.
Startup must not silently re-import a fixed private account. Leave the desktop
available for manual login and keep the API closed until authorization succeeds.
An existing native store without a marker must also be preserved.

## Deliberate recovery of one profile

An operator must authorize the selected profile import. This procedure is not a
bulk import and does not guarantee restoration of the entire GUI login list.
Never edit `accounts.dat`, print a private INI, or put credentials in commands.

Before importing the explicitly authorized profile, stage a separate private
INI with password persistence enabled, supplying paths only:

```bash
bash /mt5docker/stage_persistent_profile.sh \
  /private/authorized-selected-profile.ini /private/new-persistent-profile.ini
```

Use the staged output as the selected profile below. The output must not already
exist; the helper atomically publishes a mode-600 file and leaves the input
unchanged. It preserves unrelated sections and credential bytes, handles UTF-8
(including BOM) and CRLF, and refuses UTF-16/binary input, duplicate Common
sections, duplicate KeepPrivate options, and existing/symlink output targets.
Keep the parent directory private. No credentials or paths are printed.
The official [MT5 advanced startup documentation](https://www.metatrader5.com/en/terminal/help/start_advanced/start)
defines `[Common] KeepPrivate=1` as saving the password between connections;
`0` does not save it. Enabling this setting is a persistence prerequisite to
test, not proof of why previous native metadata disappeared or of restart
survival. The controlled evidence checklist below still applies.

1. Stop `mt5-server` before making a backup. Use a one-off container with the
   same persistent portable mount and private config mount, with its normal
   startup overridden. Do not run a second terminal against the same storage.
2. In that environment run the following, supplying paths only:

   ```bash
   bash /mt5docker/prepare_account_recovery.sh --confirm-single-profile \
     /private/selected-profile.ini /persistent/MT5_Data /private/recovery-backups
   ```

   The script refuses a running terminal, copies native Config/config metadata
   and the marker without reading or logging their contents, and stages a
   private `selected-profile.ini` in a new mode-700 recovery directory. Native
   originals remain untouched. Backup storage must be persistent and private.
   Set `MT5_BOOTSTRAP_MARKER` if the deployment stores its marker elsewhere.
   The `recovery_directory=` output identifies the exact new directory; only a
   path is printed. Every copied regular file has mode 600 and directory mode
   700 regardless of the original permissions.
3. Mount that newly staged INI using `MT5_CONFIG_FILE` and perform one controlled
   restart with `MT5_BOOTSTRAP_REIMPORT=1` in terminal mode. Startup retains
   `/portable /skipupdate` and imports only that selected profile.
4. Confirm successful GUI authorization, then verify persisted metadata using
   the actual mounted portable root and marker paths:

   ```bash
   bash /mt5docker/prepare_account_recovery.sh --verify \
     /persistent/MT5_Data /persistent/MT5_Data/.mt5-bootstrap-complete
   ```

   Verification prints native state, marker validity, `metadata_present`, and
   explicit `recovery_certified=false`, `session_certified=false`, and
   `restart_certified=false`. Its zero exit status remains compatible with
   callers and means only that metadata predicates passed. It
   requires nonempty native storage and a valid marker, never reads account
   contents, and fails if either condition is missing. It does not prove saved
   account count or credential validity; the authorized GUI session provides
   session evidence. After verification,
   restore `MT5_BOOTSTRAP_REIMPORT=0` before the next restart. Subsequent starts
   restore the GUI-selected session without `/config`. If import fails, preserve
   the desktop for manual correction; do not try another profile automatically.
5. Verify readiness and GET-only account/session endpoints. Trading requires
   its separate Demo gate and explicit mutation authorization.

For rollback stop the terminal and restore the backed-up native directories and
marker as files, never modifying their contents. Restore the previous config
mount and keep re-import disabled. Private backups have the same sensitivity as
the original native credential store and require restricted access and retention.

## Controlled recovery evidence checklist

In a separately authorized recovery window, collect a fresh pre-import Journal
baseline and fresh post-baseline startup/authorization booleans without raw
logs or account IDs. Confirm the selected account appears in GUI Accounts.
Close MT5 using normal GUI exit, confirm process absence, and collect metadata.
Restart once with re-import disabled and without `/config`, checking the same
mount and executable. Require fresh saved-session authorization and retained
GUI account presence. A stable nonempty store alone never proves account
validity, saved credentials, session authorization, or restart survival.

Start bounded read-only sampling before import and retain captures through
readiness, normal close, and config-free restart, marking each phase separately:

```bash
bash /mt5docker/observe_account_recovery.sh \
  /persistent/MT5_Data /persistent/MT5_Data/.mt5-bootstrap-complete 600 1
```

The observer emits timestamps and existence, size, inode, and mtime for both
Config/config accounts.dat stores and the marker. It never opens contents or
prints supplied paths, raw logs, or account IDs. Zero size distinguishes empty
from missing storage. A failed stat means missing/inaccessible, with unknown
metadata. Sampling allows 1..3600 observations, intervals of 1..60 seconds,
and at most one hour of waiting; gaps cannot exclude transient changes.
It performs no login, import, close, or restart and certifies none of them.

Classify disappearance before close as premature persistence acceptance;
disappearance or empty rewrite during close as close-time storage failure;
loss only after restart as reload/path failure. Retained metadata without fresh
authorization or GUI presence still fails the checklist. These classifications
guide investigation, not proven root causes: the prior audit matched paths and
found missing storage but did not establish who removed it or why.

## Optional full Wine-prefix persistence experiment

KeepPrivate alone did not preserve the native store during the observed
config-free container recreation. The base Compose file persists MT5 AppData
but leaves the rest of `/opt/wineprefix`, including Wine registry files, in the
container writable layer. Loss of that context is a candidate explanation,
not a proven cause. `compose.wine-prefix.yaml` adds an explicitly selected
bind for the complete prefix while retaining both existing MT5 data mounts
and the private INI mount. It is never enabled by the base Compose file.

In an authorized maintenance window, exit MT5 normally and stop the existing
`mt5-server` container before copying its prefix. Do not remove/recreate that
container until the copy succeeds. Initialize a new, empty prefix directory
under a private mode-700 parent using `umask 077`; copy the entire stopped
container prefix with `docker cp -a mt5-server:/opt/wineprefix/. DESTINATION`.
Do not use `-L`: preserve Wine symlinks instead of following them. Preserve
ownership, executable bits, registry files and hidden entries. Keep this copy
private because it can contain credentials and other sensitive state. This
procedure copies files without displaying their contents. Existing nonempty
destinations must be backed up and selected deliberately, never merged blindly.

Verify numeric ownership with `stat` before starting Wine. This image runs Wine
as root, so the selected prefix root and copied registry files must have UID 0;
verify the source owner from the stopped container metadata and preserve it
with `docker cp -a`. A normal host-user copy can produce UID 1000 and Wine then
refuses the prefix as "not owned by you". Check the destination prefix root is
a real directory rather than a symlink, check its resolved path is the exact
new private copy, and verify directory mode 700. Check ownership metadata for
the copied tree without following symlinks; do not display file contents.

If ownership was lost, stop all users of that prefix first. Correct only the
explicitly validated copied prefix using a trusted root helper with networking
disabled and that directory as its sole writable bind. Within that helper,
`chown -hRP 0:0 /copied-prefix` changes symlink ownership without dereferencing
or traversing symlinks; `chmod 700 /copied-prefix` restricts the verified real
prefix root. `/copied-prefix` must be the helper's exact bind target, not a host
path, and no live MT5 AppData mount should be nested beneath it during this
correction. Preserve executable modes inside the tree. Recheck numeric owner
and root mode after correction. Do not apply recursive ownership changes to a
workspace, parent private directory, unresolved path, or active Wine prefix.
This repair addresses the observed ownership error only; it does not establish
the cause of earlier account-store disappearance.

Verify the selected directory is nonempty and contains the expected `drive_c`,
`dosdevices`, `system.reg`, and `user.reg` using existence/metadata checks only.
The nested MT5 AppData bind still selects `MT5_DATA_DIR`, so retain the same
data directory and marker. Do not infer that copying the outer prefix also
backed up the independently mounted AppData; back that data up separately.
The override refuses an unset/empty selection and disables automatic host-path
creation; it does not validate an existing directory's initialization. An empty
prefix would mask the initialized image prefix and must never be selected.

Use the override consistently for subsequent Compose operations, with the
explicit private path supplied through the deployment environment:

```bash
MT5_WINE_PREFIX_DIR=/private/initialized-mt5-prefix \
  docker compose -f compose.yaml -f compose.wine-prefix.yaml up -d mt5-server
```

This is a deployment example, not permission to perform recovery. Run the
controlled recovery checklist above and require fresh authorization after
config-free recreation before claiming persistence. Do not run two containers
against the prefix. For rollback, stop the terminal, preserve the experimental
prefix/data, restore the authorized data backup if needed, and use the base
Compose file with re-import disabled.

A persisted prefix masks the image's prefix, including its installed Windows
software. Image upgrades therefore no longer automatically replace those files.
Back up the stopped prefix and data before upgrades; validate Wine compatibility
and any intended terminal migration on a separate copy. Do not overwrite a live
prefix with a new image prefix or assume that changing the image upgrades MT5.
