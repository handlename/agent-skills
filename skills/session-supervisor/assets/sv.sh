#!/usr/bin/env bash
# Start, restart, or stop the sessions of this supervisor directory: sv, sv-watch, and sv-chat (each named with the
# name_prefix of ## Locations in CLAUDE.md, and given a pane in its herdr_space when set).
# Placed by `state_tool.py setup`, which overwrites a changed copy. Run it from a Herdr terminal other than sv.
set -eu
dir=$(cd "$(dirname "$0")" && pwd)
# An invalid ## Locations stops here, before anything starts under a wrong name.
p=$(python3 "$dir/.supervisor/state_tool.py" locations --dir "$dir" --key name_prefix)
space=$(python3 "$dir/.supervisor/state_tool.py" locations --dir "$dir" --key herdr_space)

usage() {
  cat >&2 <<'EOF'
usage: sv.sh start|restart|stop|chat
  start    start sv, sv-watch, and sv-chat (sv must not exist; sv-chat only when it does not exist),
           and give the live project managers a pane
  restart  end sv-watch and sv, then start them again (sv-chat is left as it is),
           and give the live project managers a pane
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

# Project managers run under sv, so their panes go with it; give each live one (p-NNNN, or m-p-NNNN from before 0.66.0) a pane again.
keep_managers() {
  for t in $(hc ls --json | python3 -c 'import json, re, sys
for t in json.load(sys.stdin)["terminals"]:
    if re.fullmatch(re.escape(sys.argv[1]) + r"(m-)?p-[0-9]{4}", t.get("name", "")): print(t["name"])' "$p"); do
    keep "$t"
  done
}

# The JSON ids of the Herdr spaces named $space, one per line.
space_ids() {
  herdr-app ls | python3 -c 'import json, sys
for s in json.load(sys.stdin).get("spaces", []):
    if s.get("name") == sys.argv[1]: print(json.dumps(s["id"]))' "$space"
}

# With herdr_space, select that space (creating it when missing) so that keep gives the panes there. A failure only warns.
use_space() {
  [ -n "$space" ] || return 0
  if ! ids=$(space_ids); then
    echo "warning: herdr-app ls failed; the panes go to the current space, not $space" >&2
    return 0
  fi
  created=
  if [ -z "$ids" ]; then
    new=$(python3 -c 'import json, sys; print(json.dumps({"name": sys.argv[1], "focus": False}))' "$space")
    if ! out=$(herdr-app do space.new "$new" 2>&1) || ! ids=$(space_ids) || [ -z "$ids" ]; then
      echo "warning: could not create the Herdr space $space: ${out:-}; the panes go to the current space" >&2
      return 0
    fi
    created=$ids
  fi
  set -- $ids
  if [ $# -gt 1 ]; then
    echo "warning: $# Herdr spaces are named $space; using the first ($1)" >&2
  fi
  if ! out=$(herdr-app do space.select "{\"id\": $1}" 2>&1); then
    echo "warning: herdr-app do space.select failed: $out; the panes go to the current space" >&2
    # Close only a space this run created, never one the user had.
    for id in $created; do
      herdr-app do space.close "{\"id\": $id, \"confirmed\": true}" >/dev/null 2>&1 || true
    done
  fi
}

start_sv() {
  hc run --bg --no-parent --name "${p}sv" --cwd "$dir" env PATH="$PATH" claude --model opus --permission-mode auto \
    "session-supervisor skill で監督を再開してください"
  hc run --bg --name "${p}sv-watch" --parent "${p}sv" --label sv=watch --outlive-parent --cwd "$dir" \
    python3 .supervisor/state_tool.py watch-vault --state state.json --notify "${p}sv" --interval 10
  keep "${p}sv"
}

start_chat() {
  hc run --bg --no-parent --name "${p}sv-chat" --cwd "$dir/chat" env PATH="$PATH" claude --model opus --permission-mode auto
  keep "${p}sv-chat"
}

[ $# -eq 1 ] || usage
case "$1" in
  start)
    if hc get "${p}sv" >/dev/null 2>&1; then
      echo "error: ${p}sv exists; use restart" >&2
      exit 1
    fi
    end "${p}sv-watch"
    use_space
    start_sv
    hc get "${p}sv-chat" >/dev/null 2>&1 || start_chat
    keep_managers
    ;;
  restart)
    end "${p}sv-watch" "${p}sv"
    use_space
    start_sv
    keep_managers
    ;;
  stop)
    end "${p}sv-watch" "${p}sv" "${p}sv-chat"
    ;;
  chat)
    end "${p}sv-chat"
    use_space
    start_chat
    ;;
  *) usage ;;
esac
hc ls
