# Bible executor policy pair

`BibleVerseCfnExecution.json` and `BibleVerseProviderReadGuard.json` are one reviewed effective-permission unit. `executor-pair.json` pins their exact canonical hashes, purpose, executor, managed-policy ARN and retention. They must not be installed or reviewed independently.

The inline policy is 9,554 characters. The managed guard is 3,386 characters. Both fit their respective 10,240 aggregate-inline and 6,144 managed-policy limits. The managed document contains the global deny-all-other-actions guard and its own exact executor-principal and us-east-1 region fences. The inline retains its own resource, condition, principal and region restrictions.

The retained managed policy is attached only to BibleVerseCfnExecution. It must not be attached to users, groups or another role; the executor must have exactly this managed policy and its existing FoundationStagedPolicy inline policy. No trust, provider, runtime-role or permissions-boundary change is part of this pair.

The reviewed provider reads cover Bible's existing helper-role metadata, existing deployment layer and data-bucket metadata/object names (21 bucket metadata actions). The unsupported s3:GetBucketDefaultStorageClass action is absent from all capability lists and denied by the global ceiling. Layer inspection may return a download URL. They do not add helper writes, data-file content reads, data writes or access to other projects' asset buckets. Existing two-app release permissions and exact deny scopes are otherwise preserved by the frozen proposal. These files do not apply AWS changes.

## Verification and activation

Offline `python scripts/deployment/bible_deploy.py verify` checks the real local pair even while activation flags are false. The ordinary Java activation gate additionally requires:

- `executor_policy_pair_sha256`: the reviewed manifest digest.
- `executor_policy_operator_verification`: the exact bounded fields validated in `executor_policy_pair.py`, including the two observed policy hashes, default managed-policy version (`vN`), exact executor-only attachment inventory, unchanged inline name, absent boundary and a nonzero `receipt_sha256`.

The receipt hash refers to an immutable, independently supplied and coordinator-reviewed operator postflight observation. Only its non-sensitive digest and bounded identity/hash fields belong in activation configuration. Do not fabricate the receipt, use a unit-test sentinel, copy raw IAM responses or publish approval transcripts. The actual writer must inspect the deployed default policy version, its document, attachments and executor inventory before supplying that evidence.

The source gate pins that approved observation; it does not authenticate an arbitrary receipt by itself, read live IAM, or prove that policy attachments have not drifted after the observation. Deploy receives no new IAM inspection permission. A missing, extra or mismatched pairing/evidence field blocks activation. Live attachment verification remains an operator responsibility and a coordinated setup prerequisite.

Historical policy amendment note: mutation flags were false when this policy pair was published. Setup authentication stays read-only and does not enable this gate. No release, upload or AWS policy change is performed by CI or merging source. A partial/mixed pair or failed postflight must leave deployment paused. Do not detach the managed guard during the transition, since it supplies the global action ceiling; any rollback must restore the reviewed prior inline guard before a separately authorized detachment.

## Ordinary application build and deploy (local corrective proposal)

The proposed `java-release.yml` replaces the package-input workflow with one ordinary main-push or no-input manual run. Publishing this proposal would change deployment behavior: it is not authorized or enabled by this local preparation. The existing production environment and dedicated roles remain in use. Do not also enable the retired legacy Deploy workflow.

The run builds and tests the checked-out current main, synthesizes only the application with `--no-lookups`, and compares its non-code configuration with the live app. Unsupported helper, data, IAM, API, DNS, runtime configuration or retention changes stop before publication. It conditionally uploads the built JAR under the private Bible bucket's `assets/<sha256>.jar`, verifies the returned version and checksum, then submits inline app templates through the dedicated executor. Existing Lambda snapshots remain live until the new snapshots are verified, after which the two `live` aliases move. Old versions are retained. No separate user-built bundle, phase-template upload, review artifact, public checkpoint reference, hash field or review/release dispatch is required. Public-source build, compiler and test diagnostics remain visible before AWS authentication; source synthesis uses no lookups and keeps template stdout off the public log. Later AWS responses and private configuration are sanitized. GitHub reruns after a pre-write failure are allowed, but prior deterministic operations and partial or uncertain cloud state stop for reconciliation.

Only the JAR is newly stored in S3 (one object when absent, exact build size); immutable existing matching bytes may be reused. The existing private versioned bucket retains it until separately authorized cleanup. Templates, live configuration and transient operation state remain private to the runner and AWS CloudFormation; they are not uploaded as GitHub artifacts or printed in logs. Normal S3 storage/request and GitHub runner charges apply; the job timeout is 30 minutes, not a price guarantee.

Failures stop the run without application-level automatic retry or cleanup. CloudFormation's service rollback behavior still applies. A lost or uncertain submission must be reconciled, not blindly repeated; a partial second-stage failure can leave one alias moved. Retained old versions allow a separately authorized recovery. Source merge and push-trigger activation must be coordinated: the currently enabled workflow would react to a main push after publication. No current local test authorizes an actual release, upload or standing future assistant action.

The old packet/provenance modules remain unused compatibility code in this minimal proposal; their tests do not prove the new runner. Policy-pair checks, exact application scope, private artifact integrity and readiness checks remain required. Build reproducibility tools remain available but no separately prepared/rebuilt packet is a prerequisite for this ordinary path. Read-only setup authentication is unchanged.
