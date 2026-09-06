#!/bin/zsh
cd -- "${0:A:h}"
exec python3 tools/library_manager.py --root . serve --open
