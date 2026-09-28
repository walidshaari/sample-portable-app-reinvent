# Keep the core. Choose the placement.

*ARC401 field guide · Clean architecture for an age of coding assistants*

One application runs on a laptop Kubernetes cluster and on EKS in an AWS Region. The lesson is where the code can change. Matching application bytes show that the app can run in both places; they do not, by themselves, deliver recovery or data residency.

[Explore the public code](https://github.com/walidshaari/sample-portable-app-reinvent/tree/b9ef30a2e7d73311f12a6e3e2360c1a21f9214c8).

## The cost of a shortcut

In the starting app, a business action names a DynamoDB table and raises a FastAPI exception. Changing storage or the HTTP edge means editing the action itself.

`src_python/monolith/monolith.py` (lines 62-73, comments omitted):

```python
def save_user(data: dict):
    save_to_dynamodb("Users", data)
    return data

def get_user(user_id: str):
    user = get_from_dynamodb("Users", {"id": user_id})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
```

This coupling grows costly when assistants make changes. A plausible local edit can spread a platform choice through several files. Clear boundaries give a reviewer and an assistant a smaller place to look.

**When to skip the pattern:** A short-lived prototype with one owner may not need these extra files. Start with one port when an action must survive a change in storage, entry point, or team.

## Make the dependency direction visible

A use case holds the business action, and a port names the behavior it needs. An adapter calls an external system; the composition root connects that adapter at startup. The database call stays outside the use case.

Follow the arrows in the diagram: which part owns the database choice?

![Request flow through HTTP, use case, repository port, adapter, and store](https://walidshaari.github.io/sample-portable-app-reinvent/assets/architecture-boundary.png)

Follow the request from HTTP to the use case, port, adapter, and store. The use case and port form the core. Both the use case and database adapter depend on the port's contract.

`base_python/clean/application/use_cases/create_user.py` (lines 7-22, docstrings omitted):

```python
class CreateUserUseCase:
    def __init__(self, user_repository: UserRepository):
        self._user_repository = user_repository

    async def execute(self, input_data: Dict[str, Any]) -> User:
        name = input_data.get("name")
        email = input_data.get("email")
        user_id = str(uuid.uuid4())
        user = User(user_id, name, email)
        await self._user_repository.create(user)
        return user
```

The port promises `create`, `find_by_id`, `find_all`, and `delete`. The registry in `infrastructure/composition.py` selects `memory`, `dynamodb`, or `postgres` from configuration. That is an explicit extension point. It does not promise that every future data model will fit this contract.

The entity checks input types before using string operations. A missing email or numeric name raises `ValueError`, which the HTTP edge maps to 400. [Entity and HTTP tests](https://github.com/walidshaari/sample-portable-app-reinvent/tree/b9ef30a2e7d73311f12a6e3e2360c1a21f9214c8/base_python/clean/tests) cover both cases. A 28 September 2026 rehearsal capture also shows HTTP 400 on EKS and kind. A CLI command or queue consumer can use the same entity rule without importing FastAPI.

## One image. Two deployments. Separate data.

The terminal walkthrough replays the read-only [`demo/09-two-targets.sh`](https://github.com/walidshaari/sample-portable-app-reinvent/blob/b9ef30a2e7d73311f12a6e3e2360c1a21f9214c8/demo/09-two-targets.sh) audit. It checks runtime labels, store readiness, a core fingerprint matching the checkout, and pod digests matching each other and the locally built image. It stops on a mismatch. The 28 September 2026 recording documents the rehearsal build. The public package needs a new digest proof.

*Running pod image IDs in the 28 September 2026 rehearsal capture:*

```text
EKS   sha256:185f46d5fb5e59cbe8240dfcf091b4bd3f5d21b5a3d0ed65c4af5652ff40e8f9
kind  sha256:185f46d5fb5e59cbe8240dfcf091b4bd3f5d21b5a3d0ed65c4af5652ff40e8f9
one container digest on both clusters
container digest matches the locally built image
```

The paired digest lines answer whether the pods match. The diagram answers what differs around them.

![The same captured application image in EKS and kind, with separate stores](https://walidshaari.github.io/sample-portable-app-reinvent/assets/two-placements.png)

EKS uses Region settings, identity, and storage. kind uses its own settings and local PostgreSQL. The diagram has no replication path between their stores. A write in one location is not implied to appear in the other.

kind and EKS are both Kubernetes. This is placement portability within one compute model. A Lambda integration would need its own packaging and runtime proof; the EKS/kind image digest cannot establish that claim.

The public source has the same core and PostgreSQL adapter bytes as the rehearsal checkout. Its smaller package changes the image build, so the rehearsal digest does not prove what digest the public package will produce. Build and deploy the public package before making a fresh live digest claim.

**Placement is an architectural option.** Recovery and residency require a design for data, traffic, access, operations, and evidence.

## Separate a configuration change from a technology change

| Change | Code that moves | Work still required |
| --- | --- | --- |
| Same PostgreSQL API, different placement | Deployment settings and credentials; the existing adapter remains | Network, secrets, operations, and data movement |
| DynamoDB model to PostgreSQL model | A new adapter and one registry entry; the core remains | Behavior checks, schema, migration, and cutover |

Two adapter decisions preserve the existing behavior:

- `create()` uses `INSERT ... ON CONFLICT DO UPDATE` to keep the port's overwrite behavior.
- The schema omits a foreign key for `orders.user_id` because the domain accepts an order for an unknown user. An adapter must not introduce a new business rule.

`base_python/clean/infrastructure/composition.py` (lines 39-43):

```python
BACKENDS: Dict[str, str] = {
    "memory": "infrastructure.repositories.in_memory_backend:build",
    "dynamodb": "infrastructure.repositories.dynamodb_backend:build",
    "postgres": "infrastructure.repositories.postgres_repositories:build",
}
```

The prepared `demo/04-tech-axis.sh` replays the adapter addition and runs the same repository contract against each implementation. On stage, call it a patch replay. It is not a spontaneous database migration.

The sample migration job creates tables with `CREATE TABLE IF NOT EXISTS`. It does not version schema changes or move records between databases. A real cutover needs a backfill, verification, a write strategy, and rollback steps.

## Give agents a map, then test the boundary

Clear names and small ports help an assistant find the intended change. Kiro steering states the dependency rule. Skills describe repeatable tasks, and an MCP server exposes architecture checks. In an IDE session, Kiro invokes the configured PreToolUse hook before an agent tool edit is applied. The hook can block an SDK import into the domain with `AG001` and exit code `2`.

The repair is concrete: define the behavior the use case needs as a port, implement an adapter outside the core, inject it at composition, and run the shared contract. This gives entity rules, adapter behavior, contract tests, and HTTP responses distinct places to inspect when something fails.

Teams can adopt the pattern one action at a time. Extract one action and one port before refactoring the whole app. Layout alone cannot guarantee that an assistant is correct; tests and review still matter.

The on-stage command replays a Kiro tool payload locally. Hook coverage depends on how edits are made. The separate git hook and CI template must be installed and enabled in a target repository. Parse warnings from the architecture checker also need a separate syntax or test gate.

## Say exactly what the evidence supports

| Claim | Evidence in this sample | What remains |
| --- | --- | --- |
| Code portability | A successful stage check compares the core fingerprint and pod digests across EKS, kind, and the checkout | Repeat against the final public image build |
| Separate data placement | Each target selects its own store | No shared data or failover is implied |
| Recovery | The app can run in two placements | Set RTO and RPO, then test backup, restore or replication, and traffic switching |
| Data residency | The design makes store placement visible | Verify data, backups, logs, telemetry, access, and permitted transfers |

kind runs on one laptop node. Its two PostgreSQL instances do not establish site-level recovery. It is an on-premises stand-in that shows the application running locally with the tested dependencies and network setup.

## Rehearse what each screen proves

The ARC401 session is 40 minutes: 10 minutes for the change tax, core boundary, and two change axes; 25 minutes for the request path and demos; and five minutes for questions. In the demo, show the EKS/kind proof, change Region persistence, replay the adapter addition, and reject the agent shortcut. Keep the two-target proof, adapter contract, and guard rejection if time runs short.

Ask a colleague to explain the deployment diagram without narration. If they infer automatic failover or compliant residency, revise the line you used. The rehearsal works when they can say what stayed fixed, what changed, and what remains unproved.

The short replays trace the request, pair the Region and laptop digest checks, distinguish the two change axes, and show the guard rejection. In the first terminal walkthrough, the EKS and kind results appear in one view. Their digest rows sit together before the recording moves to the separate store settings.

### Four short evidence replays

These captioned replays have no voiceover. They summarize local tests and dated deployment evidence.

| Replay | Video |
| --- | --- |
| Follow one request | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/_renders/01-request-path.mp4) |
| One image, two placements | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/_renders/02-two-placements.mp4) |
| Two change axes | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/_renders/03-change-axes.mp4) |
| Agent guard | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/_renders/04-agent-guard.mp4) |

### Four terminal walkthroughs

The deployment and adapter sections contain dated rehearsal captures. The share versions use a synthetic system voice. Silent stage versions and narration text are available on the [visual article](https://walidshaari.github.io/sample-portable-app-reinvent/arc401/).

| Walkthrough | Narrated video |
| --- | --- |
| One image, two placements | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/clips/_build/04-one-image-two-placements/offline/04-one-image-two-placements.share.mp4) |
| Change the store by configuration | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/clips/_build/05-swap-the-database-with-config/offline/05-swap-the-database-with-config.share.mp4) |
| Add a database adapter | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/clips/_build/06-new-database-one-new-file/offline/06-new-database-one-new-file.share.mp4) |
| Stop the agent write | [Watch](https://walidshaari.github.io/sample-portable-app-reinvent/recordings/clips/_build/09-stop-the-agent-before-it-writes/offline/09-stop-the-agent-before-it-writes.share.mp4) |

## Source notes and limits

The code excerpts match [public source commit `b9ef30a`](https://github.com/walidshaari/sample-portable-app-reinvent/tree/b9ef30a2e7d73311f12a6e3e2360c1a21f9214c8). The videos and EKS/kind captures come from the rehearsal build. Its core and PostgreSQL adapter bytes match the public source. Its image digest does not establish the public build's digest. For a new live proof, build and deploy the public package to both targets, then run `demo/09-two-targets.sh`.

For recovery objectives and sovereignty controls, see the [AWS Reliability Pillar's recovery strategies](https://docs.aws.amazon.com/wellarchitected/latest/framework/rel_planning_for_recovery_disaster_recovery.html), the [AWS Digital Sovereignty Lens on verifiable data controls](https://docs.aws.amazon.com/wellarchitected/latest/digital-sovereignty-lens/dssec07.html), and [guidance on residency for logs](https://docs.aws.amazon.com/wellarchitected/latest/data-residency-hybrid-cloud-services-lens/drhcsec01-bp02.html). This educational sample makes no compliance certification.

## Share feedback on ARC401

Did the architecture boundary and EKS/kind walkthrough answer your questions? Tell us what was clear and what needs another example.

Scan this QR code with the AWS Events app to rate ARC401:

![QR code for the ARC401 session survey in the AWS Events app](https://walidshaari.github.io/sample-portable-app-reinvent/assets/arc401-session-survey.png)
