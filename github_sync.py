#!/usr/bin/env python3
"""
github_sync.py -- publish recipe/condiment .md files to the menubuilder-recipes
GitHub Pages repo, from any account.

The repo and its GitHub credentials belong to davidallison, but most new
recipes arrive via Keanu (sms-assistant runs as allisonbot), which can't
write to the repo or push. So, same spool-file handoff as gui_launch.py:

  - request_publish() writes a one-shot request file into a world-writable
    spool dir. Safe to call from any account; never touches git.
  - main() (this file, run as davidallison by the com.menubuilder.githubsync
    LaunchAgent on WatchPaths + a 10-min fallback) drains the spool, copies
    the files into the repo, regenerates _data/recipes.json, commits and
    pushes.
  - publish() is the same work, called directly by recipe_review_server.py,
    which already runs as davidallison.

Push behaviour is deliberately unchanged from the old Review UI sync: no
fetch/rebase (held pending David's call, see project_github_sync_hardening
memory). A failed push leaves the commit in place; the next run pushes it
again if the repo is still ahead of origin.
"""

import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).parent
_CONFIG = json.loads((_HERE / "config.json").read_text())
_COOKING_DIR = Path(_CONFIG["metadata_path"].replace("~", "/Users/davidallison")).parent
RECIPES_DIR = _COOKING_DIR / "recipes"
CONDIMENTS_DIR = _COOKING_DIR / "condiments"
REPO_DIR = _HERE.parent / "menubuilder-recipes"
GENERATE_SCRIPT = _HERE / "generate_github_pages_data.py"
_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def publish_requests_dir() -> Path:
    return Path(_CONFIG.get("state_dir", "/Users/Shared/cooking-state")) / "publish_requests"


def request_publish(recipes: list = (), condiments: list = ()) -> None:
    """Queue .md files (bare filenames or paths) for publishing. Any account.

    recipes: files in the Dropbox recipes/ dir; condiments: files in condiments/.
    No-ops if both are empty.
    """
    recipes = [Path(r).name for r in recipes if r]
    condiments = [Path(c).name for c in condiments if c]
    if not recipes and not condiments:
        return
    reqs_dir = publish_requests_dir()
    existed = reqs_dir.exists()
    reqs_dir.mkdir(parents=True, exist_ok=True)
    if not existed:
        os.chmod(reqs_dir, 0o777)  # no shared group between davidallison and allisonbot
    path = reqs_dir / f"{time.time_ns()}_{os.getpid()}.json"
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps({"recipes": recipes, "condiments": condiments, "requested_at": time.time()}))
    os.chmod(path, 0o666)  # os.open's mode is umask-masked


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO_DIR), capture_output=True, text=True, env=_ENV)


def _log(msg: str) -> None:
    print(f"[github-sync {time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def publish(recipes: list = (), condiments: list = (), message: str = "") -> bool:
    """Copy files into the repo, regenerate data, commit, push. Returns True if
    the repo ends up pushed (or had nothing to push). Serialized by a lock so a
    Review UI add and a spool drain can't interleave git commands."""
    with open(REPO_DIR / ".git" / "menubuilder-sync.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        added = []
        for name in recipes:
            src = RECIPES_DIR / Path(name).name
            if src.exists():
                shutil.copy2(src, REPO_DIR / src.name)
                added.append(src.name)
            else:
                _log(f"source not found: {src}")
        for name in condiments:
            src = CONDIMENTS_DIR / Path(name).name
            if src.exists():
                (REPO_DIR / "condiments").mkdir(exist_ok=True)
                shutil.copy2(src, REPO_DIR / "condiments" / src.name)
                added.append(f"condiments/{src.name}")
            else:
                _log(f"source not found: {src}")

        gen = subprocess.run([sys.executable, str(GENERATE_SCRIPT)], capture_output=True, text=True,
                             cwd=str(REPO_DIR))
        if gen.returncode != 0:
            _log(f"generate script failed: {gen.stderr.strip()}")

        _git("add", "_data/recipes.json", *added)
        if _git("diff", "--cached", "--quiet").returncode != 0:
            msg = message or ("feat: add " + ", ".join(Path(a).name for a in added) if added
                              else "Regenerate recipe data")
            commit = _git("commit", "-m", msg)
            if commit.returncode != 0:
                _log(f"commit failed: {commit.stderr.strip()}")
                return False

        return _push_if_ahead()


def _push_if_ahead() -> bool:
    ahead = _git("rev-list", "--count", "origin/main..HEAD").stdout.strip()
    if ahead in ("", "0"):
        return True
    push = _git("push")
    if push.returncode != 0:
        _log(f"push failed ({ahead} commit(s) waiting, will retry next run): {push.stderr.strip()}")
        return False
    _log(f"pushed {ahead} commit(s)")
    return True


def main() -> int:
    """Drain the spool (LaunchAgent entry point)."""
    reqs_dir = publish_requests_dir()
    files = sorted(reqs_dir.glob("*.json")) if reqs_dir.exists() else []
    recipes, condiments = [], []
    for f in files:
        try:
            req = json.loads(f.read_text())
        except Exception as e:
            _log(f"bad request file {f.name}: {e}")
            continue
        recipes += [r for r in req.get("recipes", []) if r not in recipes]
        condiments += [c for c in req.get("condiments", []) if c not in condiments]
    if not recipes and not condiments:
        # Empty spool (10-minute tick): only retry a push that failed earlier --
        # don't regenerate/commit data churn nobody asked to publish.
        with open(REPO_DIR / ".git" / "menubuilder-sync.lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            ok = _push_if_ahead()
    else:
        ok = publish(recipes, condiments, message="feat: add " + ", ".join(recipes + condiments))
    # The files are committed locally either way; a failed push is retried
    # from the repo's own state, so the requests are done.
    for f in files:
        f.unlink(missing_ok=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
