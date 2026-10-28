"""Project commands that work the same on Linux, macOS and Windows (standard library only).

    python tasks.py setup       virtual environment, .env, PostGIS container, staging table, raw answers
    python tasks.py demo        the pipeline on a few saved real answers, twice (no network)
    python tasks.py reproduce   the full run offline from data/raw: 755 plans, then again
    python tasks.py test        all tests
    python tasks.py validate    the validation queries (sql/02_validation.sql)
    python tasks.py qa          GeoJSON, CSV and the quick-look map, to out/
    python tasks.py ui          the dashboard (Streamlit) on http://localhost:8501
    python tasks.py raw         unpack and verify data/raw/*.tar.gz
    python tasks.py fetch       download fresh raw answers from the live portal (30 minutes or more)
    python tasks.py harvest     small live run, first 20 detail pages
    python tasks.py clean       remove generated files

`setup` can be started with any Python 3.11 or newer; the environment is built from that interpreter.
The Makefile only calls this file, for people who prefer `make`.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
BIN = VENV / ("Scripts" if sys.platform == "win32" else "bin")
ENV_FILE = ROOT / ".env"
FIRST_PORT = 5434
ENVIRONMENT = {"PYTHONUTF8": "1"}  # German text in reports and logs, also on a Windows console


def fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def tool(name):
    """Path of a program inside the virtual environment."""
    suffix = ".exe" if sys.platform == "win32" else ""
    path = BIN / f"{name}{suffix}"
    if not path.exists():
        fail("no virtual environment yet. Run 'python tasks.py setup' first.")
    return str(path)


def run(command, **options):
    environment = {**os.environ, **ENVIRONMENT}
    try:
        subprocess.run([str(part) for part in command], cwd=ROOT, check=True, env=environment, **options)
    except FileNotFoundError:
        fail(f"'{command[0]}' was not found")
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)


def read_env():
    """The KEY=value lines of .env (comments and empty lines are skipped)."""
    if not ENV_FILE.exists():
        fail("no .env yet. Run 'python tasks.py setup' first (or copy .env.example to .env).")
    values = {}
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def port_is_free(port):
    with socket.socket() as probe:
        try:
            probe.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def container_exists():
    result = subprocess.run(
        ["docker", "compose", "ps", "-a", "-q", "db"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return bool(result.stdout.strip())


def choose_port():
    """Keep the port in .env, unless another program uses it: then take the next free one."""
    values = read_env()
    wanted = int(values.get("POSTGRES_PORT", FIRST_PORT))
    if container_exists() or port_is_free(wanted):
        return  # our own container already owns it, or it is free
    port = next((p for p in range(wanted + 1, wanted + 100) if port_is_free(p)), None)
    if port is None:
        fail(f"port {wanted} is in use and no free port was found above it; set POSTGRES_PORT in .env")
    text = ENV_FILE.read_text(encoding="utf-8")
    text = re.sub(r"(?m)^POSTGRES_PORT=.*$", f"POSTGRES_PORT={port}", text)
    text = re.sub(r"(?m)^(DATABASE_URL=.*@[^:/]+):\d+/", rf"\1:{port}/", text)
    ENV_FILE.write_text(text, encoding="utf-8")
    print(f"port {wanted} is in use; the database will use port {port} (written to .env)")


def setup():
    if sys.version_info < (3, 11):
        fail(
            f"Python 3.11 or newer is needed, this is {sys.version.split()[0]}. "
            "Start the setup with a newer one, e.g. 'python3.12 tasks.py setup' or 'py -3.12 tasks.py setup'."
        )
    if not shutil.which("docker"):
        fail("Docker was not found. It runs the local PostGIS database (https://docs.docker.com/get-docker/).")
    if subprocess.run(["docker", "compose", "version"], capture_output=True, check=False).returncode != 0:
        fail("'docker compose' is not available; install Docker with the Compose plugin.")
    if not (BIN / ("python.exe" if sys.platform == "win32" else "python")).exists():
        run([sys.executable, "-m", "venv", VENV])
    python = tool("python")
    # exact versions from the lock file, then the project itself (editable, so that 'harvest' exists)
    run([python, "-m", "pip", "install", "-q", "-r", "requirements.lock"])
    run([python, "-m", "pip", "install", "-q", "--no-deps", "-e", "."])
    if not ENV_FILE.exists():
        shutil.copy(ROOT / ".env.example", ENV_FILE)
        print("created .env")
    choose_port()
    run(["docker", "compose", "up", "-d", "--wait"])
    run([tool("harvest"), "--init-db"])
    run([python, "-m", "harvester.raw", "unpack"])
    print("\nready. Next: python tasks.py demo | test | validate | reproduce")


def validate():
    values = read_env()
    with (ROOT / "sql" / "02_validation.sql").open("rb") as queries:
        run(
            ["docker", "compose", "exec", "-T", "db", "psql", "-U", values["POSTGRES_USER"],
             "-d", values["POSTGRES_DB"], "-f", "-"],
            stdin=queries,
        )  # fmt: skip


def clean():
    for name in ("out", ".pytest_cache", ".ruff_cache"):
        shutil.rmtree(ROOT / name, ignore_errors=True)


def reproduce():
    run([tool("python"), "-m", "harvester.raw", "unpack"])
    run([tool("harvest"), "--offline", "--report", "out/full_run.json"])
    run([tool("harvest"), "--offline", "--report", "out/full_run_2.json"])


COMMANDS = {
    "setup": setup,
    "raw": lambda: run([tool("python"), "-m", "harvester.raw", "unpack"]),
    "fetch": lambda: run([tool("harvest"), "--dry-run", "--report", "out/fetch.json"]),
    "reproduce": reproduce,
    "demo": lambda: run([tool("python"), "scripts/demo_offline.py"]),
    "test": lambda: run([tool("python"), "-m", "pytest", "-q"]),
    "harvest": lambda: run([tool("harvest"), "--max-details", "20", "--report", "out/report.json"]),
    "validate": validate,
    "qa": lambda: run([tool("python"), "qa/quicklook.py"]),
    "ui": lambda: run([tool("python"), "-m", "streamlit", "run", "ui/app.py"]),
    "clean": clean,
}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(__doc__.split("\n\n")[0])
        print("commands:", ", ".join(COMMANDS))
        return 0 if len(argv) == 1 else 1
    COMMANDS[argv[1]]()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
