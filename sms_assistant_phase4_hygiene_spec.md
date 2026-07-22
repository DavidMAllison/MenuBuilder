# Spec: Phase 4 engineering hygiene — sms-assistant items

**Where**: `/Users/Shared/sms-assistant/`
**Why**: `architecture_review_and_fix_plan.md` (MenuBuilder project root) Phase 4 covers
engineering hygiene across both projects. The MenuBuilder-owned items (4.1 requirements
pinning, 4.2 ruff+tests pre-push, 4.3 scoring dedup, 4.5 secrets→.env) are done and
committed on that side. This spec covers everything left that's sms-assistant-owned,
verified against the actual current code (not just the doc's original bullets, which
were written before this code existed).

Paste this into an sms-assistant session and apply directly — nothing here touches
MenuBuilder files.

---

## Item A — Document (and optionally end) the Python interpreter split

**Finding**: `README.md`'s Prerequisites section says "Python 3.11+ installed on the bot
account (via Homebrew: `brew install python@3.11`)" — but the actual running process
uses whatever `python3` resolves to via `start.sh`'s bare `python3 server.py`, which on
the bot account resolves to **Apple's Command Line Tools Python 3.9.6** (`python3
--version` → `Python 3.9.6`, no `python3.9` binary on PATH, no `.venv` anywhere in the
project). The README's prerequisite was never actually satisfied — either Homebrew
Python was never installed on the bot account, or something else takes PATH priority.
This is exactly why `menubuilder_bridge.py` and `groceryagent_bridge.py` exist: they
shell out to MenuBuilder's and GroceryAgent's own `.venv/bin/python3.12` because
sms-assistant's own interpreter (3.9) can't run code written against 3.12-only syntax
or dependencies.

**Option 1 (minimal — just fix the docs to match reality):**

In `README.md`, replace:
```markdown
- Python 3.11+ installed on the bot account (via Homebrew: `brew install python@3.11`)
```
with:
```markdown
- Python 3.9 (macOS Command Line Tools default — `python3 --version` should show 3.9.x).
  sms-assistant intentionally runs on the CLT Python; MenuBuilder and GroceryAgent each
  run in their own newer venv (3.12) and are called via subprocess bridges
  (`menubuilder_bridge.py`, `groceryagent_bridge.py`) rather than imported directly —
  see those files for why. Don't "fix" this by installing Python 3.11 on the bot
  account; it won't be used unless `start.sh` is also repointed to it.
```

**Option 2 (bigger — actually end the split):**

`brew install python@3.12` on the bot account, repoint `start.sh` to the Homebrew
binary explicitly (e.g. `/opt/homebrew/opt/python@3.12/bin/python3.12 server.py`
instead of bare `python3`), reinstall `requirements.txt` into that interpreter, and
retire the two subprocess bridges in favor of direct imports. This is a real migration
(new venv, dependency reinstall, re-test the whole Sunday flow, both bridge files
become dead code to remove) — not a drop-in swap. Recommend treating it as its own
task if you want it, not bundled into this hygiene pass.

**Recommendation**: do Option 1 now (it's a doc fix, zero risk). Revisit Option 2 later
if the bridge subprocess overhead or the two-interpreter complexity becomes a real
problem — it isn't causing failures today, just confusion in the docs.

---

## Item B — `keanu.log` rotation (ops step, not code)

**Finding**: `com.keanu.sms-assistant.plist` redirects both `StandardOutPath` and
`StandardErrorPath` to `/Users/Shared/sms-assistant/keanu.log`. Nothing rotates it —
`logging.basicConfig()` in `server.py` has no `filename=`, so all the actual file
writing happens via launchd's redirect, not Python's logging module. Currently 753 KB
and growing forever with no cap.

This isn't a code change — the file is written by launchd, not by anything `unittest`
or `py_compile` would touch. The standard macOS-native fix is `newsyslog`, which
rotates any file without requiring the writing process to know about rotation at all.

**Fix (run once, as an admin — needs `sudo`, so this is for David, not a code paste):**

Create `/etc/newsyslog.d/keanu.conf`:
```
# logfilename                                  [owner:group]     mode  count  size(KB)  when  flags
/Users/Shared/sms-assistant/keanu.log          allisonbot:staff  644   5      10240     *     J
```
This keeps 5 rotated copies, rotates at 10 MB, compresses old ones (`J` = bzip2).
`newsyslog` runs via a system launchd job already present on macOS — no service to
install. Verify with `sudo newsyslog -nvv` (dry run, shows what it would rotate).

**Do not** try to solve this by adding a `RotatingFileHandler` in `server.py` unless
you also remove the `StandardOutPath`/`StandardErrorPath` redirect from the plist —
otherwise you'd have two independent things writing to the same growing file and the
Python-side rotation wouldn't actually cap anything, since launchd's redirect isn't
routed through it.

---

## Item C — Bridge: log full stdout/stderr on JSON parse failure

**Finding**: Both bridge files lose the actual content that failed to parse when
`json.loads()` raises — they only log the exception's short message (e.g. `"Expecting
value: line 1 column 1 (char 0)"`), which doesn't tell you *what* the subprocess
actually printed (a stray `print()`, a warning, a partial traceback mixed into stdout,
etc.). This is the hardest class of bug to debug after the fact since the original
output is gone by the time you're looking at `keanu.log`.

### C1 — `menubuilder_bridge.py`

Current (lines 44–50):
```python
        if r.returncode != 0:
            log.error(f"MenuBuilder tool {tool_name} failed: {r.stderr.strip()}")
            return {"error": r.stderr.strip()}
        return json.loads(r.stdout.strip())
    except Exception as e:
        log.error(f"MenuBuilder bridge error ({tool_name}): {e}")
        return {"error": str(e)}
```

Replace with:
```python
        if r.returncode != 0:
            log.error(f"MenuBuilder tool {tool_name} failed: {r.stderr.strip()}")
            return {"error": r.stderr.strip()}
        try:
            return json.loads(r.stdout.strip())
        except json.JSONDecodeError as e:
            log.error(
                f"MenuBuilder tool {tool_name} returned non-JSON stdout: {e}\n"
                f"  stdout={r.stdout!r}\n  stderr={r.stderr!r}"
            )
            return {"error": f"invalid JSON from MenuBuilder tool {tool_name}: {e}"}
    except Exception as e:
        log.error(f"MenuBuilder bridge error ({tool_name}): {e}")
        return {"error": str(e)}
```

### C2 — `groceryagent_bridge.py`

Current (lines 52–54, the function-level `except` that catches the final
`return json.loads(raw)` at line 48):
```python
    except json.JSONDecodeError as e:
        log.error(f"GroceryAgent receipt_parser returned invalid JSON: {e}")
        return {"error": f"invalid JSON from receipt_parser: {e}"}
```

Replace with:
```python
    except json.JSONDecodeError as e:
        log.error(f"GroceryAgent receipt_parser returned invalid JSON: {e}\n  raw={raw!r}")
        return {"error": f"invalid JSON from receipt_parser: {e}"}
```
(`raw` is already bound earlier in the `try` block at line 44 — `raw = r.stdout.strip()`
— before the point of failure, so it's in scope in this `except`.)

No other changes needed in this file — the `if r.returncode != 0` branch above it
already logs full `stdout`/`stderr` on failure.

---

## Not in scope here — flag for a separate GroceryAgent spec

The original doc's Phase 4.6 ("Receipt parser: request JSON via a `tool` schema or
strip to first `{...}` block before parsing; sniff image magic bytes instead of
trusting mime labels") is about `receipt_parser.py`, which lives in
**`/Users/davidallison/projects/personal/GroceryAgent/`** — a separate project, not
sms-assistant. `groceryagent_bridge.py` (sms-assistant side, fixed above) just calls
into it as a subprocess.

Checked the actual code there:
- The "strip to first `{...}` block" fallback **already exists** (`receipt_parser.py`
  lines 113–120, regex `\{.*\}` extraction on `JSONDecodeError`) — that part of 4.6 is
  effectively done, doc was stale.
- The mime-sniffing gap is real: `media_type = MEDIA_TYPES.get(path.suffix.lower(),
  "image/jpeg")` picks the media type sent to Claude Vision purely from the file
  extension, not the actual image bytes. A misnamed file (e.g. a PNG saved as `.jpg`)
  would send the wrong `media_type`.

This needs its own handoff into a GroceryAgent session, not this one — flagging it here
so it doesn't get lost, not attempting it.
