#!/bin/sh
# Entry point. The supervisor runs this script whenever it (re)starts the harness.
exec "${SIA_PYTHON:-python3}" -u "$(dirname "$0")/main.py"
