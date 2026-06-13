import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Props for {@link KnowledgePipelineStack}.
 */
export interface KnowledgePipelineStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
  /** JSON-encoded list of crawl target URLs (au Jibun Bank site/FAQ). */
  readonly targetUrls: readonly string[];
}

/**
 * KnowledgePipelineStack (U-02) provisions the weekly knowledge crawl + diff +
 * embedding pipeline:
 *
 *   - CrawlerLambda  (Python 3.12, 15 min, 1024 MB): weekly crawl + diff.
 *   - EmbedderLambda (Python 3.12, 10 min, 1024 MB): Titan v2 embed + upsert.
 *   - EventBridge Scheduler: weekly Sunday 02:00 JST (17:00 UTC Saturday).
 *   - Least-privilege IAM (no "*" actions): scoped DynamoDB / S3 / Bedrock /
 *     Lambda invoke.
 *   - CloudWatch error alarms for both functions.
 *
 * All cross-cutting resource names/ARNs are consumed from the SharedInfraStack
 * SSM parameters (region ap-northeast-1).
 */
export class KnowledgePipelineStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: KnowledgePipelineStackProps) {
    super(scope, id, props);

    const env = props.envName;
    const prefix = `au-jibun-bank-${env}`;
    const account = cdk.Stack.of(this).account;
    const base = `/au-jibun-bank/${env}`;

    // -----------------------------------------------------------------------
    // 1. Resolve SharedInfraStack exports from SSM Parameter Store.
    // -----------------------------------------------------------------------
    const p = (name: string): string =>
      ssm.StringParameter.valueForStringParameter(this, name);

    const vectorStoreTableName = p(`${base}/dynamodb/vector-store-table-name`);
    const contentDiffTableName = p(`${base}/dynamodb/content-diff-table-name`);
    const crawlBucketName = p(`${base}/s3/crawl-content-bucket-name`);
    const cmkArn = p(`${base}/kms/cmk-arn`);
    const permBoundaryArn = p(`${base}/iam/lambda-permission-boundary-arn`);
    const pythonDepsLayerArn = p(`${base}/lambda/python-deps-layer-arn`);

    const cmk = kms.Key.fromKeyArn(this, 'SharedCmk', cmkArn);
    const permissionBoundary = iam.ManagedPolicy.fromManagedPolicyArn(
      this,
      'LambdaPermBoundary',
      permBoundaryArn,
    );
    const pythonDepsLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      'PythonDepsLayer',
      pythonDepsLayerArn,
    );

    // ARNs reconstructed from names (SSM exports names, not ARNs, for tables/bucket).
    const vectorStoreArn = `arn:aws:dynamodb:${this.region}:${account}:table/${vectorStoreTableName}`;
    const contentDiffArn = `arn:aws:dynamodb:${this.region}:${account}:table/${contentDiffTableName}`;
    const crawlBucketArn = `arn:aws:s3:::${crawlBucketName}`;

    // -----------------------------------------------------------------------
    // 2. Shared environment for both functions.
    // -----------------------------------------------------------------------
    const commonEnv: Record<string, string> = {
      VECTOR_STORE_TABLE_NAME: vectorStoreTableName,
      CONTENT_DIFF_TABLE_NAME: contentDiffTableName,
      CRAWL_CONTENT_BUCKET: crawlBucketName,
      POWERTOOLS_SERVICE_NAME: 'u-02-knowledge-pipeline',
      LOG_LEVEL: 'INFO',
    };

    // -----------------------------------------------------------------------
    // 3. EmbedderLambda (created first so the crawler can reference its name).
    // -----------------------------------------------------------------------
    const embedderRole = this.makeLambdaRole('EmbedderRole', `${prefix}-embedder-role`, permissionBoundary);

    const embedder = new lambda.Function(this, 'EmbedderLambda', {
      functionName: `${prefix}-embedder`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.vector_store.handler.lambda_handler',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
      }),
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.minutes(10),
      // 1024MB (was 512): the final-batch cache rebuild scans the whole corpus
      // (~5,700 items x 1024-dim) into a numpy matrix. Even after dropping the
      // Decimal read path, peak resident set is ~300MB+, leaving thin headroom
      // at 512MB. Lambda scales CPU with memory, so the extra memory also speeds
      // up the numpy/JSON work; billed as duration x memory, halving rebuild
      // duration while avoiding timeout retries is cost-neutral-or-better.
      memorySize: 1024,
      role: embedderRole,
      environment: commonEnv,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // -----------------------------------------------------------------------
    // 4. CrawlerLambda.
    // -----------------------------------------------------------------------
    const crawlerRole = this.makeLambdaRole('CrawlerRole', `${prefix}-crawler-role`, permissionBoundary);

    const crawler = new lambda.Function(this, 'CrawlerLambda', {
      functionName: `${prefix}-crawler`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.crawler.handler.lambda_handler',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
      }),
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.minutes(15),
      memorySize: 1024,
      role: crawlerRole,
      environment: {
        ...commonEnv,
        CRAWLER_TARGET_URLS: JSON.stringify(props.targetUrls),
        EMBEDDER_FUNCTION_NAME: embedder.functionName,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // -----------------------------------------------------------------------
    // 5. Least-privilege IAM (no "*" actions).
    // -----------------------------------------------------------------------
    // Crawler: read/write ContentDiff, write S3 content, invoke Embedder.
    crawlerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ContentDiffReadWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:PutItem',
          'dynamodb:BatchWriteItem',
          'dynamodb:DeleteItem',
          'dynamodb:Scan',
          'dynamodb:Query',
        ],
        resources: [contentDiffArn, `${contentDiffArn}/index/*`],
      }),
    );
    crawlerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrawlBucketWrite',
        effect: iam.Effect.ALLOW,
        actions: ['s3:PutObject', 's3:GetObject', 's3:DeleteObject'],
        resources: [`${crawlBucketArn}/*`],
      }),
    );
    crawlerRole.addToPolicy(
      new iam.PolicyStatement({
        // Without bucket-level s3:ListBucket, GetObject on a non-existent
        // key (e.g. the BFS state object before its first save) returns
        // AccessDenied instead of NoSuchKey/404.
        sid: 'CrawlBucketList',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [crawlBucketArn],
      }),
    );
    crawlerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeEmbedder',
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [embedder.functionArn],
      }),
    );
    cmk.grantEncryptDecrypt(crawlerRole);

    // Embedder: read/write VectorStore, read S3 content, invoke Bedrock embed.
    embedderRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorStoreReadWrite',
        effect: iam.Effect.ALLOW,
        actions: [
          'dynamodb:GetItem',
          'dynamodb:PutItem',
          'dynamodb:DeleteItem',
          'dynamodb:Scan',
          'dynamodb:Query',
        ],
        resources: [vectorStoreArn, `${vectorStoreArn}/index/*`],
      }),
    );
    embedderRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrawlBucketRead',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [`${crawlBucketArn}/*`],
      }),
    );
    embedderRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorCacheWrite',
        effect: iam.Effect.ALLOW,
        actions: ['s3:PutObject'],
        resources: [`${crawlBucketArn}/vector-cache/*`],
      }),
    );
    embedderRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockEmbed',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
        ],
      }),
    );
    cmk.grantEncryptDecrypt(embedderRole);

    // -----------------------------------------------------------------------
    // 6. EventBridge Scheduler: weekly Sunday 02:00 JST (= Sat 17:00 UTC).
    // -----------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'CrawlScheduleRole', {
      roleName: `${prefix}-crawl-schedule-role`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Allows EventBridge Scheduler to invoke the CrawlerLambda',
    });
    schedulerRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [crawler.functionArn],
      }),
    );

    new scheduler.CfnSchedule(this, 'WeeklyCrawlSchedule', {
      name: `${prefix}-weekly-crawl`,
      description: 'Weekly au Jibun Bank knowledge crawl (Sun 02:00 JST)',
      flexibleTimeWindow: { mode: 'OFF' },
      // cron(min hour day month day-of-week year); 17:00 UTC Saturday == 02:00 JST Sunday.
      scheduleExpression: 'cron(0 17 ? * SAT *)',
      scheduleExpressionTimezone: 'UTC',
      target: {
        arn: crawler.functionArn,
        roleArn: schedulerRole.roleArn,
        retryPolicy: { maximumRetryAttempts: 2 },
      },
    });

    // -----------------------------------------------------------------------
    // 7. CloudWatch error-rate alarms.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'CrawlerErrorAlarm', {
      alarmName: `${prefix}-crawler-errors`,
      metric: crawler.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'EmbedderErrorAlarm', {
      alarmName: `${prefix}-embedder-errors`,
      metric: embedder.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 3,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 8. Tags + outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-02');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    new cdk.CfnOutput(this, 'CrawlerFunctionName', { value: crawler.functionName });
    new cdk.CfnOutput(this, 'EmbedderFunctionName', { value: embedder.functionName });
  }

  /**
   * Build a Lambda execution role with the shared permission boundary and the
   * AWS-managed basic execution policy (CloudWatch Logs).
   */
  private makeLambdaRole(
    id: string,
    roleName: string,
    permissionBoundary: iam.IManagedPolicy,
  ): iam.Role {
    return new iam.Role(this, id, {
      roleName,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      permissionsBoundary: permissionBoundary,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });
  }
}
