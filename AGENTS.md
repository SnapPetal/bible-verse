# AGENTS.md

This file provides guidance to coding agents working with code in this repository.

## Build & Development Commands

### Java (from project root)

- `mvn clean verify` — build, test, and package (CI uses this)
- `mvn spotless:apply` — auto-format Java, POM, JS, and Markdown (Palantir Java format)
- `mvn test -Dtest=BibleVerseApplicationTests#methodName` — run a single test
- Output artifact: `target/bibleverse-2.0.0-aws.jar` (shaded JAR referenced by CDK)

### CDK Infrastructure (from `.infrastructure/`)

- `yarn install && yarn cdk synth` — synthesize CloudFormation
- `yarn cdk deploy --all` — deploy to AWS
- `yarn cdk diff` — preview changes
- `npm run test` — run CDK Jest tests

### Toolchain

Managed via `mise.toml`: Java Corretto 25, Maven 3.9, Node.js 24.

## Architecture

Serverless REST API returning random Bible verses in CSB (English) and Greek translations.

**Request flow:** API Gateway v2 → Lambda (Spring Cloud Function adapter) → `RandomBibleVerseHandler` → pre-indexed in-memory verse data

**Key design decisions:**
- Bible XML files (CSB + Greek) are stored in S3, loaded once at Lambda init, and pre-indexed into `List<Book>` / `Chapter` / `Verse` records for O(1) random access. DOM documents are discarded after indexing.
- SnapStart is enabled on the RandomBibleVerse Lambda with a published version + `live` alias. The API Gateway routes to the alias, not `$LATEST`.
- Two Spring Cloud Functions registered via `SPRING_CLOUD_FUNCTION_DEFINITION` env var: `randomBibleVerseHandler` (default route) and `aboutHandler` (`/about`).
- The Java app must be built (`mvn clean verify`) before `cdk deploy` since CDK references the JAR directly.

**Data files:** `.infrastructure/lib/data/files/{csb,greek}/bible.xml` — deployed to S3 via `BucketDeployment`.

## CI/CD

GitHub Actions (`.github/workflows/deploy.yml`): push to `main` → `mvn clean verify` → `cdk deploy`. AWS auth uses GitHub OIDC and the `AWS_ROLE_TO_ASSUME` repository secret.

## Code Formatting

Spotless runs automatically during `mvn verify`. Run `mvn spotless:apply` before committing to avoid failures. Enforces Palantir Java format, POM sorting (by scope then groupId), and Prettier for JS.

Maven Enforcer bans `com.amazonaws:aws-java-sdk*` artifacts so AWS service clients stay on AWS SDK for Java v2.
