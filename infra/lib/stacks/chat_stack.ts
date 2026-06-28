import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';

/**
 * Props for {@link ChatStack}.
 */
export interface ChatStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * ChatStack (U-08) exposes the RAG pipeline to a web chat UI as a streaming
 * HTTP endpoint — the customer-facing counterpart to the Connect voice path.
 *
 *   - ChatApiLambda (Python 3.12 + FastAPI): the same mask -> personalize ->
 *     embed -> cosine search pipeline as RagHandler, but it streams the Claude
 *     answer token-by-token over Server-Sent Events. It runs behind the AWS
 *     Lambda Web Adapter (LWA) layer, which serves the ASGI app via uvicorn and
 *     proxies a Function URL request to it in RESPONSE_STREAM mode.
 *
 * Access is lightly protected for a public demo: a generated demo key (Secrets
 * Manager, injected as DEMO_API_KEY) plus a low reserved-concurrency cap to
 * bound Bedrock spend. IAM mirrors RagHandler's least-privilege grants.
 */
export class ChatStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ChatStackProps) {
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
    const customerHistoryTableName = p(`${base}/dynamodb/customer-history-table-name`);
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

    // AWS Lambda Web Adapter (x86_64) — published by account 753240598075.
    // Serves the ASGI app and enables Function URL response streaming.
    const lwaLayer = lambda.LayerVersion.fromLayerVersionArn(
      this,
      'LambdaWebAdapter',
      `arn:aws:lambda:${this.region}:753240598075:layer:LambdaAdapterLayerX86:28`,
    );

    const vectorStoreArn = `arn:aws:dynamodb:${this.region}:${account}:table/${vectorStoreTableName}`;
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;
    const crawlBucketArn = `arn:aws:s3:::${crawlBucketName}`;

    // -----------------------------------------------------------------------
    // 2. Demo access key (generated; injected as DEMO_API_KEY via a dynamic
    //    reference so the plaintext never lands in the CloudFormation template).
    // -----------------------------------------------------------------------
    const demoKey = new secretsmanager.Secret(this, 'ChatDemoKey', {
      secretName: `${prefix}-chat-demo-key`,
      description: 'Shared demo key for the public chat API (x-demo-key header)',
      generateSecretString: { passwordLength: 32, excludePunctuation: true },
    });
    const demoKeyRef = `{{resolve:secretsmanager:${prefix}-chat-demo-key:SecretString:::}}`;

    // -----------------------------------------------------------------------
    // 3. ChatApiLambda (FastAPI + Lambda Web Adapter, response streaming).
    //    Like RagHandler it loads the ~877 MB vector cache on cold start, so it
    //    gets the same generous memory/ephemeral sizing. The LWA serves uvicorn
    //    (handler = run.sh) and streams SSE back through the Function URL.
    // -----------------------------------------------------------------------
    const chatRole = this.makeLambdaRole('ChatRole', `${prefix}-chat-role`, permissionBoundary);
    const chatFn = new lambda.Function(this, 'ChatApiLambda', {
      functionName: `${prefix}-chat-api`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'run.sh',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv', 'frontend', 'chat-ui'],
      }),
      layers: [pythonDepsLayer, lwaLayer],
      timeout: cdk.Duration.seconds(60),
      memorySize: 4096,
      ephemeralStorageSize: cdk.Size.mebibytes(1536),
      reservedConcurrentExecutions: 3,
      role: chatRole,
      environment: {
        VECTOR_STORE_TABLE_NAME: vectorStoreTableName,
        CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
        CRAWL_CONTENT_BUCKET: crawlBucketName,
        POWERTOOLS_SERVICE_NAME: 'u-08-chat-api',
        LOG_LEVEL: 'INFO',
        // Lambda Web Adapter configuration.
        AWS_LAMBDA_EXEC_WRAPPER: '/opt/bootstrap',
        AWS_LWA_INVOKE_MODE: 'response_stream',
        AWS_LWA_PORT: '8080',
        // App configuration.
        DEMO_API_KEY: demoKeyRef,
        CHAT_CORS_ORIGINS: '*',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });
    // {{resolve}} dynamic references do not create an implicit dependency.
    chatFn.node.addDependency(demoKey);

    // IAM (mirrors RagHandler): VectorStore read, vector-cache S3 read +
    // ListBucket, CustomerHistory read/write, Bedrock embed + answer, Comprehend.
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorStoreRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:BatchGetItem', 'dynamodb:Query', 'dynamodb:Scan'],
        resources: [vectorStoreArn, `${vectorStoreArn}/index/*`],
      }),
    );
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorCacheRead',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [`${crawlBucketArn}/vector-cache/*`],
      }),
    );
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorCacheListPrefix',
        effect: iam.Effect.ALLOW,
        actions: ['s3:ListBucket'],
        resources: [crawlBucketArn],
        conditions: { StringLike: { 's3:prefix': ['vector-cache/*'] } },
      }),
    );
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryReadWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:PutItem'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockEmbedAndAnswer',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0`,
          'arn:aws:bedrock:ap-northeast-3::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0',
          `arn:aws:bedrock:${this.region}:${account}:inference-profile/jp.anthropic.claude-haiku-4-5-20251001-v1:0`,
        ],
      }),
    );
    chatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ComprehendDetectPii',
        effect: iam.Effect.ALLOW,
        actions: ['comprehend:DetectPiiEntities'],
        resources: ['*'],
      }),
    );
    cmk.grantEncryptDecrypt(chatRole);

    // -----------------------------------------------------------------------
    // 4. Function URL with response streaming (no IAM auth; demo-key gated).
    // -----------------------------------------------------------------------
    const fnUrl = chatFn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,
      invokeMode: lambda.InvokeMode.RESPONSE_STREAM,
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.POST, lambda.HttpMethod.GET],
        allowedHeaders: ['content-type', 'x-demo-key'],
      },
    });

    // -----------------------------------------------------------------------
    // 5. Outputs.
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'ChatApiUrl', { value: fnUrl.url });
    new cdk.CfnOutput(this, 'ChatDemoKeySecretArn', { value: demoKey.secretArn });
    new ssm.StringParameter(this, 'PChatApiUrl', {
      parameterName: `${base}/chat/api-url`,
      stringValue: fnUrl.url,
    });
  }

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
