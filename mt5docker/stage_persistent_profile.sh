#!/usr/bin/env bash
# Explicitly selected private profile only; never emit its contents.
set -Eeuo pipefail
if [ "$#" -ne 2 ]; then
    echo 'Usage: stage_persistent_profile.sh INPUT_INI NEW_OUTPUT_INI' >&2
    exit 2
fi
exec python3 - "$1" "$2" <<'PY'
import os
import re
import stat
import sys
import tempfile

temporary = None
try:
    source, destination = sys.argv[1:]
    if os.path.lexists(destination):
        raise ValueError()
    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError()
        data = stream.read()
    # Fail closed for UTF-16 or binary input. No transcoding of credentials.
    if data.startswith((b'\xff\xfe', b'\xfe\xff')) or b'\0' in data:
        raise ValueError()
    data.decode('utf-8-sig')
    bom = b'\xef\xbb\xbf' if data.startswith(b'\xef\xbb\xbf') else b''
    lines = data[len(bom):].splitlines(keepends=True)
    newline = b'\r\n' if b'\r\n' in data else b'\n'
    common_start = None
    common_end = len(lines)
    keep_index = None
    active = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        section = re.fullmatch(rb'\[([^\]]+)\][ \t]*(?:[;#].*)?', stripped)
        if section:
            if active:
                common_end = index
            active = section.group(1).strip().lower() == b'common'
            if active:
                if common_start is not None:
                    raise ValueError()
                common_start = index
        elif active and re.match(rb'keepprivate[ \t]*=', stripped, re.I):
            if keep_index is not None:
                raise ValueError()
            keep_index = index
    if keep_index is not None:
        ending = b'\r\n' if lines[keep_index].endswith(b'\r\n') else (b'\n' if lines[keep_index].endswith(b'\n') else b'')
        lines[keep_index] = b'KeepPrivate=1' + ending
    elif common_start is not None:
        if common_end and not lines[common_end - 1].endswith(b'\n'):
            lines[common_end - 1] += newline
        lines.insert(common_end, b'KeepPrivate=1' + newline)
    else:
        if lines and not lines[-1].endswith(b'\n'):
            lines[-1] += newline
        lines.extend([b'[Common]' + newline, b'KeepPrivate=1' + newline])
    parent = os.path.dirname(os.path.abspath(destination))
    descriptor, temporary = tempfile.mkstemp(prefix='.mt5-profile-', dir=parent)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(bom + b''.join(lines))
        stream.flush()
        os.fsync(stream.fileno())
    # Atomic publication without replacing an existing file or symlink.
    os.link(temporary, destination, follow_symlinks=False)
except Exception:
    print('Profile staging failed; input unchanged. Check encoding, unique Common/KeepPrivate, and unused output path.', file=sys.stderr)
    sys.exit(1)
finally:
    if temporary is not None:
        os.unlink(temporary)
PY
