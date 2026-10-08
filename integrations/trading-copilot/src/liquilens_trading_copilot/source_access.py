"""Read a dedicated source credential without accepting broker environment files."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path


def _systemd_credential(path: Path, info: os.stat_result, fd: int) -> bool:
    """Ubuntu systemd uses root-owned credentials with a service-specific ACL.

    Accept that format only in the supplied credential directory on a read-only
    mount. Ordinary group-readable files remain forbidden.
    """
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if (
        not directory
        or path.parent != Path(directory)
        or path.parent.parent != Path("/run/credentials")
        or info.st_uid != 0
        or info.st_gid != 0
        or stat.S_IMODE(info.st_mode) != 0o440
    ):
        return False
    parent = path.parent.lstat()
    return (
        stat.S_ISDIR(parent.st_mode)
        and parent.st_uid == 0
        and parent.st_gid == 0
        and stat.S_IMODE(parent.st_mode) == 0o550
        and bool(os.fstatvfs(fd).f_flag & os.ST_RDONLY)
    )


def read_source_token(path: Path) -> str:
    """Accept owner-only regular credentials, including systemd's read-only copy."""
    fd = None
    try:
        if not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        private_owner = before.st_uid == os.geteuid() and stat.S_IMODE(
            before.st_mode
        ) in (0o400, 0o600)
        if not (
            stat.S_ISREG(before.st_mode)
            and (private_owner or _systemd_credential(path, before, fd))
            and before.st_nlink == 1
            and 16 <= before.st_size <= 8192
        ):
            raise ValueError
        raw = os.read(fd, 8193)
        after = os.fstat(fd)
        current = path.lstat()

        def identity(info: os.stat_result) -> tuple[int, ...]:
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if identity(before) != identity(after) or identity(before) != identity(current):
            raise ValueError
        token = raw.removesuffix(b"\n").decode("ascii")
        if re.fullmatch(r"[A-Za-z0-9_.-]{16,8192}", token) is None:
            raise ValueError
        return token
    except (OSError, ValueError, UnicodeError):
        raise ValueError("source_credential_unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)
