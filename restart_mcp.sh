#!/bin/sh
# Kill all running menu_server.py MCP processes so the next Claude Code
# tool call respawns a fresh one with current code. Each interactive
# session's MCP connection is a long-lived subprocess that does NOT pick up
# edits to menu_server.py until it's restarted — run this after any change
# to mcp/menu_server.py, or if get_workflow_state() reports stale_code_warning.
#
# Safe to run any time: killing these processes only drops the MCP stdio
# connection for whichever Claude Code session(s) are using them. It does
# not touch the sms-assistant bridge (which spawns its own fresh subprocess
# per tool call and is never affected by this).

pids=$(pgrep -f "mcp/menu_server.py")

if [ -z "$pids" ]; then
    echo "No menu_server.py processes running."
    exit 0
fi

echo "Killing menu_server.py processes: $pids"
kill $pids
echo "Done. Reconnect (restart Claude Code / your MCP client) to respawn."
