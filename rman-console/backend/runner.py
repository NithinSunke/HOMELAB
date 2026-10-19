"""Executes a job on the target over SSH and streams its log back.

Shape of a run:
  1. create a per-job directory on the target
  2. upload the RMAN command file and a shell wrapper
  3. launch the wrapper detached with setsid, so it outlives the SSH session
  4. poll the log file over SFTP, publishing new bytes to any subscribers
  5. stop when the wrapper writes its status file, and record the exit code
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import shlex
from collections import defaultdict

import asyncssh
from asyncssh.public_key import KeyEncryptionError, KeyImportError

from . import rman
from .config import JOB_TIMEOUT_HOURS, KNOWN_HOSTS, POLL_INTERVAL, REMOTE_DIR
from .database import SessionLocal
from .models import Job, Target

log = logging.getLogger("rman_console.runner")

# job id -> queues belonging to connected websocket clients
_subscribers: dict[int, set[asyncio.Queue]] = defaultdict(set)
_running: dict[int, asyncio.Task] = {}


def subscribe(job_id: int) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    _subscribers[job_id].add(q)
    return q


def unsubscribe(job_id: int, q: asyncio.Queue) -> None:
    _subscribers[job_id].discard(q)
    if not _subscribers[job_id]:
        _subscribers.pop(job_id, None)


def _publish(job_id: int, payload: dict) -> None:
    for q in list(_subscribers.get(job_id, ())):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:  # pragma: no cover - unbounded queues
            pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _connect(target: Target) -> asyncssh.SSHClientConnection:
    """Open an SSH connection with host key verification enabled."""
    return await asyncssh.connect(
        host=target.hostname,
        port=target.ssh_port,
        username=target.ssh_user,
        client_keys=[target.ssh_key_path],
        known_hosts=KNOWN_HOSTS,
    )


async def test_target(target: Target) -> tuple[bool, str]:
    """Check SSH reachability and that the Oracle home looks usable."""
    try:
        async with await _connect(target) as conn:
            probe = (
                f"test -x {shlex.quote(target.oracle_home)}/bin/rman "
                f"&& echo RMAN_OK || echo RMAN_MISSING"
            )
            result = await conn.run(probe, check=False)
            out = (result.stdout or "").strip()
            if "RMAN_OK" not in out:
                return False, (
                    f"Connected as {target.ssh_user}@{target.hostname}, but "
                    f"{target.oracle_home}/bin/rman is missing or not executable."
                )
            ver = await conn.run(
                f"{shlex.quote(target.oracle_home)}/bin/rman 2>&1 </dev/null | head -2",
                check=False,
            )
            banner = " ".join((ver.stdout or "").split())[:200]
            return True, f"SSH and RMAN both reachable. {banner}"
    except asyncssh.HostKeyNotVerifiable:
        return False, (
            f"The host key for {target.hostname} is not in {KNOWN_HOSTS}. "
            f"Add it with:  ssh-keyscan -p {target.ssh_port} {target.hostname} "
            f">> {KNOWN_HOSTS}"
        )
    except asyncssh.PermissionDenied:
        return False, (
            f"{target.ssh_user}@{target.hostname} rejected the key. Check that "
            f"the public half of {target.ssh_key_path} is in that user's "
            "~/.ssh/authorized_keys, and that the user name is right."
        )
    except KeyEncryptionError:
        return False, (
            f"The private key {target.ssh_key_path} is protected by a "
            "passphrase. This app does not prompt for one. Use a key without a "
            "passphrase for automation, or decrypt a copy with:  "
            "ssh-keygen -p -f <copy-of-key>"
        )
    except KeyImportError as exc:
        return False, (
            f"Could not read {target.ssh_key_path} as a private key ({exc}). "
            "PuTTY .ppk files are not supported: convert with PuTTYgen using "
            "Conversions > Export OpenSSH key. The file must be the private "
            "key, not the .pub."
        )
    except FileNotFoundError as exc:
        missing = getattr(exc, "filename", None) or str(exc)
        hint = ""
        if KNOWN_HOSTS in str(missing):
            hint = (
                " Create it, then add the host with ssh-keyscan. On Windows "
                "the folder is C:\\Users\\<you>\\.ssh"
            )
        return False, f"File not found: {missing}.{hint}"
    except (OSError, asyncssh.Error) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        # A diagnostic endpoint should always report, never 500. Log the
        # traceback so the server console still has the detail.
        log.exception("connection test failed for target %s", target.name)
        return False, f"{type(exc).__name__}: {exc}"


def _set_status(job_id: int, **fields) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        for k, v in fields.items():
            setattr(job, k, v)
        db.commit()


async def run_job(job_id: int) -> None:
    """Drive one job from queued to finished. Never raises."""
    try:
        await _run_job_inner(job_id)
    except asyncio.CancelledError:
        _set_status(job_id, status="CANCELLED", finished_at=_now())
        _publish(job_id, {"type": "status", "status": "CANCELLED"})
        raise
    except Exception as exc:  # noqa: BLE001 - last line of defence
        log.exception("job %s failed", job_id)
        _set_status(
            job_id,
            status="FAILED",
            finished_at=_now(),
            error_summary=f"{type(exc).__name__}: {exc}",
        )
        _publish(job_id, {"type": "status", "status": "FAILED"})
    finally:
        _running.pop(job_id, None)
        _publish(job_id, {"type": "done"})


async def _run_job_inner(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        target = db.get(Target, job.target_id)
        if target is None:
            raise RuntimeError("target no longer exists")
        script = rman.build_script(target, job)
        remote_dir = f"{REMOTE_DIR.rstrip('/')}/job-{job.id}"
        wrapper = rman.build_wrapper(target, remote_dir)
        job.rman_script = script
        job.remote_dir = remote_dir
        job.status = "RUNNING"
        job.started_at = _now()
        db.commit()
        # Detach plain values so the session can close.
        tgt = db.get(Target, job.target_id)
        host_desc = f"{tgt.ssh_user}@{tgt.hostname}"
        target_obj = tgt

    _publish(job_id, {"type": "status", "status": "RUNNING"})
    _publish(job_id, {"type": "log", "data": f"Connecting to {host_desc} ...\n"})

    async with await _connect(target_obj) as conn:
        q_dir = shlex.quote(remote_dir)
        await conn.run(f"mkdir -p {q_dir} && chmod 700 {q_dir}", check=True)

        async with conn.start_sftp_client() as sftp:
            async with sftp.open(f"{remote_dir}/backup.rman", "w") as fh:
                await fh.write(script)
            async with sftp.open(f"{remote_dir}/run.sh", "w") as fh:
                await fh.write(wrapper)
            # The command file may carry a catalog connect string.
            await sftp.chmod(f"{remote_dir}/backup.rman", 0o600)
            await sftp.chmod(f"{remote_dir}/run.sh", 0o700)

        # setsid gives the wrapper its own process group, so a cancel can
        # signal the whole tree rather than just the shell.
        launch = (
            f"cd {q_dir} && setsid nohup ./run.sh >wrapper.out 2>&1 < /dev/null & "
            f"echo $!"
        )
        res = await conn.run(launch, check=True)
        pid = int((res.stdout or "0").strip() or 0)
        _set_status(job_id, pid=pid)
        _publish(job_id, {"type": "log", "data": f"RMAN started, pid {pid}\n\n"})

        await _follow(conn, job_id, remote_dir)


async def _follow(
    conn: asyncssh.SSHClientConnection, job_id: int, remote_dir: str
) -> None:
    """Poll the remote log until the wrapper writes its status file."""
    offset = 0
    buffered: list[str] = []
    deadline = _now() + dt.timedelta(hours=JOB_TIMEOUT_HOURS)
    log_path = f"{remote_dir}/backup.log"
    status_path = f"{remote_dir}/status"

    async with conn.start_sftp_client() as sftp:
        while True:
            # New log bytes since last pass.
            try:
                attrs = await sftp.stat(log_path)
                size = attrs.size or 0
                if size > offset:
                    async with sftp.open(log_path, "r", encoding="utf-8",
                                         errors="replace") as fh:
                        await fh.seek(offset)
                        chunk = await fh.read()
                    offset = size
                    if chunk:
                        buffered.append(chunk)
                        _publish(job_id, {"type": "log", "data": chunk})
                elif size < offset:
                    # Log was truncated or replaced; start over.
                    offset = 0
            except (OSError, asyncssh.SFTPError):
                pass  # the log may not exist for the first second or two

            # Has the wrapper finished?
            finished = False
            exit_code = None
            try:
                async with sftp.open(status_path, "r") as fh:
                    raw = (await fh.read()).strip()
                if raw:
                    exit_code = int(raw)
                    finished = True
            except (OSError, asyncssh.SFTPError, ValueError):
                pass

            if finished:
                await asyncio.sleep(0.5)  # let the last writes land
                try:
                    attrs = await sftp.stat(log_path)
                    if (attrs.size or 0) > offset:
                        async with sftp.open(log_path, "r", encoding="utf-8",
                                             errors="replace") as fh:
                            await fh.seek(offset)
                            tail = await fh.read()
                        if tail:
                            buffered.append(tail)
                            _publish(job_id, {"type": "log", "data": tail})
                except (OSError, asyncssh.SFTPError):
                    pass

                full_log = "".join(buffered)
                status = "SUCCESS" if exit_code == 0 else "FAILED"
                _set_status(
                    job_id,
                    status=status,
                    exit_code=exit_code,
                    finished_at=_now(),
                    log_text=full_log,
                    error_summary=rman.summarise_errors(full_log),
                )
                _publish(
                    job_id,
                    {"type": "status", "status": status, "exit_code": exit_code},
                )
                return

            if _now() > deadline:
                full_log = "".join(buffered)
                _set_status(
                    job_id,
                    status="FAILED",
                    finished_at=_now(),
                    log_text=full_log,
                    error_summary=(
                        f"Gave up waiting after {JOB_TIMEOUT_HOURS} hours. "
                        "The backup may still be running on the target."
                    ),
                )
                _publish(job_id, {"type": "status", "status": "FAILED"})
                return

            # Persist progress periodically so a restart does not lose the log.
            if len(buffered) % 10 == 0:
                _set_status(job_id, log_text="".join(buffered))

            await asyncio.sleep(POLL_INTERVAL)


def start(job_id: int) -> None:
    """Schedule a job on the event loop."""
    task = asyncio.create_task(run_job(job_id))
    _running[job_id] = task


async def cancel(job_id: int) -> tuple[bool, str]:
    """Signal the remote process group, then stop following the job."""
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if job is None:
            return False, "no such job"
        if job.status not in ("RUNNING", "QUEUED"):
            return False, f"job is {job.status}, nothing to cancel"
        target = db.get(Target, job.target_id)
        pid = job.pid

    if pid and target:
        try:
            async with await _connect(target) as conn:
                # Negative pid signals the whole process group.
                await conn.run(f"kill -TERM -{int(pid)}", check=False)
        except (OSError, asyncssh.Error) as exc:
            return False, f"could not signal the target: {exc}"

    task = _running.get(job_id)
    if task:
        task.cancel()
    else:
        _set_status(job_id, status="CANCELLED", finished_at=_now())

    return True, "cancellation signalled"
