# -*- coding: utf-8 -*-
"""Build payload.zip: the whole KargilProperty folder minus transient dirs."""
import os
import sys
import zipfile

SRC = r"D:\Property Dealing\KargilProperty"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "payload.zip")
EXCLUDE_TOP = {"logs", "data_tmp"}          # regenerated at runtime
EXCLUDE_FILES = {"Uninstall.bat"}           # created by the installer, not shipped

count = 0
if os.path.exists(OUT):
    os.remove(OUT)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, dirs, files in os.walk(SRC):
        if os.path.abspath(root) == os.path.abspath(SRC):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_TOP]
        for f in files:
            if f in EXCLUDE_FILES:
                continue
            full = os.path.join(root, f)
            arc = os.path.relpath(full, SRC)   # relative -> extracts directly into dest
            z.write(full, arc)
            count += 1

print("payload:", OUT)
print("files  :", count)
print("size   : %.1f MB" % (os.path.getsize(OUT) / 1024 / 1024))
