#!/bin/bash
# Direct-write fallback for WeeklyShoppingList.app (Aug 22 2026).
#
# The compiled .app bundle failed every full end-to-end test today, even
# after fixing the real script bugs (Rosetta translation, silent hang on
# uncaught error, a catastrophic per-item property-access loop) -- Reminders
# itself intermittently times out on Apple Events sent by that compiled app,
# reproducible even with a 2-line throwaware test app. A raw `osascript`
# call issued from a Terminal-hosted shell (this script's own invocation
# path) succeeded reliably in every test today instead, because Terminal
# already carries a long-standing kTCCServiceReminders grant that a fresh
# process doesn't have.
#
# Run this directly from a Terminal window (or anything launched from one)
# rather than double-clicking WeeklyShoppingList.app.
exec /usr/bin/osascript "/Users/davidallison/projects/personal/MenuBuilder/WeeklyShoppingList_backup.applescript"
