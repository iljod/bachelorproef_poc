#!/bin/bash
set -e

(
  echo "[json-auth] Waiting for guacamole.properties..."
  while [ ! -s "${GUACAMOLE_HOME}/guacamole.properties" ]; do
    sleep 0.3
  done
  sleep 2

  cp /opt/guacamole-json/guacamole-auth-json-${GUACAMOLE_VERSION}.jar \
     "${GUACAMOLE_HOME}/extensions/guacamole-auth-json-${GUACAMOLE_VERSION}.jar"

  grep -q "json-secret-key" "${GUACAMOLE_HOME}/guacamole.properties" || \
    printf "\njson-secret-key: %s\n" "${JSON_SECRET_KEY}" \
    >> "${GUACAMOLE_HOME}/guacamole.properties"

  echo "[json-auth] Extension registered."
) &

exec /opt/guacamole/bin/start.sh
