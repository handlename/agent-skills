#!/usr/bin/env bash
#
# setup-worktree.sh
#
# Copies untracked files declared in the main worktree's `.worktreeinclude`
# into the current linked git worktree. Intended to run as a Claude Code
# SessionStart hook, but safe to run manually from any directory.
#
# Declarations are read from the main worktree root:
#   - `.worktreeinclude`       — team-shared list (usually tracked)
#   - `.worktreeinclude.local` — personal additions (untracked), for files
#     like `.mise.local.toml` that only exist in your own checkout
#
# Behavior:
#   - Does nothing (exit 0) when: not in a git repo, in the main worktree,
#     or the main worktree has neither declaration file.
#   - Never overwrites existing files in the worktree.
#   - Idempotent: a second run is a no-op.
#   - Always exits 0 so a Claude Code session start is never blocked.

set -u

main() {
    local dir="${CLAUDE_PROJECT_DIR:-$PWD}"
    cd "$dir" 2>/dev/null || return 0

    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0

    # In a linked worktree --git-dir points into <common>/worktrees/<name>,
    # while in the main worktree both paths are identical.
    local git_dir common_dir
    git_dir=$(git rev-parse --path-format=absolute --git-dir 2>/dev/null) || return 0
    common_dir=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 0
    [[ "$git_dir" == "$common_dir" ]] && return 0

    # The main worktree is documented to be listed first.
    local main_wt
    main_wt=$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')
    [[ -n "$main_wt" ]] || return 0

    local include includes=()
    for include in "$main_wt/.worktreeinclude" "$main_wt/.worktreeinclude.local"; do
        [[ -f "$include" ]] && includes+=("$include")
    done
    [[ ${#includes[@]} -eq 0 ]] && return 0

    local wt_root
    wt_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 0

    # List untracked files in the main worktree matching the declared
    # patterns. Tracked files are excluded by --others. Files matching both
    # declarations are copied once: the second pass skips existing targets.
    local copied=0 f src dest
    for include in "${includes[@]}"; do
        while IFS= read -r -d '' f; do
            src="$main_wt/$f"
            dest="$wt_root/$f"
            [[ -e "$dest" ]] && continue
            # Reject symlinks: following one could copy files from outside the
            # repository (e.g. a malicious repo linking to ~/.ssh).
            [[ -L "$src" ]] && continue
            [[ -f "$src" ]] || continue
            mkdir -p "$(dirname "$dest")" || continue
            if cp -p "$src" "$dest"; then
                [[ "$copied" -eq 0 ]] && echo "setup-worktree: copying declared files from $main_wt"
                echo "  + $f"
                copied=$((copied + 1))
            fi
        done < <(git -C "$main_wt" ls-files -z --others --ignored --exclude-from="$include" 2>/dev/null)
    done

    return 0
}

main "$@" || echo "setup-worktree: skipped due to an unexpected error" >&2
exit 0
