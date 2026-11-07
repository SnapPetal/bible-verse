import * as cdk from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import * as fs from 'fs';
import * as path from 'path';
import { BibleVerseStack } from '../lib/bibleverse-stack';
import { BibleVerseStack as BeforeRetentionStack } from './fixtures/bibleverse-stack.before-retention';

// This compares source synthesis, not a redacted live-template fixture.
// Both stacks use the same CI-built JAR, repository XML, context and toolchain.
function synth(StackClass: typeof BibleVerseStack) {
  const context = JSON.parse(fs.readFileSync(path.join(__dirname, '../cdk.json'), 'utf8')).context;
  const app = new cdk.App({ context });
  const stack = new StackClass(app, 'bible-verse-stack', {
    env: { account: '664759038511', region: 'us-east-1' },
  });
  const template = Template.fromStack(stack).toJSON();
  const assembly = app.synth();
  const manifest = JSON.parse(fs.readFileSync(
    path.join(assembly.directory, `${stack.artifactId}.assets.json`), 'utf8'));
  const assets = Object.fromEntries(Object.entries(manifest.files).filter(([, item]) =>
    (item as { source: { path: string } }).source.path !== `${stack.artifactId}.template.json`));
  return { template, assets };
}

test('retention adoption changes exactly five lifecycle policies, preserving all other synthesis', () => {
  const before = synth(BeforeRetentionStack);
  const after = synth(BibleVerseStack);
  const versions = Object.keys(before.template.Resources).filter(id =>
    before.template.Resources[id].Type === 'AWS::Lambda::Version');
  expect(versions).toHaveLength(2);
  const targets = [
    'BibleVerseBucketF8938CE6',
    'BibleVerseBucketAutoDeleteObjectsCustomResource64B22329',
    'DeployBibleVerseAwsCliLayer2ED68A0E',
    ...versions,
  ];
  const expected = JSON.parse(JSON.stringify(before.template));
  for (const id of targets) {
    expected.Resources[id].DeletionPolicy = 'Retain';
    expected.Resources[id].UpdateReplacePolicy = 'Retain';
  }
  expect(after.template).toEqual(expected);
  expect(after.assets).toEqual(before.assets);
  expect(Object.keys(after.assets)).toHaveLength(5);
  expect(after.template.Resources.BibleVerseBucketF8938CE6.Properties.Tags)
    .toContainEqual({ Key: 'aws-cdk:auto-delete-objects', Value: 'true' });
  // Whole-template equality above covers all Properties, ServiceTokens, aliases,
  // version IDs, IAM, code/layer references, dependencies and metadata.
});
