#!/usr/bin/env bash
# Start, restart, or stop the sessions of this supervisor directory: sv, sv-watch, and sv-chat.
# Placed by `state_tool.py setup`, which overwrites a changed copy. Run it from a Herdr terminal other than sv.
set -eu
dir=$(cd "$(dirname "$0")" && pwd)

usage() {
  cat >&2 <<'EOF'
usage: sv.sh start|restart|stop|chat
  start    start sv, sv-watch, and sv-chat (sv must not exist; sv-chat only when it does not exist)
  restart  end sv-watch and sv, then start them again (sv-chat is left as it is)
  stop     end sv-watch, sv, and sv-chat (workers and managers keep running)
  chat     end sv-chat, then start it again
EOF
  exit 2
}

end() {
  for t in "$@"; do
    hc kill "$t" >/dev/null 2>&1 || true
    hc rm "$t" >/dev/null 2>&1 || true
  done
}

# Give the terminal a pane (a tab) in the Herdr window, as `hc run --bg` starts it without one.
# Right after `hc run` the window may not know the terminal yet, so this retries for about 5 seconds.
keep() {
  id=$(hc get "$1" --json | python3 -c 'import json, sys; print(json.load(sys.stdin)["address"])')
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    out=$(herdr-app do terminal.keep --terminal "$id" 2>&1) && return 0
    sleep 0.5
  done
  echo "warning: herdr-app do terminal.keep --terminal $id failed: $out" >&2
  echo "warning: give $1 a pane in Herdr (run the command above by hand)" >&2
}

start_sv() {
  hc run --bg --no-parent --name sv --cwd "$dir" env PATH="$PATH" claude --model opus --permission-mode auto \
    "session-supervisor skill で監督を再開してください"
  hc run --bg --name sv-watch --parent sv --label sv=watch --outlive-parent --cwd "$dir" \
    python3 .supervisor/state_tool.py watch-vault --state state.json --notify sv --interval 10
  keep sv
}

start_chat() {
  hc run --bg --no-parent --name sv-chat --cwd "$dir/chat" env PATH="$PATH" claude --model opus --permission-mode auto
  keep sv-chat
}

[ $# -eq 1 ] || usage
case "$1" in
  start)
    if hc get sv >/dev/null 2>&1; then
      echo "error: sv exists; use restart" >&2
      exit 1
    fi
    end sv-watch
    start_sv
    hc get sv-chat >/dev/null 2>&1 || start_chat
    ;;
  restart)
    end sv-watch sv
    start_sv
    ;;
  stop)
    end sv-watch sv sv-chat
    ;;
  chat)
    end sv-chat
    start_chat
    ;;
  *) usage ;;
esac
hc ls
