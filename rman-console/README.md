# RMAN Console

A small web application that triggers Oracle RMAN backups on registered
database hosts over SSH and streams the log back live.

Supports level 0, level 1 cumulative, level 1 differential, and
archive-log-only backups.

## How it works

```
browser  ──HTTP/WS──>  FastAPI app  ──SSH/SFTP──>  Oracle DB host
                            │                          │
                         SQLite                   rman target /
                      targets + jobs            (runs detached)
```

For each job the app:

1. creates `/tmp/rman-console/job-<id>` on the target
2. uploads a generated `backup.rman` command file and a `run.sh` wrapper
3. launches the wrapper with `setsid nohup`, so the backup survives a dropped
   SSH session or an app restart
4. polls `backup.log` over SFTP and publishes new bytes to connected browsers
5. stops when the wrapper writes its exit code to a `status` file

RMAN connects with `rman target /`, using operating system authentication. No
database password is stored anywhere unless you configure a recovery catalog.

## Requirements

**App server**

- Python 3.11 or newer
- Network reach to each database host on its SSH port
- An SSH private key accepted by the Oracle operating system user on each target

**Each target host**

- Oracle Database with RMAN, reachable at the configured `ORACLE_HOME`
- An operating system user in the `dba` group, so `rman target /` connects
- Archive log mode enabled, for incremental backups to be meaningful

## Install

The app server runs on Windows or Linux. The database hosts it connects to
must be Linux, since the generated wrapper script is bash.

### Windows

```powershell
cd rman-console
.\setup.ps1      # creates .venv, installs deps, writes .env with a fresh token
.\run.ps1        # starts on http://localhost:8000
```

If PowerShell blocks the scripts, allow them for the current session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Or do it by hand:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into .env
python -m uvicorn backend.main:app --port 8000
```

### Linux and macOS

```bash
cd rman-console
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"   # paste into .env
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Edit `.env` and set at least `RMAN_CONSOLE_TOKEN` and
`RMAN_CONSOLE_KNOWN_HOSTS`.

### Windows specifics

- **SSH key path** is a path on the Windows machine, for example
  `C:\Users\you\.ssh\id_rsa`. OpenSSH format keys work; PuTTY `.ppk` files
  do not, so convert them with PuTTYgen first.
- **known_hosts** normally lives at `C:\Users\you\.ssh\known_hosts`. Populate
  it with `ssh-keyscan db01.example.com >> $env:USERPROFILE\.ssh\known_hosts`.
  Windows 10 and later ship `ssh-keyscan` with OpenSSH.
- **Database path** in `.env` may use either slash direction; the app
  normalises it.

### Host keys

Host key verification is on, and unknown hosts are rejected. Add each target
before registering it:

```bash
ssh-keyscan -p 22 db01.example.com >> ~/.ssh/known_hosts
```

## Run

Open <http://localhost:8000>, paste the token, and add a target.

For anything beyond a laptop, run it behind a reverse proxy with TLS.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `RMAN_CONSOLE_TOKEN` | *(none)* | Shared bearer token. The API refuses every request until this is set. |
| `RMAN_CONSOLE_SECRET_KEY` | *(generated)* | Fernet key for stored credentials. Defaults to `secret.key` beside the database. |
| `RMAN_CONSOLE_DB` | `./rman_console.db` | SQLite file holding targets and job history |
| `RMAN_CONSOLE_KNOWN_HOSTS` | `~/.ssh/known_hosts` | Host keys used to verify targets |
| `RMAN_CONSOLE_REMOTE_DIR` | `/tmp/rman-console` | Working directory created on each target |
| `RMAN_CONSOLE_POLL_INTERVAL` | `2` | Seconds between log polls |
| `RMAN_CONSOLE_JOB_TIMEOUT_HOURS` | `24` | When the app stops following a job |

## Target settings

| Field | Notes |
| --- | --- |
| Authentication | `key` or `password` |
| SSH key path | Path **on the app server**, not the target. Key auth only |
| SSH password | Password auth only. Encrypted at rest |
| Run RMAN as | Optional. Leave blank when the SSH user owns the database |
| sudo password | Only when *Run RMAN as* is set and sudo is not NOPASSWD |
| ORACLE_HOME | Used to set the environment and locate the `rman` binary |
| ORACLE_SID | The instance to connect to with `rman target /` |
| Channels | Parallel disk channels allocated in the `RUN` block |
| Compressed | Emits `AS COMPRESSED BACKUPSET` |
| Backup destination | Leave blank to use the Fast Recovery Area |
| Catalog connect | Optional. Written into the command file on the target with mode 0600 |

## Connecting to a target

Two choices, independent of each other: how the app authenticates over SSH,
and whether it then switches to another user to run RMAN.

| Your situation | Authentication | Run RMAN as | sudo password |
| --- | --- | --- | --- |
| Key is installed for `oracle` | SSH private key | *(blank)* | - |
| OCI: key is for `opc` | SSH private key | `oracle` | blank if NOPASSWD |
| On-prem: `oracle` has a password | Password | *(blank)* | - |
| Admin account with a password, sudo to `oracle` | Password | `oracle` | the sudo password |

All four are supported and tested end to end.

### Stored credentials

SSH passwords, sudo passwords and catalog connect strings are encrypted with
Fernet before they reach the database, so the SQLite file and any backup of
it carry no readable credentials. They are never returned by the API; a
target reports only whether each one is set.

The key lives in `secret.key` beside the database, created on first use, or
comes from `RMAN_CONSOLE_SECRET_KEY` if you set it. **Keep it with the
database**: without it, stored passwords cannot be decrypted and must be
re-entered.

Be clear about what this buys you. The key sits on the same machine as the
database, so anyone who can read the app server can decrypt. It protects a
leaked database file, a shared backup, or a glance over a shoulder. It is
not a substitute for restricting who can reach the host. Where you have the
choice, key authentication avoids the problem entirely.

### When the SSH user is not the Oracle user

This is the normal arrangement on OCI and on most hardened Linux builds: the
SSH key belongs to `opc` (or `ec2-user`, or a named admin account) while the
database runs as `oracle`.

Set **SSH user** to `opc` and **Run RMAN as** to `oracle`. The app then:

- connects as `opc`
- uploads the command file and wrapper into its own working directory
- hands that directory to `oracle` with `sudo -n chown`
- launches the wrapper with `sudo -n -u oracle setsid nohup`
- reads the log back as `opc`, which is why the directory is left at 0755

The command file stays mode 0600 and owned by `oracle`, since it may carry a
catalog connect string.

**`opc` needs sudo to `oracle`**, either passwordless or with a password set
on the target. On OCI images passwordless sudo is usually already configured.
If not, either set the sudo password on the target, or add a sudoers rule:

```
opc ALL=(oracle) NOPASSWD: ALL
opc ALL=(root)   NOPASSWD: /bin/chown, /bin/chmod
```

The `-n` flag means sudo fails immediately rather than waiting for a password,
so a missing rule shows up as a clear message instead of a job that hangs
until the timeout. **Test connection** checks sudo before it checks
`ORACLE_HOME`, and reports which one failed.

## Generated RMAN

A level 0 with two channels, compression and archive logs produces:

```
CONNECT TARGET /;

SHOW ALL;

RUN {
  ALLOCATE CHANNEL c1 DEVICE TYPE DISK;
  ALLOCATE CHANNEL c2 DEVICE TYPE DISK;
  BACKUP AS COMPRESSED BACKUPSET INCREMENTAL LEVEL 0 DATABASE TAG 'L0_20261019_101500';
  BACKUP AS COMPRESSED BACKUPSET ARCHIVELOG ALL TAG 'L0_20261019_101500_ARC';
  BACKUP CURRENT CONTROLFILE TAG 'L0_20261019_101500_CTL';
  RELEASE CHANNEL c1;
  RELEASE CHANNEL c2;
}

LIST BACKUP SUMMARY;
EXIT;
```

Backup type maps to RMAN as follows. A level 1 backup is differential unless
`CUMULATIVE` is stated, which is why the differential form carries no extra
keyword.

| Selection | RMAN clause |
| --- | --- |
| Level 0 | `INCREMENTAL LEVEL 0 DATABASE` |
| Level 1 cumulative | `INCREMENTAL LEVEL 1 CUMULATIVE DATABASE` |
| Level 1 differential | `INCREMENTAL LEVEL 1 DATABASE` |
| Archive logs only | `ARCHIVELOG ALL` |

## Security notes

Read these before exposing the app to anyone else.

- **One shared token is not user authentication.** There is no per-user
  identity and no audit of who triggered what. Put it behind SSO or a
  proxy that authenticates first.
- **The app can run RMAN as the Oracle user on every registered host.** Treat
  the app server as a privileged machine and restrict who can reach it.
- **Target fields are pattern-validated on input**, and every value
  interpolated into a shell command is passed through `shlex.quote`. Targets
  are intended to be created by administrators, not end users.
- **Catalog credentials are stored in plain text** in SQLite and written to the
  target with mode 0600. Prefer the control file unless you need a catalog.
- **`DELETE INPUT` is off by default.** It removes archive logs from disk once
  they are backed up and cannot be undone.

## Limitations

- Autonomous Database cannot be targeted; it has no RMAN access
- OCI Base Database Service often has Oracle-managed automatic backups already
  configured. Check before adding a second backup regime
- One job at a time per target, enforced by the API
- `su` is not supported, only `sudo`. Ask if you need it
- Targets cannot be edited; remove and re-add to change credentials
- No scheduling yet. Drive it from cron or a scheduler calling `POST /api/jobs`
- No retention management. `DELETE OBSOLETE` and `CROSSCHECK` are deliberately
  not generated

## API

All endpoints require `Authorization: Bearer <token>`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/targets` | List targets |
| POST | `/api/targets` | Register a target |
| DELETE | `/api/targets/{id}` | Remove a target |
| POST | `/api/targets/{id}/test` | Check SSH and locate RMAN |
| GET | `/api/backup-types` | Selectable backup types |
| GET | `/api/jobs` | Recent jobs |
| GET | `/api/jobs/{id}` | One job, with its full log |
| POST | `/api/jobs` | Start a backup |
| POST | `/api/jobs/{id}/cancel` | Signal the remote process group |
| GET | `/api/jobs/{id}/script` | The generated command file |
| WS | `/ws/jobs/{id}?token=...` | Live log stream |

Trigger a nightly level 1 from cron:

```bash
curl -sS -X POST http://rman-console:8000/api/jobs \
  -H "Authorization: Bearer $RMAN_CONSOLE_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"target_id": 1, "backup_type": "LEVEL1_DIFF"}'
```
