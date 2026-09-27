#!/bin/sh
cd /mnt/d/Code/mycroft/verification-layer
python3 -m uvicorn web.server:app --reload --port 8000 