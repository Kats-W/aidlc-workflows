import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Props for {@link ConversationStack}.
 */
export interface ConversationStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * ConversationStack (U-03) provisions the real-time AI conversation engine that
 * Amazon Connect contact flows invoke:
 *
 *   - RagHandlerLambda  (Python 3.12, 30s, 512 MB): PII mask -> personalize ->
 *     embed -> cosine search -> Claude answer -> history append. The 8s Connect
 *     budget is enforced *inside* the function via asyncio.wait_for(6s).
 *   - PersonalizerLambda (Python 3.12, 10s, 256 MB): standalone history-context
 *     builder (also used in-process by the RAG handler).
 *   - EscalationLambda  (Python 3.12, 10s, 256 MB): routes hit=false contacts
 *     to the human queue.
 *   - CsatHandlerLambda (Python 3.12, 10s, 256 MB): persists post-contact CSAT.
 *
 * All cross-cutting names/ARNs come from SharedInfraStack SSM parameters; IAM is
 * least-privilege (no "*" actions) and bounded by the shared permission boundary.
 */
export class ConversationStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ConversationStackProps) {
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
    const escalationQueueArn = p(`${base}/connect/escalation-queue-arn`);
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
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;
    const crawlBucketArn = `arn:aws:s3:::${crawlBucketName}`;

    // -----------------------------------------------------------------------
    // 2. Shared environment.
    // -----------------------------------------------------------------------
    const commonEnv: Record<string, string> = {
      VECTOR_STORE_TABLE_NAME: vectorStoreTableName,
      CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
      CRAWL_CONTENT_BUCKET: crawlBucketName,
      POWERTOOLS_SERVICE_NAME: 'u-03-conversation-engine',
      LOG_LEVEL: 'INFO',
    };

    const code = lambda.Code.fromAsset('..', {
      // In CI the deployment package is built by `uv`; placeholder bundling.
      exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
    });

    // -----------------------------------------------------------------------
    // 3. RagHandlerLambda (30s wall-clock; 6s asyncio budget enforced in code).
    // -----------------------------------------------------------------------
    const ragRole = this.makeLambdaRole('RagRole', `${prefix}-rag-role`, permissionBoundary);
    const ragHandler = new lambda.Function(this, 'RagHandlerLambda', {
      functionName: `${prefix}-rag-handler`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.rag_handler.handler.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 512,
      role: ragRole,
      environment: commonEnv,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // RAG: read VectorStore, read/write CustomerHistory, Bedrock embed + answer,
    // Comprehend DetectPiiEntities, SSM read.
    ragRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorStoreRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:Scan'],
        resources: [vectorStoreArn, `${vectorStoreArn}/index/*`],
      }),
    );
    ragRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'VectorCacheRead',
        effect: iam.Effect.ALLOW,
        actions: ['s3:GetObject'],
        resources: [`${crawlBucketArn}/vector-cache/*`],
      }),
    );
    ragRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryReadWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query', 'dynamodb:PutItem'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    ragRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockEmbedAndAnswer',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:InvokeModel'],
        resources: [
          `arn:aws:bedrock:${this.region}::foundation-model/amazon.titan-embed-text-v2:0`,
          `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-sonnet-4-6-20250514-v1:0`,
          `arn:aws:bedrock:${this.region}:${account}:inference-profile/jp.anthropic.claude-sonnet-4-6-20250514-v1:0`,
        ],
      }),
    );
    ragRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ComprehendDetectPii',
        effect: iam.Effect.ALLOW,
        // Comprehend Detect* APIs do not support resource-level scoping.
        actions: ['comprehend:DetectPiiEntities'],
        resources: ['*'],
      }),
    );
    cmk.grantEncryptDecrypt(ragRole);

    // -----------------------------------------------------------------------
    // 4. PersonalizerLambda (history-context builder; read-only history).
    // -----------------------------------------------------------------------
    const personalizerRole = this.makeLambdaRole(
      'PersonalizerRole',
      `${prefix}-personalizer-role`,
      permissionBoundary,
    );
    const personalizer = new lambda.Function(this, 'PersonalizerLambda', {
      functionName: `${prefix}-personalizer`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.rag_handler.personalizer.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: personalizerRole,
      environment: commonEnv,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });
    personalizerRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    cmk.grantEncryptDecrypt(personalizerRole);

    // -----------------------------------------------------------------------
    // 5. EscalationLambda (returns transfer attributes; no data access).
    // -----------------------------------------------------------------------
    const escalationRole = this.makeLambdaRole(
      'EscalationRole',
      `${prefix}-escalation-role`,
      permissionBoundary,
    );
    const escalation = new lambda.Function(this, 'EscalationLambda', {
      functionName: `${prefix}-escalation`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.rag_handler.escalation.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: escalationRole,
      environment: {
        ...commonEnv,
        ESCALATION_QUEUE_ARN: escalationQueueArn,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // -----------------------------------------------------------------------
    // 6. CsatHandlerLambda (writes CSAT to CustomerHistory).
    // -----------------------------------------------------------------------
    const csatRole = this.makeLambdaRole('CsatRole', `${prefix}-csat-role`, permissionBoundary);
    const csatHandler = new lambda.Function(this, 'CsatHandlerLambda', {
      functionName: `${prefix}-csat-handler`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.session_manager.csat_handler.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: csatRole,
      environment: commonEnv,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });
    csatRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:PutItem'],
        resources: [customerHistoryArn],
      }),
    );
    cmk.grantEncryptDecrypt(csatRole);

    // -----------------------------------------------------------------------
    // 7. Amazon Connect invoke permissions.
    //
    // Connect invokes these Lambdas from contact flows; each needs a resource
    // policy allowing connect.amazonaws.com to call lambda:InvokeFunction.
    // -----------------------------------------------------------------------
    const connectPrincipal = new iam.ServicePrincipal('connect.amazonaws.com');
    for (const [fn, sid] of [
      [ragHandler, 'AllowConnectInvoke'],
      [escalation, 'AllowConnectInvoke'],
      [personalizer, 'AllowConnectInvoke'],
      [csatHandler, 'AllowConnectInvoke'],
    ] as [lambda.Function, string][]) {
      fn.addPermission(sid, {
        principal: connectPrincipal,
        action: 'lambda:InvokeFunction',
      });
    }

    // -----------------------------------------------------------------------
    // 8. SSM: publish Lambda ARNs for contact flow wiring in OmnichannelStack.
    // -----------------------------------------------------------------------
    const ssmParam = (id: string, name: string, value: string): ssm.StringParameter =>
      new ssm.StringParameter(this, id, { parameterName: name, stringValue: value });

    ssmParam('PRagHandlerArn', `${base}/lambda/rag-handler-arn`, ragHandler.functionArn);
    ssmParam('PEscalationArn', `${base}/lambda/escalation-arn`, escalation.functionArn);
    ssmParam('PPersonalizerArn', `${base}/lambda/personalizer-arn`, personalizer.functionArn);
    ssmParam('PCsatHandlerArn', `${base}/lambda/csat-handler-arn`, csatHandler.functionArn);

    // -----------------------------------------------------------------------
    // 9. CloudWatch error alarms.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'RagHandlerErrorAlarm', {
      alarmName: `${prefix}-rag-handler-errors`,
      metric: ragHandler.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'RagHandlerThrottleAlarm', {
      alarmName: `${prefix}-rag-handler-throttles`,
      metric: ragHandler.metricThrottles({ period: cdk.Duration.minutes(5) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 8. Tags + outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-03');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    new cdk.CfnOutput(this, 'RagHandlerFunctionName', { value: ragHandler.functionName });
    new cdk.CfnOutput(this, 'PersonalizerFunctionName', { value: personalizer.functionName });
    new cdk.CfnOutput(this, 'EscalationFunctionName', { value: escalation.functionName });
    new cdk.CfnOutput(this, 'CsatHandlerFunctionName', { value: csatHandler.functionName });
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
