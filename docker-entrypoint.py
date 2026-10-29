"""Container entrypoint: take ownership of the data volume as root, then run uvicorn as `appuser`.

Volumes (e.g. on Fly.io) are mounted root-owned, so the unprivileged user can't write the cached
Kite token without this. Runs a single worker: the Kite session, chat and research jobs live in memory.
"""

import os
import pwd

data_dir = os.environ.get("DATA_DIR", "/data")
user = pwd.getpwnam("appuser")

os.makedirs(data_dir, exist_ok=True)
os.chown(data_dir, user.pw_uid, user.pw_gid)

os.setgroups([])
os.setgid(user.pw_gid)
os.setuid(user.pw_uid)
os.environ["HOME"] = user.pw_dir

os.execvp(
    "uvicorn",
    [
        "uvicorn", "app.main:app",
        "--host", "0.0.0.0",
        "--port", os.environ.get("PORT", "8000"),
        "--workers", "1",
        "--proxy-headers", "--forwarded-allow-ips", "*",
    ],
)
