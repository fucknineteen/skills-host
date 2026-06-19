#!/bin/bash
# Wrapper for process_reviews.py — no_agent cron job
# The script produces output only when there are reviews due;
# empty stdout = SILENT (no delivery to user)

cd /root/.hermes/trade_review && python3 process_reviews.py
