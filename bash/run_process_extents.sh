#!/bin/bash

# NOTE: This script should either be called with environment wrapper script ${CWMSGRID_HOME}/dep/bin/cgenv
#       or by explicitly doing a . .bash_profile from inside this script (similar to commented lines below)
#       This will ensure proper environment variables are available when the script is called via cron
#
# . .bash_profile

SCRIPT_NAME=`basename ${0}`

LOGDIR=${CWMSGRID_DATA_HOME}/NOHRSC-snodas/logs

# Set Variable LOGDATE to YYYY.MM.DD to facilitate daily logging
LOGDATE=$(date "+%Y.%m.%d")

# Set Variable for Logfile
LOG="${LOGDIR}/${SCRIPT_NAME}.${LOGDATE}.log"

# Get Everything Into The Log
exec >> ${LOG} 2>&1

python ${CWMSGRID_HOME}/software/snowmelt_app/snowmelt/scripts/process_extents.py --all

