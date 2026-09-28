#!/bin/sh
# The paper reader on its own. A PDF path may be passed in.
cd "$(dirname "$0")" || exit 1
exec python3 -m livetranslator.ui.reader_window "$@"
