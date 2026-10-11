import * as cdk from 'aws-cdk-lib';
import {Match, Template} from 'aws-cdk-lib/assertions';
import {BibleVerseStack} from "../lib/bibleverse-stack";

test('S3 bucket is private, encrypted, and TLS-only', () => {
    const template = synth();

    template.hasResourceProperties('AWS::S3::Bucket', {
        PublicAccessBlockConfiguration: {
            BlockPublicAcls: true,
            BlockPublicPolicy: true,
            IgnorePublicAcls: true,
            RestrictPublicBuckets: true,
        },
        BucketEncryption: {
            ServerSideEncryptionConfiguration: [
                {
                    ServerSideEncryptionByDefault: {
                        SSEAlgorithm: 'AES256',
                    },
                },
            ],
        },
    });

    template.hasResourceProperties('AWS::S3::BucketPolicy', {
        PolicyDocument: {
            Statement: Match.arrayWith([
                Match.objectLike({
                    Effect: 'Deny',
                    Condition: {Bool: {'aws:SecureTransport': 'false'}},
                }),
                Match.objectLike({
                    Effect: 'Deny',
                    Condition: {NumericLessThan: {'s3:TlsVersion': 1.2}},
                }),
            ]),
        },
    });
});

test('only the random-verse function reads the Bible bucket', () => {
    const template = synth();

    template.hasResourceProperties('AWS::Lambda::Function', {
        Runtime: 'java25',
        Environment: {
            Variables: Match.objectLike({
                SPRING_CLOUD_FUNCTION_DEFINITION: 'randomBibleVerseHandler',
                BIBLEVERSE_S3_BUCKET_NAME: {Ref: Match.anyValue()},
            }),
        },
    });

    const javaFunctions = template.findResources('AWS::Lambda::Function', {
        Properties: {Runtime: 'java25'},
    });
    const about = Object.values(javaFunctions).find(
        (resource) =>
            resource.Properties?.Environment?.Variables?.SPRING_CLOUD_FUNCTION_DEFINITION === 'aboutHandler',
    );
    expect(about?.Properties.Environment.Variables.BIBLEVERSE_S3_BUCKET_NAME).toBeUndefined();

    const policyText = JSON.stringify(template.findResources('AWS::IAM::Policy'));
    expect(policyText).toContain('s3:GetObject');
    expect(policyText).toContain('RandomBibleVerseFunctionServiceRole');
    expect(policyText).not.toContain('AboutFunctionServiceRole');
});

function synth() {
    const app = new cdk.App();
    const stack = new BibleVerseStack(app, 'MyTestStack');
    return Template.fromStack(stack);
}
