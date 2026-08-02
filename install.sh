#!/usr/bin/env bash
# Install mdcat locally. No network access and no package manager required:
# the script copies the two runtime files into a directory on PATH.
#
#   ./install.sh                 install into ~/.local/bin
#   ./install.sh /usr/local/bin  install into a chosen directory
#   ./install.sh --uninstall     remove a previous installation
set -euo pipefail

DEFAULT_PREFIX="$HOME/.local/bin"
SOURCE_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES=(mdcat.py mdcat)

usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

uninstall() {
    local prefix="$1" removed=0
    for file in "${FILES[@]}"; do
        if [ -e "$prefix/$file" ]; then
            rm -f "$prefix/$file"
            echo "removed $prefix/$file"
            removed=1
        fi
    done
    [ "$removed" -eq 1 ] || echo "nothing to remove in $prefix"
}

main() {
    local prefix="$DEFAULT_PREFIX" mode="install"
    for arg in "$@"; do
        case "$arg" in
            -h|--help) usage 0 ;;
            --uninstall) mode="uninstall" ;;
            -*) echo "install.sh: unknown option $arg" >&2; usage 1 ;;
            *) prefix="$arg" ;;
        esac
    done

    if [ "$mode" = "uninstall" ]; then
        uninstall "$prefix"
        return
    fi

    command -v python3 >/dev/null 2>&1 || {
        echo "install.sh: python3 was not found on PATH" >&2
        exit 1
    }

    mkdir -p "$prefix"
    for file in "${FILES[@]}"; do
        install -m 0755 "$SOURCE_DIR/$file" "$prefix/$file"
        echo "installed $prefix/$file"
    done

    # A plain string match on PATH is enough here and avoids depending on the
    # directory already existing when the check runs.
    case ":$PATH:" in
        *":$prefix:"*) ;;
        *) echo
           echo "note: $prefix is not on PATH. Add this to the shell profile:"
           echo "      export PATH=\"$prefix:\$PATH\"" ;;
    esac

    echo
    echo "Run 'mdcat --help' to get started, or 'mdcat sample.md' for a demo."
}

main "$@"
