#!/usr/bin/env python3
"""Run SDCC's linker; tolerate only its prebuilt-library ABI-tag notice.

The pinned devkitSMS SMSlib archive is assembled without an sdcccall tag. SDCC
4.5 exits nonzero after writing a valid IHX and reports a metadata-only
"Conflicting sdcc options" warning when it meets normal call1 C objects. Do not
hide real errors: accept that one warning class only, require a fresh nonempty
IHX, and let ihx2sms + smstest validate the ROM afterwards.
"""
from __future__ import annotations
import subprocess, sys
from pathlib import Path

if len(sys.argv) < 3:
    raise SystemExit("usage: checked_link.py output.ihx <sdcc command and args...>")
out_path = Path(sys.argv[1])
cmd = sys.argv[2:]
out_path.unlink(missing_ok=True)
run = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
log = run.stdout + run.stderr
if run.returncode == 0:
    sys.stdout.write(run.stdout)
    sys.stderr.write(run.stderr)
    raise SystemExit(0)
lines = [line for line in log.splitlines() if line.strip()]
known = "?ASlink-Warning-Conflicting sdcc options:" in log
bad = any(token in log for token in (
    "?ASlink-Error", "Undefined Global", "Undefined symbol", "error ",
    "fatal error", "cannot open", "No room"))
only_known = known and not bad and all(
    line.startswith("?ASlink-Warning-Conflicting sdcc options:") or line.startswith(" ")
    for line in lines
)
if run.returncode != 0 and only_known and out_path.is_file() and out_path.stat().st_size:
    print("link note: SDCC emitted only bundled-SMSlib call-convention metadata warnings; fresh IHX created")
    print("          final ROM header and gameplay will be checked by ihx2sms and smstest")
    raise SystemExit(0)
sys.stdout.write(run.stdout)
sys.stderr.write(run.stderr)
raise SystemExit(run.returncode)
