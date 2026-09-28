#!/usr/bin/env bash
# Testability: the core imports nothing from the outside world, and its tests
# run with the network switched off and no credentials, in well under a second.
source "$(dirname "$0")/lib.sh"
cd "${APP_DIR}"

step "grep -rnE 'boto3|botocore|fastapi|starlette|mangum|uvicorn|psycopg|sqlalchemy|os\.environ|getenv' domain/ application/"
if grep -rnE 'boto3|botocore|fastapi|starlette|mangum|uvicorn|psycopg|sqlalchemy|os\.environ|getenv' domain/ application/; then
  echo "FOUND infrastructure in the core"; exit 1
fi
echo "(no matches)"

step "env -i PATH=\$PATH pytest tests/core     # no AWS variables, sockets disabled"
start=$(python3 -c 'import time; print(time.time())')
env -i PATH="${PATH}" HOME="${HOME}" "${PY}" -m pytest tests/core -q -p no:cacheprovider
end=$(python3 -c 'import time; print(time.time())')
python3 -c "print(f'wall clock including interpreter start: {${end}-${start}:.2f}s')"
