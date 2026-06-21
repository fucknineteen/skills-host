#!/bin/bash
# Jin10 calendar cache refresh — run every 6h
cd /root/.hermes/trade_review && python3 jin10_fallback.py --sync
