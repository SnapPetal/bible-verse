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

All mutation flags remain false in this amendment. Setup authentication stays read-only and does not enable this gate. No release, upload or AWS policy change is performed by CI or merging source. A partial/mixed pair or failed postflight must leave deployment paused. Do not detach the managed guard during the transition, since it supplies the global action ceiling; any rollback must restore the reviewed prior inline guard before a separately authorized detachment.
