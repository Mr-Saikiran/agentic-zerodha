"""Container entrypoint: runs a single uvicorn worker (chat history and research jobs live in memory).

Started as root (Docker, Fly.io), it takes ownership of the data dir and drops to `appuser`.
Started as a non-root user (Hugging Face Spaces runs containers as UID 1000), it just starts uvicorn.
"""

import os
import pwd

if os.geteuid() == 0:
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
