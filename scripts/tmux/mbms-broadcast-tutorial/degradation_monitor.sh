#!/bin/bash
# Long-duration monitor for the eNB/modem "degrades after hours" bug (see
# project-modem-long-uptime-degradation memory / SIB13_MBSFN_TEST_RESULTS.md).
# Samples RSS, thread count, and per-thread CPU time for both processes every
# 2 minutes so that if/when the degradation recurs, there's actual data to
# diagnose against instead of needing to catch it live. Does not touch the
# processes themselves (read-only /proc sampling).
#
# Usage: nohup ./degradation_monitor.sh <enb_pid> <modem_pid> >> degradation_monitor.log 2>&1 &

ENB_PID=$1
MODEM_PID=$2
OUT=/home/jordijoan/.local/state/mbms-broadcast-tutorial/degradation_monitor.csv

if [ -z "$ENB_PID" ] || [ -z "$MODEM_PID" ]; then
  echo "usage: $0 <enb_pid> <modem_pid>"
  exit 1
fi

if [ ! -f "$OUT" ]; then
  echo "timestamp,enb_pid,enb_rss_kb,enb_threads,enb_cpu_pct,modem_pid,modem_rss_kb,modem_threads,modem_cpu_pct" > "$OUT"
fi

while true; do
  ts=$(date -Is)
  # Use /proc/$PID existence, not `kill -0`: the modem runs as root (netns
  # setup) so we lack signal permission on it even though it's alive and
  # /proc/$PID/status is still readable.
  if [ -d "/proc/$ENB_PID" ]; then
    enb_rss=$(awk '/VmRSS/{print $2}' /proc/$ENB_PID/status 2>/dev/null)
    enb_threads=$(awk '/Threads/{print $2}' /proc/$ENB_PID/status 2>/dev/null)
    enb_cpu=$(ps -o %cpu= -p "$ENB_PID" 2>/dev/null | tr -d ' ')
  else
    enb_rss=DEAD; enb_threads=DEAD; enb_cpu=DEAD
  fi
  if [ -d "/proc/$MODEM_PID" ]; then
    modem_rss=$(awk '/VmRSS/{print $2}' /proc/$MODEM_PID/status 2>/dev/null)
    modem_threads=$(awk '/Threads/{print $2}' /proc/$MODEM_PID/status 2>/dev/null)
    modem_cpu=$(ps -o %cpu= -p "$MODEM_PID" 2>/dev/null | tr -d ' ')
  else
    modem_rss=DEAD; modem_threads=DEAD; modem_cpu=DEAD
  fi
  echo "$ts,$ENB_PID,$enb_rss,$enb_threads,$enb_cpu,$MODEM_PID,$modem_rss,$modem_threads,$modem_cpu" >> "$OUT"
  sleep 120
done
