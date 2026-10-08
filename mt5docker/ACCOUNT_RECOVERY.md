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

   Verification prints only the native state and marker-valid boolean. It
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
