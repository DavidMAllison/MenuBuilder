#!/usr/bin/env python3
"""
build_weekly_recipe_email.py -- Build the weekly "new recipe ideas" email
(10 picks, sent to both David and Ashley every Saturday morning).

Selection:
  - Reuses pick_ashley_batch.gather_candidates() for the same agent_results/*.json
    queue + active-collection dedup it already does -- which also already excludes
    anything previously sent in an Ashley SMS batch (ashley_batch_sent.json), so the
    same idea never shows up twice across the two channels.
  - Additionally excludes anything already sent in a prior weekly email
    (email_batch_sent.json).
  - Picks are shuffled (not sorted by cook time -- that bias is specific to Ashley's
    time-focused SMS batch) for cuisine/type variety.

Writes:
  - weekly_email_batch.json  -- display-only record of this week's picks, mirrors
    ashley_recipe_batch.json's shape.
  - email_action_tokens.json -- token -> full recipe payload, consumed by
    recipe_review_server.py's /api/email_action routes (Save / Add to Queue /
    Not Interested). Single-use, 30-day expiry (see EMAIL_TOKEN_TTL_DAYS there).
  - Appends picks to email_batch_sent.json.

Does NOT send anything -- prints {"subject", "html", "recipients"} as JSON to
stdout. The launchd-triggered headless `claude -p` step reads this and sends it
via the Gmail MCP connector.

Usage:
    python3 build_weekly_recipe_email.py            # generate + write, print email JSON
    python3 build_weekly_recipe_email.py --dry-run   # preview without writing state
"""

import argparse
import hashlib
import json
import random
import secrets
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pick_ashley_batch  # noqa: E402

# Hardlinked to ~/Dropbox/LLMContext/cooking (same inode) -- never Path.home()
# here: this script is also invoked from a headless launchd-triggered process,
# which may not share the interactive login session's resolved home directory.
STATE_DIR = Path("/Users/Shared/cooking-state")
EMAIL_BATCH_PATH = STATE_DIR / "weekly_email_batch.json"
EMAIL_TOKENS_PATH = STATE_DIR / "email_action_tokens.json"
EMAIL_SENT_LOG_PATH = STATE_DIR / "email_batch_sent.json"
CONFIG_PATH = Path(__file__).parent / "config.json"

BATCH_SIZE = 10


def _email_sent_sets() -> tuple[set, set]:
    if not EMAIL_SENT_LOG_PATH.exists():
        return set(), set()
    try:
        entries = json.loads(EMAIL_SENT_LOG_PATH.read_text())
    except Exception:
        return set(), set()
    urls = {(e.get("url", "") or "").rstrip("/") for e in entries}
    titles = {pick_ashley_batch._normalize_title(e.get("title", "")) for e in entries}
    return urls, titles


def gather_email_candidates() -> list:
    """Full-record candidates (ingredients/instructions intact), already deduped
    against the active collection and Ashley's SMS sent-log by gather_candidates();
    additionally excludes anything already sent in a prior weekly email."""
    candidates = pick_ashley_batch.gather_candidates()
    sent_urls, sent_titles = _email_sent_sets()
    return [
        c for c in candidates
        if (c.get("url", "") or "").rstrip("/") not in sent_urls
        and pick_ashley_batch._normalize_title(c.get("title", "")) not in sent_titles
    ]


def pick_batch(candidates: list, n: int = BATCH_SIZE) -> list:
    return random.sample(candidates, min(n, len(candidates)))


def _write_tokens(picks: list) -> dict:
    """token -> full recipe payload, for the /api/email_action routes."""
    tokens = {}
    if EMAIL_TOKENS_PATH.exists():
        try:
            tokens = json.loads(EMAIL_TOKENS_PATH.read_text())
        except Exception:
            tokens = {}

    batch_id = f"email-{date.today().isoformat()}"
    created_at = date.today().isoformat()
    token_for = {}
    for r in picks:
        token = secrets.token_urlsafe(16)
        token_for[r["title"]] = token
        tokens[token] = {
            "title":        r.get("title", ""),
            "url":          r.get("url", ""),
            "source":       pick_ashley_batch._clean_source_name(r.get("source", "")),
            "time":         r.get("time", ""),
            "image":        r.get("image", ""),
            "ingredients":  r.get("ingredients", []),
            "instructions": r.get("instructions", []),
            "cuisine":      r.get("cuisine", ""),
            "yield":        r.get("yield", ""),
            "video_url":    r.get("video_url", ""),
            "batch_id":     batch_id,
            "created_at":   created_at,
            "used":         False,
        }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    EMAIL_TOKENS_PATH.write_text(json.dumps(tokens, indent=2, ensure_ascii=False))
    try:
        EMAIL_TOKENS_PATH.chmod(0o666)
    except Exception:
        pass
    return token_for


# Same palette as SOURCE_COLORS in recipe_review/index.html's sourceColor().
# The site assigns colors in first-seen order within a session (stateful,
# non-deterministic across page loads); a stable hash is used here instead
# since the email has no equivalent running session to key off of.
_SOURCE_COLORS = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#d35400", "#16a085", "#2c3e50", "#f39c12"]

# Exact SVG icon paths from recipe_review/index.html's modal-action buttons
# (add-btn, queue-add-btn, dismiss-btn) and visitBtn()'s external-link icon.
_ICON_VIEW = '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>'
_ICON_ADD = '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/><line x1="12" y1="9" x2="12" y2="15"/><line x1="9" y1="12" x2="15" y2="12"/>'
_ICON_QUEUE = '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>'
_ICON_REMOVE = '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>'


def _source_color(source: str) -> str:
    return _SOURCE_COLORS[int(hashlib.md5(source.encode()).hexdigest(), 16) % len(_SOURCE_COLORS)]


def _render_card(r: dict, token_for: dict, base_url: str) -> str:
    """Mirrors recipe_review/index.html's recipe detail modal (_showModal +
    the New-view actionHtml/visitHtml toolbar) as closely as static email HTML
    allows: full-width image, colored source badge + cuisine, title, time
    meta with a divider, then the same 4 actions (View/Add/Queue/Remove) with
    the same icons, colors, and labels as the site's modal-action bar."""
    title = r.get("title", "")
    token = token_for[title]
    image = r.get("image", "")
    source = pick_ashley_batch._clean_source_name(r.get("source", ""))
    cuisine = r.get("cuisine", "")
    if isinstance(cuisine, list):
        cuisine = ", ".join(cuisine)
    time_ = r.get("time", "")

    # Hotlinked directly to the source site (real internet domain) -- NOT
    # routed through the local review server, which is only reachable via
    # mDNS on the home network. Email image loads happen via the mail
    # provider's own cloud-side fetch (Apple's Mail Privacy Protection
    # relay, Gmail's image proxy), which can never resolve a .local
    # hostname regardless of hotlink protection concerns.
    #
    # width="100%" as an HTML attribute (not just CSS) is invalid -- the img
    # width attribute must be a plain integer per spec. Gmail's sanitizer is
    # strict enough to drop a tag over that, which is why images silently
    # never rendered even though the URLs themselves were fine. Numeric
    # pixel width/height attributes here, CSS still handles the responsive
    # override.
    img_html = (
        f'<img src="{image}" alt="" width="600" height="220" '
        f'style="width:100%;height:220px;object-fit:cover;display:block;">'
        if image else ""
    )

    badge_html = (
        f'<span style="background-color:{_source_color(source)};color:#fff;padding:3px 10px;'
        f'border-radius:12px;font-size:11px;font-weight:700;">{source}</span>'
        if source else ""
    )
    cuisine_html = f'<span style="font-size:12px;color:#888;margin-left:8px;">{cuisine}</span>' if cuisine else ""

    save_url = f"{base_url}/api/email_action?token={token}&action=save"
    queue_url = f"{base_url}/api/email_action?token={token}&action=queue"
    remove_url = f"{base_url}/api/email_action?token={token}&action=remove"
    view_url = r.get("url", "")

    def action(href, label, color, icon_paths):
        svg = f'<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2">{icon_paths}</svg>'
        return (
            f'<td width="25%" align="center" style="padding:6px 2px;">'
            f'<a href="{href}" style="display:block;text-decoration:none;color:{color};">'
            f'{svg}<div style="font-size:10px;font-weight:700;letter-spacing:0.3px;margin-top:3px;">{label}</div>'
            f"</a></td>"
        )

    actions = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
        + action(view_url, "View", "#2980b9", _ICON_VIEW)
        + action(save_url, "Add", "#27ae60", _ICON_ADD)
        + action(queue_url, "Queue", "#8e44ad", _ICON_QUEUE)
        + action(remove_url, "Remove", "#e67e22", _ICON_REMOVE)
        + "</tr></table>"
    )

    return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="max-width:600px;margin:0 auto 24px;border-radius:12px;overflow:hidden;font-family:-apple-system,BlinkMacSystemFont,sans-serif;background-color:#ffffff;box-shadow:0 1px 4px rgba(0,0,0,0.12);">
  <tr><td>{img_html}</td></tr>
  <tr><td style="padding:18px 20px 16px;">
    <div>{badge_html}{cuisine_html}</div>
    <div style="font-size:19px;font-weight:700;color:#111;line-height:1.3;margin-top:10px;">{title}</div>
    <div style="font-size:13px;color:#888;margin-top:6px;padding-bottom:14px;border-bottom:1px solid #eee;">{time_}</div>
  </td></tr>
  <tr><td style="padding:8px 12px 14px;">{actions}</td></tr>
</table>"""


def _render_html(picks: list, token_for: dict, base_url: str) -> str:
    view_more_url = f"{base_url}/?view=new"
    cards = "".join(_render_card(r, token_for, base_url) for r in picks)

    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:24px 12px;background-color:#f7f7f7;font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
<div style="max-width:600px;margin:0 auto 20px;text-align:center;">
  <h2 style="margin:0 0 6px;color:#222;">{len(picks)} New Recipe Ideas</h2>
  <p style="margin:0;color:#666;font-size:14px;">Fresh picks for this week's menu.</p>
</div>
{cards}
<div style="max-width:600px;margin:8px auto 0;text-align:center;">
  <a href="{view_more_url}" style="color:#2563eb;font-size:14px;text-decoration:none;">View more new ideas &rarr;</a>
</div>
</body></html>"""


def build(size: int = BATCH_SIZE, write: bool = True) -> dict | None:
    candidates = gather_email_candidates()
    if not candidates:
        return None

    picks = pick_batch(candidates, size)
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    base_url = config.get("review_ui_base_url", "").rstrip("/")
    recipients = [e for e in (config.get("david_email", ""), config.get("ashley_email", "")) if e]

    token_for = {r["title"]: secrets.token_urlsafe(16) for r in picks}  # preview tokens for dry-run
    if write:
        token_for = _write_tokens(picks)

        batch = {
            "batch_id": f"email-{date.today().isoformat()}",
            "generated_at": date.today().isoformat(),
            "picks": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "source": pick_ashley_batch._clean_source_name(r.get("source", "")),
                    "time": r.get("time", ""),
                    "image": r.get("image", ""),
                }
                for r in picks
            ],
        }
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        EMAIL_BATCH_PATH.write_text(json.dumps(batch, indent=2, ensure_ascii=False))
        try:
            EMAIL_BATCH_PATH.chmod(0o666)
        except Exception:
            pass

        sent_log = []
        if EMAIL_SENT_LOG_PATH.exists():
            try:
                sent_log = json.loads(EMAIL_SENT_LOG_PATH.read_text())
            except Exception:
                sent_log = []
        for p in batch["picks"]:
            sent_log.append({"title": p["title"], "url": p["url"], "sent_at": batch["generated_at"]})
        EMAIL_SENT_LOG_PATH.write_text(json.dumps(sent_log, indent=2, ensure_ascii=False))
        try:
            EMAIL_SENT_LOG_PATH.chmod(0o666)
        except Exception:
            pass

    html = _render_html(picks, token_for, base_url)
    subject = f"{len(picks)} New Recipe Ideas for the Week of {date.today().strftime('%b %-d')}"
    return {"subject": subject, "html": html, "recipients": recipients, "picks": len(picks)}


def send_email(subject: str, html: str, recipients: list) -> None:
    """Send via Gmail SMTP using an app password -- replaces the old headless
    `claude -p` + Gmail MCP send step, which silently stripped every <img> tag
    from the body (confirmed via isolated test) and couldn't carry image bytes
    through an LLM context window at any scale. Direct SMTP has neither
    limitation since the HTML goes out byte-for-byte, untouched by an LLM."""
    config = json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    from_addr = config.get("david_email", "")
    app_password = config.get("gmail_smtp_app_password", "")
    if not from_addr or not app_password:
        print(json.dumps({"error": "smtp_not_configured"}))
        sys.exit(1)
    if not recipients:
        print(json.dumps({"error": "no_recipients"}))
        sys.exit(1)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)
    msg.set_content("This email requires HTML to view.")
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(from_addr, app_password)
        smtp.send_message(msg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing state")
    parser.add_argument("--size", type=int, default=BATCH_SIZE, help="Number of picks")
    parser.add_argument("--send", action="store_true", help="Send the email via SMTP after building")
    args = parser.parse_args()

    result = build(size=args.size, write=not args.dry_run)
    if result is None:
        print(json.dumps({"error": "queue_empty"}))
        sys.exit(1)

    if args.send:
        send_email(result["subject"], result["html"], result["recipients"])
        print(json.dumps({"sent": True, "recipients": result["recipients"], "picks": result["picks"]}))
    else:
        print(json.dumps(result))


if __name__ == "__main__":
    main()
