#!/bin/sh
# WP8 fallback for a locally rate-limited PoC account. Served only from the
# test host on the bench LAN; API key arrives via environment, never argv/logs.
set -eu
base=http://127.0.0.1
user=${EMONOS_APP_TEST_USER:-emonospoc}
key=${EMONOS_APP_TEST_APIKEY:?missing test API key}
printf '%s' "$key" | grep -Eq '^[0-9a-fA-F]{32}$'
echo "app-check: input"
curl -fsS --max-time 15 -G "$base/input/post" \
    --data-urlencode "node=$user" --data-urlencode 'fulljson={"power":100}' \
    --data-urlencode "apikey=$key" | grep -q '"success"'
input=$(curl -fsS --max-time 15 "$base/input/list.json?apikey=$key" | \
    sed -n 's/.*"id":"\{0,1\}\([0-9][0-9]*\)"\{0,1\}.*"nodeid":"'"$user"'".*/\1/p' | head -1)
test -n "$input"
echo "app-check: create feed"
feed=$(curl -fsS --max-time 15 -G "$base/feed/create.json" \
    --data-urlencode 'tag=poc' --data-urlencode "name=$user-$(date +%s)" \
    --data-urlencode 'engine=5' --data-urlencode 'options={"interval":10}' \
    --data-urlencode "apikey=$key")
feed_id=$(printf '%s' "$feed" | sed -n 's/.*"feedid":\([0-9][0-9]*\).*/\1/p')
test -n "$feed_id"
echo "app-check: process and write"
curl -fsS --max-time 15 -X POST \
    --data-urlencode "processlist=[{\"fn\":\"process__log_to_feed\",\"args\":[$feed_id]}]" \
    "$base/input/process/set.json?inputid=$input&apikey=$key" | grep -q '"success":true'
curl -fsS --max-time 15 -G "$base/input/post" \
    --data-urlencode "node=$user" --data-urlencode 'fulljson={"power":250}' \
    --data-urlencode "apikey=$key" | grep -q '"success"'
for _ in 1 2 3 4 5 6; do
    value=$(curl -fsS --max-time 15 "$base/feed/timevalue.json?id=$feed_id&apikey=$key" | \
        sed -n 's/.*"value":\([^,}]*\).*/\1/p')
    test "$value" = 250 && exit 0
    sleep 5
done
exit 1
