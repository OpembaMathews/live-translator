#!/bin/sh
# Live Translator on macOS or Linux. Run it from this folder.
#
# First time:
#     brew install portaudio          # macOS, for PyAudio
#     pip install -r requirements.txt
#     python3 -m livetranslator.models   # the voice and language packs
cd "$(dirname "$0")" || exit 1
exec python3 -m livetranslator "$@"
