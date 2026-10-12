import { Stack, StackProps, RemovalPolicy, Duration, Annotations } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as route53 from 'aws-cdk-lib/aws-route53';
import * as targets from 'aws-cdk-lib/aws-route53-targets';
import {
  HttpApi,
  DomainName,
  HttpMethod,
  CorsHttpMethod,
} from 'aws-cdk-lib/aws-apigatewayv2';
import {
  HttpLambdaIntegration,
} from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as path from "path";
import * as logs from 'aws-cdk-lib/aws-logs';
import { RetentionDays } from 'aws-cdk-lib/aws-logs';

export class BibleVerseStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const certArn = 'arn:aws:acm:us-east-1:664759038511:certificate/1554896f-abd0-4a6b-8955-710f2f4a642b';
    const domainName = 'bibleverse.thonbecker.com';

    // Create log groups for Lambda functions
    const aboutFunctionLogGroup = new logs.LogGroup(this, 'AboutFunctionLogGroup', {
      logGroupName: '/aws/lambda/BibleVerseStack-AboutFunction',
      retention: RetentionDays.THREE_DAYS,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const randomBibleVerseFunctionLogGroup = new logs.LogGroup(this, 'RandomBibleVerseFunctionLogGroup', {
      logGroupName: '/aws/lambda/BibleVerseStack-RandomBibleVerseFunction',
      retention: RetentionDays.THREE_DAYS,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const aboutFunction = new lambda.Function(this, 'AboutFunction', {
      runtime: lambda.Runtime.JAVA_25,
      memorySize: 1024,
      timeout: Duration.seconds(30),
      snapStart: lambda.SnapStartConf.ON_PUBLISHED_VERSIONS,
      logGroup: aboutFunctionLogGroup,
      handler: 'org.springframework.cloud.function.adapter.aws.FunctionInvoker',
      code: lambda.Code.fromAsset('../target/bibleverse-2.0.0-aws.jar'),
      environment: {
        'SPRING_CLOUD_FUNCTION_DEFINITION': 'aboutHandler'
      },
    });

    Annotations.of(aboutFunction).acknowledgeWarning('@aws-cdk/aws-lambda:snapStartRequirePublish');
    const aboutVersion = aboutFunction.currentVersion;
    const aboutAlias = new lambda.Alias(this, 'AboutFunctionLive', {
      aliasName: 'live',
      version: aboutVersion,
    });

    const bucket = new s3.Bucket(this, 'BibleVerseBucket', {
      bucketName: 'bible-verse-data-files',
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      minimumTLSVersion: 1.2,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    const randomBibleVerseFunction = new lambda.Function(this, 'RandomBibleVerseFunction', {
      runtime: lambda.Runtime.JAVA_25,
      memorySize: 1024,
      timeout: Duration.seconds(30),
      snapStart: lambda.SnapStartConf.ON_PUBLISHED_VERSIONS,
      logGroup: randomBibleVerseFunctionLogGroup,
      handler: 'org.springframework.cloud.function.adapter.aws.FunctionInvoker',
      code: lambda.Code.fromAsset('../target/bibleverse-2.0.0-aws.jar'),
      environment: {
        'SPRING_CLOUD_FUNCTION_DEFINITION': 'randomBibleVerseHandler',
        'BIBLEVERSE_S3_BUCKET_NAME': bucket.bucketName,
      },
    });

    Annotations.of(randomBibleVerseFunction).acknowledgeWarning('@aws-cdk/aws-lambda:snapStartRequirePublish');
    const randomBibleVerseVersion = randomBibleVerseFunction.currentVersion;
    const randomBibleVerseAlias = new lambda.Alias(this, 'RandomBibleVerseLive', {
      aliasName: 'live',
      version: randomBibleVerseVersion,
    });

    const deployment = new s3deploy.BucketDeployment(this, 'DeployBibleVerse', {
      sources: [s3deploy.Source.asset(path.join(__dirname, 'data', 'files'))],
      destinationBucket: bucket,
    });

    deployment.deployedBucket.grantRead(randomBibleVerseFunction);
    randomBibleVerseFunction.node.addDependency(deployment);

    const dn = new DomainName(this, 'DomainNameBibleVerse', {
      domainName,
      certificate: acm.Certificate.fromCertificateArn(this, 'CertificateBibleVerse', certArn),
    });

    const api = new HttpApi(this, 'HttpApiBibleVerse', {
      corsPreflight: {
        allowOrigins: ['*'],
        allowMethods: [CorsHttpMethod.GET, CorsHttpMethod.OPTIONS],
        allowHeaders: [
          'Content-Type',
          'Access-Control-Allow-Headers',
          'Access-Control-Allow-Origin',
          'Access-Control-Allow-Methods',
        ],
      },
      defaultIntegration: new HttpLambdaIntegration('DefaultIntegration', randomBibleVerseAlias),
      defaultDomainMapping: {
        domainName: dn,
      },
    });

    api.addRoutes({
      path: '/about',
      methods: [HttpMethod.GET],
      integration: new HttpLambdaIntegration('AboutIntegration', aboutAlias)
    });

    new route53.ARecord(this, 'AliasBibleVerse', {
      zone: route53.HostedZone.fromHostedZoneAttributes(this, 'HostedZoneBibleVerse', {
        hostedZoneId: 'Z0960080GF0UBO75OWWP',
        zoneName: 'thonbecker.com'
      }),
      recordName: 'bibleverse.thonbecker.com',
      target: route53.RecordTarget.fromAlias(new targets.ApiGatewayv2DomainProperties(dn.regionalDomainName, dn.regionalHostedZoneId)),
    });
  }
}
