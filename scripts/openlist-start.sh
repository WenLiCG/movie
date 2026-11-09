#!/bin/sh
set -eu
umask 077
cat /proc/sys/kernel/random/uuid > /run/cinema/instance-id.new
mv /run/cinema/instance-id.new /run/cinema/instance-id
exec "$@"
