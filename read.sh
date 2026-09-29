#!/bin/sh
# The paper reader on its own. A PDF path may be passed in.
#
# First time:
#     brew install portaudio             # macOS, for PyAudio
#     pip3 install -r requirements.txt
#     python3 -m livetranslator.models   # the voice and language packs
#     chmod +x run.sh read.sh
#
# macOS ships no python3 of its own, so "python3: command not found" is the
# first thing a Mac is likely to say here. Rather than leave that bare, this
# looks for any Python new enough and explains itself when there is none.

cd "$(dirname "$0")" || exit 1

find_python() {
    for name in python3 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$name" >/dev/null 2>&1; then
            if "$name" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
                echo "$name"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python) || {
    echo "Live Translator needs Python 3.9 or newer, and none was found." >&2
    echo >&2
    case "$(uname -s)" in
        Darwin)
            echo "macOS does not come with one. Either:" >&2
            echo "    xcode-select --install        # the quickest" >&2
            echo "    brew install python@3.12" >&2
            ;;
        *)
            echo "Install it with your package manager, for example:" >&2
            echo "    sudo apt install python3 python3-pip" >&2
            ;;
    esac
    exit 1
}

if ! "$PYTHON" -c "import PySide6" >/dev/null 2>&1; then
    echo "The libraries are not installed yet. Run:" >&2
    echo "    $PYTHON -m pip install -r requirements.txt" >&2
    echo >&2
    echo "On a Mac, PyAudio needs PortAudio first:  brew install portaudio" >&2
    exit 1
fi

exec "$PYTHON" -m livetranslator.ui.reader_window "$@"
