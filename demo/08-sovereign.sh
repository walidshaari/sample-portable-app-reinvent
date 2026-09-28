#!/usr/bin/env bash
# The on-premises stand-in needs no Region: no cloud credentials in the pod,
# egress to cloud endpoints dropped by policy, every image served from inside
# the cluster. Run it with Wi-Fi off for the airplane-mode version.
source "$(dirname "$0")/lib.sh"
K=(kubectl --context "${KIND_CONTEXT}" -n portable-app)
pod="$("${K[@]}" get pods -l app=portable-app -o jsonpath='{.items[0].metadata.name}')"

step "AWS variables inside ${pod}"
"${K[@]}" exec "${pod}" -- python3.11 -c \
  'import os; v=[k for k in os.environ if k.startswith("AWS_")]; print(v or "none"); raise SystemExit(bool(v))'

step "try to reach a cloud endpoint from the pod (NetworkPolicy: in-cluster egress only)"
"${K[@]}" exec "${pod}" -- python3.11 -c '
import socket
try:
    socket.create_connection(("52.94.0.1", 443), timeout=3)
    print("CONNECTED (egress is open)")
    raise SystemExit(1)
except OSError as e:
    print("blocked:", type(e).__name__)'

step "where every running image comes from"
kubectl --context "${KIND_CONTEXT}" get pods -A -o jsonpath='{range .items[*]}{range .status.containerStatuses[*]}{.imageID}{"\n"}{end}{end}' \
  | sed -E 's#@sha256:.*##; s#^docker.io/##' | sort | uniq -c
echo "(all cached on the node; the app image comes from the in-cluster registry localhost:5001)"

step "create then read on the laptop"
id="$(curl -fsS -X POST "${LOCAL_URL}/api/users" -H 'content-type: application/json' \
  -d '{"name":"Offline Attendee","email":"offline@example.com"}' | jq -er .id)"
curl -fsS "${LOCAL_URL}/api/users/${id}" | jq -ec --arg id "${id}" 'select(.id == $id)'
health "${LOCAL_URL}"
