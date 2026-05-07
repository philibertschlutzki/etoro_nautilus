#!/bin/bash

# Trage hier deine NEUEN Keys ein
API_KEY="sdgdskldFPLGfjHn1421dgnlxdGTbngdflg6290bRjslfihsjhSDsdgGHH25hjf"
USER_KEY="eyJjaSI6IjYwY2FiYjBiLTU1OTctNDQ4NS04ZjYzLTdlOWUwNTZlMGJiOCIsImVhbiI6IlVucmVnaXN0ZXJlZEFwcGxpY2F0aW9uIiwiZWsiOiJocXZSSG91SS1EdjBLMldWbTBwd2pvejNUcE03Q0xRRERqYnpGYUpSZjU5cTRvelJGc1Ewbk9QVmNuSWFqU2M1RXZMaUZUUlM4NFBSSlNhREJaUUxsZG93STVObFE3dU00SUFmdFdZUW5PMF8ifQ__"

# Request-ID und Parameter
REQUEST_ID=$(uuidgen)
SYMBOL="TSLA"  
BASE_URL="https://public-api.etoro.com/api/v1/market-data/search"

echo "Frage gefilterte Marktdaten für ${SYMBOL} ab..."

# cURL Anfrage mit jq-Filter am Ende
curl -s -X GET "${BASE_URL}?internalSymbolFull=${SYMBOL}" \
  -H "x-api-key: ${API_KEY}" \
  -H "x-user-key: ${USER_KEY}" \
  -H "x-request-id: ${REQUEST_ID}" \
  -H "Accept-Language: en-US,en;q=0.9" \
  -H "Accept: application/json" | jq '.items[] | {Symbol: .internalSymbolFull, Name: .internalInstrumentDisplayName, Preis: .currentRate, Typ: .internalAssetClassName}'
