#!/usr/bin/env bash
# Technology axis: key-value DynamoDB to relational PostgreSQL. One new
# adapter file plus one registry line in the composition root. The domain,
# the use cases and the ports stay byte-identical.
#
#   04-tech-axis.sh before   remove the PostgreSQL adapter (state before the change)
#   04-tech-axis.sh after    apply it and prove the blast radius
source "$(dirname "$0")/lib.sh"
PATCH="${DEMO_ROOT}/demo/patches/tech-axis-postgres.patch"
cd "${DEMO_ROOT}"

case "${1:-after}" in
  before)
    # Idempotent: preflight leaves the patch applied, so check the state first.
    if git apply -R --check "${PATCH}" 2>/dev/null; then
      git apply -R "${PATCH}"
    elif git apply --check "${PATCH}" 2>/dev/null; then
      echo "(already in the 'before' state: patch not applied)"
    else
      echo "tech-axis patch neither applies nor reverts cleanly; inspect git status" >&2; exit 1
    fi
    step "grep -c postgres base_python/clean/infrastructure/composition.py"
    grep -c postgres base_python/clean/infrastructure/composition.py || true
    ;;
  after)
    step "git apply --stat demo/patches/tech-axis-postgres.patch"
    git apply --stat "${PATCH}"
    if git apply --check "${PATCH}" 2>/dev/null; then
      git apply "${PATCH}"
    elif git apply -R --check "${PATCH}" 2>/dev/null; then
      echo "(already applied: the PostgreSQL adapter is in the tree)"
    else
      echo "tech-axis patch neither applies nor reverts cleanly; inspect git status" >&2; exit 1
    fi
    step "git diff --stat HEAD -- base_python/clean/domain base_python/clean/application"
    out="$(git -P diff --stat HEAD -- base_python/clean/domain base_python/clean/application)"
    echo "${out:-(empty: domain, use cases and ports are byte-identical to HEAD)}"
    step "python3 kiro/archguard/archguard.py check"
    python3 kiro/archguard/archguard.py check
    step "pytest port contract: the same assertions against memory, DynamoDB and PostgreSQL"
    (cd "${APP_DIR}" && env -i PATH="${PATH}" HOME="${HOME}" REQUIRE_POSTGRES=1 \
      TEST_POSTGRES_DSN="${TEST_POSTGRES_DSN:-postgresql://app:test-only@127.0.0.1:55432/app}" \
      "${PY}" -m pytest tests/integration/test_repository_contract.py -q -p no:cacheprovider -p no:warnings)
    ;;
  *) echo "usage: $0 before|after" >&2; exit 1 ;;
esac
