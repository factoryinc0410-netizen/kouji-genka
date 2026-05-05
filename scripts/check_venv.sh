#!/usr/bin/env bash
# scripts/check_venv.sh — venv consistency guard (Phase G-1, prevents B-1 recurrence).
#
# Verifies that $PROJECT_ROOT/.venv is internally consistent:
#   1. pyvenv.cfg `command = ... <path>` resolves to this very .venv
#   2. No shebang in .venv/bin/ references a different project's /.venv/
#
# B-1 root cause: `cp -r prod-app/.venv dev-app/.venv` left dev-app's venv
# pointing back to /home/ubuntu/prod-app/.venv. Pip-installs in dev-app then
# silently mutated prod-app's site-packages.
#
# Exit 0 = clean. Exit 1 = contamination detected.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
VENV="$PROJECT_ROOT/.venv"

if [ ! -d "$VENV" ]; then
    echo "[check_venv] FAIL: .venv not found at $VENV" >&2
    exit 1
fi

CFG="$VENV/pyvenv.cfg"
if [ ! -f "$CFG" ]; then
    echo "[check_venv] FAIL: $CFG missing" >&2
    exit 1
fi

EXPECTED="$(realpath "$VENV")"

# 1. pyvenv.cfg `command = ... <venv-path>`
cmd_line="$(grep -E '^command[[:space:]]*=' "$CFG" || true)"
if [ -n "$cmd_line" ]; then
    declared="$(echo "$cmd_line" | awk '{print $NF}')"
    declared_real="$(realpath "$declared" 2>/dev/null || echo "$declared")"
    if [ "$declared_real" != "$EXPECTED" ]; then
        cat >&2 <<EOF
[check_venv] FAIL: pyvenv.cfg `command` points to $declared
                  expected: $EXPECTED
                  cause:    likely cp -r from another project (B-1 regression)
                  fix:      rm -rf $VENV && python3 -m venv $VENV \\
                            && $VENV/bin/pip install -r $PROJECT_ROOT/requirements.txt \\
                                                     -r $PROJECT_ROOT/requirements-dev.txt
EOF
        exit 1
    fi
fi

# 2. shebang check across .venv/bin/*
bad=0
shopt -s nullglob
for f in "$VENV"/bin/*; do
    [ -f "$f" ] || continue
    if ! head -c 2 "$f" 2>/dev/null | grep -q '^#!'; then
        continue
    fi
    shebang="$(head -n 1 "$f")"
    case "$shebang" in
        "#!$VENV/bin/python"*) ;;
        "#!/usr/bin/python"*) ;;
        "#!/usr/bin/env "*) ;;
        *)
            if echo "$shebang" | grep -q '/\.venv/'; then
                echo "[check_venv] FAIL: $(basename "$f") shebang -> $shebang" >&2
                bad=$((bad + 1))
            fi
            ;;
    esac
done

if [ "$bad" -gt 0 ]; then
    echo "[check_venv] FAIL: $bad cross-project shebang(s) in $VENV/bin/" >&2
    exit 1
fi

echo "[check_venv] OK: $VENV is internally consistent"
