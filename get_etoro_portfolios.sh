#!/bin/bash

# Trage hier deine NEUEN Keys ein
API_KEY="sdgdskldFPLGfjHn1421dgnlxdGTbngdflg6290bRjslfihsjhSDsdgGHH25hjf"
USER_KEY="eyJjaSI6IjYwY2FiYjBiLTU1OTctNDQ4NS04ZjYzLTdlOWUwNTZlMGJiOCIsImVhbiI6IlVucmVnaXN0ZXJlZEFwcGxpY2F0aW9uIiwiZWsiOiJocXZSSG91SS1EdjBLMldWbTBwd2pvejNUcE03Q0xRRERqYnpGYUpSZjU5cTRvelJGc1Ewbk9QVmNuSWFqU2M1RXZMaUZUUlM4NFBSSlNhREJaUUxsZG93STVObFE3dU00SUFmdFdZUW5PMF8ifQ__"

# Request-ID und URLs
REQUEST_ID=$(uuidgen)
BASE_URL="https://public-api.etoro.com/api/v1"
ENDPOINT="/agent-portfolios"

echo "Sende Anfrage an eToro..."

# cURL Befehl
curl -s -X GET "${BASE_URL}${ENDPOINT}" \
  -H "x-api-key: ${API_KEY}" \
  -H "x-user-key: ${USER_KEY}" \
  -H "x-request-id: ${REQUEST_ID}" \
  -H "Accept: application/json" | jq .
