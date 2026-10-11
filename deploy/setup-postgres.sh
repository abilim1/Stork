#!/bin/sh
# Run as the SSH user; sudo authenticates privately in the terminal.
set -eu
umask 077
if [ "$(id -u)" = 0 ]; then
    echo 'Run this as your normal SSH user, not as root.' >&2
    exit 1
fi
sudo -v
python3 - <<'PY'
import json
import os
from pathlib import Path
import secrets
import subprocess

def admin(sql):
    return subprocess.run(['sudo', '-n', '-u', 'postgres', 'psql', '-X', '-v',
                           'ON_ERROR_STOP=1', '-At', '-d', 'postgres'],
                          input=sql, text=True, capture_output=True)

check = admin("SELECT EXISTS(SELECT FROM pg_roles WHERE rolname='stork') OR EXISTS(SELECT FROM pg_database WHERE datname='stork');")
if check.returncode or check.stdout.strip() != 'f':
    raise SystemExit('Setup stopped: Stork role/database already exists, or administrator access failed. Nothing overwritten.')
directory = Path.home() / '.config' / 'stork'
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
path = directory / 'postgres.json'
password = secrets.token_urlsafe(48)
# Create the private file first so partial setup cannot lose the generated secret.
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as handle:
    json.dump({'dbname': 'stork', 'user': 'stork', 'password': password}, handle)
result = admin("CREATE ROLE stork LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '" + password + "';\n"
               "CREATE DATABASE stork OWNER stork;\n"
               "REVOKE ALL ON DATABASE stork FROM PUBLIC;\n")
if result.returncode:
    # psql errors can include a statement containing the secret; never echo them.
    raise SystemExit('Setup incomplete. Private credentials retained; ask for a status check before retrying.')
print('Stork database created. Password saved privately; PostgreSQL remains private.')
PY
