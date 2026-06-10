#!/usr/bin/env bash
# One-shot dev-environment setup (Linux / macOS).
#
# Installs everything needed to build the ROM and run the script editor:
#   1. Installs `uv` (Astral's Python toolchain manager) if it is missing.
#   2. Creates the project virtual environment (.venv) with a matching
#      CPython (uv downloads it if needed).
#   3. Installs retrotool FROM PyPI:
#        retrotool[all]     -> build engine + bundled libsfx/asar/bass/xdelta
#        retrotool[editor]  -> `retrotool edit` GUI (pywebview + Pillow;
#                              Linux adds pywebview's [qt] backend)
#
# Run this ONCE per machine (or after dependencies change), then follow the
# checklist it prints. Adapted from the Rushing Beat Shura translation
# project's contributor scripts — generic: it reads the ROM path from
# project.toml, so it works unchanged in any retrotool project.
set -euo pipefail

RETROTOOL_SPEC='retrotool[all,editor]>=0.9.3'
PY_VERSION='3.13'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
echo "==> Project: $REPO_ROOT"

if [[ ! -f project.toml ]]; then
    echo "error: no project.toml in $REPO_ROOT — run this from the project checkout." >&2
    exit 1
fi

# --- 1. Ensure uv is installed ---------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv (Python toolchain manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
        echo "error: uv installed but not on PATH in this session." >&2
        echo "       Open a new terminal and re-run scripts/setup.sh." >&2
        exit 1
    fi
fi
echo "==> uv: $(uv --version)"

# --- 2. Create the virtual environment (.venv) -----------------------------
echo "==> Creating .venv (CPython $PY_VERSION)..."
uv venv --python "$PY_VERSION"

# --- 3. Install dependencies FROM PyPI -------------------------------------
case "$(uname -s)" in
    Darwin) EXTRA_SPECS=() ;;                  # native Cocoa webview backend
    *)      EXTRA_SPECS=('pywebview[qt]') ;;   # Linux: Qt (PyQt6 + WebEngine)
esac
echo "==> Installing $RETROTOOL_SPEC ${EXTRA_SPECS[*]:-} from PyPI..."
uv pip install "$RETROTOOL_SPEC" "${EXTRA_SPECS[@]}"

# --- 4. Verify the environment ---------------------------------------------
echo
echo "==> Verifying the environment:"
fail=0
check() {  # check <label> <command...>
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  ✓ $label"
    else
        echo "  ✗ $label"
        fail=1
    fi
}
check ".venv python"         test -x .venv/bin/python
check "retrotool CLI"        test -x .venv/bin/retrotool
check "retrotool importable" .venv/bin/python -c 'import retrotool'
check "Pillow (PIL)"         .venv/bin/python -c 'import PIL'
check "pywebview (editor)"   .venv/bin/python -c 'import webview'

# The source ROM path comes from project.toml so this stays generic. In a
# real project this is a manual step (copyright — never distributed); in
# THIS example the "game" is synthesized by tools/make_demo_rom.py.
ROM_PATH="$(.venv/bin/python - <<'EOF' 2>/dev/null || true
import tomllib
print(tomllib.load(open("project.toml","rb"))["rom"]["file"])
EOF
)"
if [[ -n "$ROM_PATH" ]]; then
    if [[ -f "$ROM_PATH" ]]; then
        echo "  ✓ source ROM ($ROM_PATH)"
    else
        echo "  ! source ROM MISSING — generate the demo ROM with:"
        echo "      .venv/bin/python tools/make_demo_rom.py"
        echo "    (a real project would say: place your legally-obtained copy at $ROM_PATH)"
    fi
fi

if [[ $fail -ne 0 ]]; then
    echo
    echo "Setup FAILED one or more checks — review the output above." >&2
    exit 1
fi
echo
echo "==> OK. Environment ready. Next steps (see README.md):"
echo "    .venv/bin/python tools/make_demo_rom.py   (demo source ROM)"
echo "    .venv/bin/retrotool build .               (build the translated ROM)"
echo "    .venv/bin/retrotool edit .                (GUI script editor)"
