#!/bin/sh
# macOS: double-click this file to start Blindspot. It runs start.sh in this folder.
cd "$(dirname "$0")" || exit 1
exec sh ./start.sh
