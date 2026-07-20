#!/usr/bin/env bash
#
# setup-worktree.sh
#
# Sets up the current linked git worktree at Claude Code session start. Safe
# to run manually from any directory. Two phases run in order:
#
#   1. Copy — copies untracked files declared in the main worktree's
#      `.worktreeinclude` into the current linked worktree.
#   2. Run  — executes setup scripts declared in the main worktree
#      (`.worktreesetup`), but only when the repository is trusted.
#
# Declarations are read from the main worktree root:
#   - `.worktreeinclude`       — team-shared copy list (usually tracked)
#   - `.worktreeinclude.local` — personal copy additions (untracked)
#   - `.worktreesetup`         — team-shared setup script (usually tracked)
#   - `.worktreesetup.local`   — personal setup script (untracked)
#
# Setup scripts run every session (there is no first-run marker), so they
# must be written to be idempotent — e.g. `mise trust`, `aqua policy allow`.
# They run with the worktree root as the working directory, after the copy
# phase, and only when the repository's identity (derived from its `origin`
# remote) matches an entry in the trust allowlist:
#   ${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup/trusted
# Entries are `host/org` (org-wide) or `host/org/repo` (single repo), one per
# line; `#` comments and blank lines are ignored. The allowlist lives outside
# any repository, so a cloned repository cannot authorize itself to run code.
#
# Behavior:
#   - Does nothing (exit 0) when: not in a git repo, in the main worktree,
#     or the main worktree has no declaration files.
#   - Never overwrites existing files in the worktree.
#   - Copy phase is idempotent: a second run copies nothing new.
#   - Setup scripts run only for allowlisted repositories; when they exist
#     but the repository is untrusted, a notice explains what was skipped and
#     how to opt in. A failing script is reported but does not abort the
#     session.
#   - Always exits 0 so a Claude Code session start is never blocked.

set -u

# Derive a repository identity of the form host/org/repo from the main
# worktree's `origin` remote, normalizing the common URL shapes:
#   git@host:org/repo(.git)          -> host/org/repo
#   ssh|https://[user@]host/org/repo -> host/org/repo
# Prints the identity on success; returns non-zero when there is no usable
# remote (in which case the repository is treated as untrusted).
repo_identity() {
    local url
    url=$(git -C "$1" remote get-url origin 2>/dev/null) || return 1
    [[ -n "$url" ]] || return 1

    url=${url%.git}
    if [[ "$url" == *://* ]]; then
        # scheme://[user@]host[:port]/org/repo
        local rest=${url#*://}
        rest=${rest#*@}
        local host=${rest%%/*}
        local path=${rest#*/}
        printf '%s/%s\n' "${host%%:*}" "$path"
    elif [[ "$url" == *@*:* ]]; then
        # git@host:org/repo
        local host_part=${url#*@}
        printf '%s/%s\n' "${host_part%%:*}" "${host_part#*:}"
    else
        return 1
    fi
}

# Return 0 when the given host/org/repo identity is listed in the trust
# allowlist. An entry may name a whole org (host/org) or a single repo
# (host/org/repo); `#` comments and blank lines are ignored.
is_trusted() {
    local id="$1"
    local org_id="${id%/*}"
    local cfg="${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup/trusted"
    [[ -f "$cfg" ]] || return 1

    local line
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        # Trim surrounding whitespace and a trailing slash.
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        line="${line%/}"
        [[ -z "$line" ]] && continue
        [[ "$line" == "$id" || "$line" == "$org_id" ]] && return 0
    done < "$cfg"
    return 1
}

# Execute the main worktree's setup scripts in the worktree root, but only
# for trusted repositories. Scripts run every session and so must be
# idempotent. A non-zero exit is reported without aborting the session. When
# setup scripts exist but the repository is not trusted, report what was
# skipped and how to opt in instead of running silently.
run_setup_scripts() {
    local main_wt="$1" wt_root="$2"

    # Collect declared setup scripts. Symlinks are rejected for the same
    # reason the copy phase does: one could point at a script outside the
    # repository.
    local script scripts=()
    for script in "$main_wt/.worktreesetup" "$main_wt/.worktreesetup.local"; do
        [[ -f "$script" && ! -L "$script" ]] && scripts+=("$script")
    done
    [[ ${#scripts[@]} -eq 0 ]] && return 0

    local id
    id=$(repo_identity "$main_wt") || id=""

    if [[ -z "$id" ]] || ! is_trusted "$id"; then
        local cfg="${XDG_CONFIG_HOME:-$HOME/.config}/worktree-setup/trusted"
        echo "setup-worktree: setup scripts found but NOT run — this repository is not trusted:"
        for script in "${scripts[@]}"; do
            echo "  - $script"
        done
        if [[ -n "$id" ]]; then
            echo "  To run them, add this repository (or its org) to the trust allowlist:"
            echo "    echo '$id' >> \"$cfg\""
        else
            echo "  This repository has no 'origin' remote, so it cannot be identified or trusted."
        fi
        return 0
    fi

    for script in "${scripts[@]}"; do
        echo "setup-worktree: running $script (repo trusted: $id)"
        if ! ( cd "$wt_root" && bash "$script" ); then
            echo "setup-worktree: $script exited non-zero; continuing" >&2
        fi
    done
}

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

    local wt_root
    wt_root=$(git rev-parse --show-toplevel 2>/dev/null) || return 0

    copy_declared_files "$main_wt" "$wt_root"
    run_setup_scripts "$main_wt" "$wt_root"

    return 0
}

# Copy untracked files declared in the main worktree's `.worktreeinclude`
# into the worktree root. No-op when neither declaration file exists.
copy_declared_files() {
    local main_wt="$1" wt_root="$2"

    local include includes=()
    for include in "$main_wt/.worktreeinclude" "$main_wt/.worktreeinclude.local"; do
        [[ -f "$include" ]] && includes+=("$include")
    done
    [[ ${#includes[@]} -eq 0 ]] && return 0

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
}

main "$@" || echo "setup-worktree: skipped due to an unexpected error" >&2
exit 0
