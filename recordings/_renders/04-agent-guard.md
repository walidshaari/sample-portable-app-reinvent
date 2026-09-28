# When the agent crosses the line

Automated, captioned evidence replay. No voiceover.

## 00s · What if an agent takes a shortcut?

Status: CODE TOUR. Source: `.kiro/steering/domain-purity.md`.

Proposed edit: import boto3 in domain/user.py

Steering describes the rule. The hook checks the edit.

## 04s · The hook blocks the proposed write

Status: FRESH LOCAL PROOF. Source: `demo/07-kiro-guard.sh + .kiro/hooks/clean-guard-write.json`.

$ bash demo/07-kiro-guard.sh hook
AG001  domain imports 'boto3'
exit code: 2  ·  domain unchanged

Fresh local replay of a Kiro PreToolUse payload.

## 10s · Give the agent a repair path

Status: CURRENT RULE. Source: `kiro/archguard/archguard.py`.

Name a port. Build an adapter.
Inject at composition. Test the contract.

The checker explains this path in its rejection message.

## 15s · Guardrails need activation

Status: CLAIM LIMIT. Source: `kiro/README.md`.

Install the git hook and CI check in each target repo.

The agent hook checks supported tool writes; syntax/tests need a separate gate.
