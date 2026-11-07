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

## Manual Java release consent and preparation

Java release storage/sharing consent is requested for each authenticated manual run, not enabled by global storage/sharing switches. Both inputs default to false:

- `approve_private_records`: approve this pinned, reviewed package on the GitHub Actions runner and immutable records in the private `bible-verse-cdk-assets-664759038511-us-east-1` bucket under `reviews/<content-sha256>.json`. Records may contain reviewed deployment templates/configuration, release plans, controls, operation checkpoints and bounded reconciliation evidence. Credentials, unrelated private data, user transcripts and raw operator events are excluded.
- `approve_public_references`: approve publishing only bounded reference fields in this public repository's Actions artifacts: schema, bucket, owner, content-addressed key, immutable version ID, byte count, SHA256/base64 checksum and AES256 label. No template, Environment values or raw record contents are published.

Each consent is bound to that run's verified actor, current source commit, mode, exact package/template/asset/stage pins and selected bundle or review-artifact reference. Generated records carry the authenticated input digest, checked again before storage. This internal digest is not an extra user input or an approval by itself. The inputs are user consent for one specified operation, not standing permission for an assistant to approve future runs; assistant-triggered releases/uploads still require actual user approval.

Public references expire after three days. Private S3 records are immutable/versioned and retained indefinitely unless separately approved cleanup is performed. Each record is limited to 4,000,000 bytes. Per run:

- `review`: at most one private review record and one public reference, up to 4 MB private storage.
- `release`: the five bounded workflow submission slots permit at most ten private intent/response records and ten public references, up to 40 MB private storage. The ordinary two-phase path normally makes four submissions, each recorded by an intent and response; completed or stopped paths can create fewer records.
- `operator-reconcile`: at most one bounded evidence record and one public reference, up to 4 MB private storage. It does not authorize raw CloudTrail-event publication or automatic re-execution.

These are upper bounds for new checkpoint records, not a flat price. The earlier setup CI cap is not standing authorization for future runs. Normal S3 Standard storage, PUT/readback/read requests, transfer where applicable and GitHub Actions/artifact charges may apply. Standard public GitHub-hosted compute is generally free; account-specific billing and storage allowances still apply. Private retention costs continue after public references expire. Partial failure can leave already verified immutable records retained.

Before a review/release run, the exact JAR, two stage templates and private review bundle must already be prepared, reviewed and stored with verified versioned receipts. These consent inputs do not prepare them or authorize their initial upload. The separate nondeploy two-clean-build proof must pass for the exact proposed source/toolchain before relying on byte-identical review rebuilds. Source-only synthesis and full JAR/template/asset identity checks must remain strict.

This source amendment keeps activation flags false and releases no application code. The read-only setup-auth workflow can still inspect its fixed identities and bucket metadata after a legitimate activation; it cannot change activation or perform uploads/deployment. A successful setup probe is not a release approval or reproducible-build proof.
