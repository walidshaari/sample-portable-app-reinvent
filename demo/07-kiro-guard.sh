#!/usr/bin/env bash
# Enforcement is a file in the repo, not willpower.
#
#   07-kiro-guard.sh hook       fallback for the live Kiro moment: replay the
#                               exact PreToolUse payload Kiro sends and show exit 2
#   07-kiro-guard.sh commit     a human stages a violation; the git pre-commit
#                               hook (same checker) refuses it; then unstage
#   07-kiro-guard.sh scan       brownfield detection: monolith vs clean
#   07-kiro-guard.sh scaffold   greenfield: generate a service, test it, check it
source "$(dirname "$0")/lib.sh"
AG="${DEMO_ROOT}/kiro/archguard/archguard.py"
cd "${DEMO_ROOT}"

case "${1:-hook}" in
  hook)
    before="$(shasum -a 256 base_python/clean/domain/user.py | awk '{print $1}')"
    payload="$(jq -nc --arg cwd "${DEMO_ROOT}" '{hook_event_name:"PreToolUse",cwd:$cwd,tool_name:"str_replace",
      tool_input:{path:"base_python/clean/domain/user.py",oldStr:"import re",newStr:"import re\nimport boto3",replace_all:false}}')"
    step "echo '<Kiro str_replace payload: add import boto3 to domain/user.py>' | archguard hook --agent kiro"
    set +e; echo "${payload}" | python3 "${AG}" hook --agent kiro; code=$?; set -e
    echo "exit code: ${code}   (2 = blocked)"
    [ "${code}" -eq 2 ] || { echo "expected Kiro hook to block the write" >&2; exit 1; }
    step "compare domain/user.py before and after the blocked write"
    after="$(shasum -a 256 base_python/clean/domain/user.py | awk '{print $1}')"
    [ "${before}" = "${after}" ] || { echo "domain changed during the hook replay" >&2; exit 1; }
    echo "(domain unchanged)"
    ;;
  commit)
    tmp="$(mktemp -d)"; trap 'rm -rf "${tmp}"' EXIT
    git clone -q --no-hardlinks "${DEMO_ROOT}" "${tmp}/repo" 2>/dev/null
    cp "${DEMO_ROOT}/archguard.toml" "${tmp}/repo/"
    mkdir -p "${tmp}/repo/kiro/archguard" && cp "${AG}" "${tmp}/repo/kiro/archguard/"
    mkdir -p "${tmp}/repo/.githooks"
    cp "${DEMO_ROOT}/kiro/hooks/git-pre-commit" "${tmp}/repo/.githooks/pre-commit"
    chmod +x "${tmp}/repo/.githooks/pre-commit"
    cd "${tmp}/repo"
    git config core.hooksPath .githooks
    # Portable prepend: BSD sed (macOS) does not expand \n in a replacement.
    { echo 'import boto3'; cat base_python/clean/domain/order.py; } > order.py.tmp \
      && mv order.py.tmp base_python/clean/domain/order.py
    git add base_python/clean/domain/order.py
    step "git commit -m 'boundary violation'     # .githooks/pre-commit rejects it"
    set +e
    git -c user.name="Demo" -c user.email="demo@example.com" commit -m "boundary violation"
    code=$?
    set -e
    echo "exit code: ${code}   (non-zero = commit refused)"
    [ "${code}" -ne 0 ] || { echo "expected pre-commit hook to reject the change" >&2; exit 1; }
    [ "$(git status --short -- base_python/clean/domain/order.py)" = "M  base_python/clean/domain/order.py" ] \
      || { echo "expected violating change to remain staged" >&2; exit 1; }
    echo "(ran in a throwaway clone at ${tmp}; your working tree is untouched)"
    ;;
  scan)
    step "archguard scan src_python/monolith"
    python3 "${AG}" scan src_python/monolith --format json | jq -c '{classification, coupling_score}'
    step "archguard scan base_python/clean"
    python3 "${AG}" scan base_python/clean --format json | jq -c '{classification, coupling_score}'
    step "full report with the six-move plan: archguard scan src_python/monolith --format md"
    python3 "${AG}" scan src_python/monolith --format md | sed -n '/## Refactor plan/,/^## /p' | head -24
    ;;
  scaffold)
    tmp="$(mktemp -d)"; trap 'rm -rf "${tmp}"' EXIT
    step "new_service.py notes-svc <tmp> --entity Note"
    python3 kiro/skills/clean-service-scaffold/scripts/new_service.py notes-svc "${tmp}" --entity Note >/dev/null
    cd "${tmp}/notes-svc"
    step "pytest (generated tests, sockets blocked)"
    env -i PATH="${PATH}" HOME="${HOME}" "${PY}" -m pytest -p no:cacheprovider -o addopts="" -q 2>&1 | tail -1
    step "archguard check (vendored copy, from the generated hooks)"
    python3 tools/archguard/archguard.py check
    step "the generated service ships its own hook: try to put boto3 in its domain"
    set +e
    jq -nc --arg cwd "$PWD" '{hook_event_name:"PreToolUse",cwd:$cwd,tool_name:"fs_append",
      tool_input:{path:"src/domain/note.py",text:"import boto3\n"}}' \
      | python3 "$(jq -r '.hooks[0].action.command' .kiro/hooks/clean-guard.json | awk '{print $2}')" hook --agent kiro 2>&1 | head -2
    code="${PIPESTATUS[1]}"
    set -e
    echo "exit code: ${code}"
    [ "${code}" -eq 2 ] || { echo "expected scaffold hook to block the write" >&2; exit 1; }
    ;;
  *) echo "usage: $0 hook|commit|scan|scaffold" >&2; exit 1 ;;
esac
