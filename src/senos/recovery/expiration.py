"""Independent systemd-owned recovery worker for a single authorized session."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import time

from senos.executor.application import CPUApplication, process_identity
from senos.storage.execution import ExecutionStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--session", required=True)
    args = parser.parse_args()
    store = ExecutionStore(args.db)
    store.ready(args.session)
    while True:
        session = store.get(args.session)
        if session["status"] != "active":
            return
        alive = process_identity(session["owner_pid"]) == session["owner_start"]
        if not alive or datetime.now(timezone.utc) >= datetime.fromisoformat(session["deadline"]):
            CPUApplication(args.db).finish(args.session)
            return
        time.sleep(1)


if __name__ == "__main__":
    main()
