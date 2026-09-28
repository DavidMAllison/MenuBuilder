"""
eventkit_helpers.py -- shared EventKit boilerplate for shopping_list_sync.py /
meal_calendar_sync.py. No business logic here, just auth/store/lookup/logging.

Why this exists: AppleScript automation of Reminders.app/Calendar.app proved
fundamentally unreliable on this Mac (Aug 22 2026) -- Apple Events sent by a
compiled AppleScript app intermittently timed out waiting for the GUI app to
respond, reproduced even in a 2-line throwaway test, recoverable only by
quitting and relaunching the target app. EventKit talks directly to the
underlying data store daemon instead of routing through the GUI app's own
Apple-Events bridge, so it doesn't depend on Reminders.app/Calendar.app being
launched, foregrounded, or responsive at all.

EventKit's authorization API is completion-block based, and a plain script has
no NSApplication/CFRunLoop already spinning to receive the async reply -- the
wait helpers below pump the run loop in short slices instead of blocking on a
bare semaphore, which is the standard pattern for this in a non-GUI pyobjc
script.
"""

import logging
import time
from pathlib import Path

from EventKit import (
    EKCalendar,
    EKEntityTypeReminder,
    EKEventStore,
)
from Foundation import NSDate, NSRunLoop

EKAuthorizationStatusNotDetermined = 0
EKAuthorizationStatusRestricted = 1
EKAuthorizationStatusDenied = 2
EKAuthorizationStatusFullAccess = 3
EKAuthorizationStatusWriteOnly = 4

_ALLOWED_STATUSES = (EKAuthorizationStatusFullAccess, EKAuthorizationStatusWriteOnly)

_store = None


def get_store() -> EKEventStore:
    """Process-wide singleton -- constructing EKEventStore isn't free and
    there's no reason to pay for it twice in one script run."""
    global _store
    if _store is None:
        _store = EKEventStore.alloc().init()
    return _store


def _pump_run_loop_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    run_loop = NSRunLoop.currentRunLoop()
    while not predicate() and time.monotonic() < deadline:
        run_loop.runMode_beforeDate_(
            "NSDefaultRunLoopMode", NSDate.dateWithTimeIntervalSinceNow_(0.1)
        )
    return predicate()


def request_access(store: EKEventStore, entity_type: int, timeout: float = 30) -> int:
    """
    Ensure `store` is authorized for `entity_type` (EKEntityTypeReminder or
    EKEntityTypeEvent), requesting access if not yet determined. Returns the
    resulting EKAuthorizationStatus.

    Cheap synchronous pre-check first (no run-loop pump needed) -- skip the
    async request entirely if already resolved one way or the other. If
    already Denied/Restricted, requesting again will NOT show a fresh system
    dialog (macOS doesn't re-prompt after an explicit user decision) -- raise
    with a clear message pointing at System Settings instead of silently
    hanging or looping.
    """
    current = EKEventStore.authorizationStatusForEntityType_(entity_type)
    if current in _ALLOWED_STATUSES:
        return current
    if current in (EKAuthorizationStatusDenied, EKAuthorizationStatusRestricted):
        kind = "Reminders" if entity_type == EKEntityTypeReminder else "Calendar"
        raise PermissionError(
            f"{kind} access is already Denied/Restricted for this process identity "
            f"(status={current}) -- macOS will not re-prompt. Enable it manually in "
            f"System Settings -> Privacy & Security -> {kind}, then re-run."
        )

    result = {"done": False, "granted": False, "error": None}

    def _completion(granted, error):
        result["granted"] = bool(granted)
        result["error"] = error
        result["done"] = True

    if entity_type == EKEntityTypeReminder:
        store.requestFullAccessToRemindersWithCompletion_(_completion)
    else:
        store.requestFullAccessToEventsWithCompletion_(_completion)

    if not _pump_run_loop_until(lambda: result["done"], timeout):
        raise TimeoutError(
            f"EventKit access request timed out after {timeout}s -- "
            "if a system permission dialog is on screen, it needs a human to click it."
        )
    if result["error"] is not None:
        raise RuntimeError(f"EventKit access request errored: {result['error']}")

    return EKEventStore.authorizationStatusForEntityType_(entity_type)


def find_calendar_by_title(store: EKEventStore, entity_type: int, title: str, prefer_source_title: str = "iCloud"):
    """Equivalent to AppleScript's `list "Grocery"` / `first calendar whose
    name is "Calendar"`. Raises LookupError rather than silently guessing if
    the title is ambiguous across multiple sources -- the old AppleScript's
    implicit "first match" behavior was never validated against a real
    multi-source case, and a write to the wrong account's list is worse than
    a loud failure."""
    cals = [c for c in store.calendarsForEntityType_(entity_type) if c.title() == title]
    if not cals:
        return None
    if len(cals) > 1:
        by_source = [c for c in cals if c.source().title() == prefer_source_title]
        if len(by_source) == 1:
            logging.getLogger(__name__).warning(
                "Ambiguous %r match across sources %s -- using the %r one",
                title, [c.source().title() for c in cals], prefer_source_title,
            )
            return by_source[0]
        raise LookupError(
            f"Ambiguous {title!r}: {len(cals)} matches across sources "
            f"{[c.source().title() for c in cals]}, none uniquely {prefer_source_title!r}"
        )
    return cals[0]


def find_or_create_reminder_list(store: EKEventStore, title: str, prefer_source_title: str = "iCloud"):
    """Reminders-only: create the list if it doesn't exist yet, matching
    today's AppleScript (`if not (exists list listName) then make new
    list...`). Calendar sync deliberately does NOT get this treatment --
    today's AppleScript assumes "Calendar" already exists and so does the
    replacement, see meal_calendar_sync.py."""
    existing = find_calendar_by_title(store, EKEntityTypeReminder, title, prefer_source_title)
    if existing is not None:
        return existing

    sources = [s for s in store.sources() if s.title() == prefer_source_title]
    source = sources[0] if sources else store.defaultCalendarForNewReminders().source()

    new_list = EKCalendar.calendarForEntityType_eventStore_(EKEntityTypeReminder, store)
    new_list.setTitle_(title)
    new_list.setSource_(source)
    ok, err = store.saveCalendar_commit_error_(new_list, True, None)
    if not ok:
        raise RuntimeError(f"Could not create reminder list {title!r}: {err}")
    return new_list


def fetch_reminders(store: EKEventStore, predicate, timeout: float = 30):
    """fetchRemindersMatchingPredicate:completion: is async (unlike event
    fetching, which is synchronous) -- same run-loop-pump wait as
    request_access. Replaces the AppleScript's `whose`-filtered queries with
    ONE round trip; all filtering happens in-process afterward."""
    result = {"done": False, "reminders": None}

    def _completion(reminders):
        result["reminders"] = list(reminders) if reminders is not None else []
        result["done"] = True

    store.fetchRemindersMatchingPredicate_completion_(predicate, _completion)

    if not _pump_run_loop_until(lambda: result["done"], timeout):
        raise TimeoutError(f"fetchRemindersMatchingPredicate timed out after {timeout}s")
    return result["reminders"]


def configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger(log_path.stem)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_path)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
