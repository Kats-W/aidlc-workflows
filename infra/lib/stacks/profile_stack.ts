import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';

/**
 * Props for {@link ProfileStack}.
 */
export interface ProfileStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
  /** External CRM endpoint URL that receives conversation summaries. */
  readonly crmEndpoint: string;
}

/**
 * ProfileStack (U-05) provisions the SDK customer-profile attribution and the
 * asynchronous CRM summary write-back:
 *
 *   - CustomerProfileLambda (Python 3.12, 256 MB, 10s): hashes the au ID into a
 *     stable customerId and looks up the customer's profile/tier in
 *     CustomerHistory for the Connect contact flow (US-5.1 / US-5.2).
 *   - CrmWriterLambda (Python 3.12, 256 MB, 30s): SQS-triggered; POSTs the
 *     conversation summary to the CRM with exponential back-off, skipping
 *     anonymous customers and DLQ'ing terminal failures (US-6.3).
 *   - CrmWriteQueue (KMS-encrypted) -> CrmWriterLambda, with a CrmWriteDlq
 *     dead-letter queue (14-day retention) for poison/terminal messages.
 *   - CloudWatch error alarms for both functions.
 *
 * All cross-cutting names/ARNs come from SharedInfraStack SSM parameters; IAM is
 * least-privilege (no "*" actions) and bounded by the shared permission boundary.
 */
export class ProfileStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ProfileStackProps) {
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

    const customerHistoryTableName = p(`${base}/dynamodb/customer-history-table-name`);
    const crmApiKeyArn = p(`${base}/secrets/crm-api-key-arn`);
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

    // ARN reconstructed from the exported table name (SSM exports the name).
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;

    // -----------------------------------------------------------------------
    // 2. SQS: CRM write queue + dead-letter queue (KMS-encrypted).
    // -----------------------------------------------------------------------
    const dlq = new sqs.Queue(this, 'CrmWriteDlq', {
      queueName: `${prefix}-crm-write-dlq`,
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: cmk,
      enforceSSL: true,
    });

    const crmWriteQueue = new sqs.Queue(this, 'CrmWriteQueue', {
      queueName: `${prefix}-crm-write`,
      retentionPeriod: cdk.Duration.days(4),
      visibilityTimeout: cdk.Duration.seconds(180), // >= 6x CrmWriter timeout.
      encryption: sqs.QueueEncryption.KMS,
      encryptionMasterKey: cmk,
      enforceSSL: true,
      deadLetterQueue: { queue: dlq, maxReceiveCount: 3 },
    });

    // -----------------------------------------------------------------------
    // 3. CustomerProfileLambda (10s, 256 MB).
    // -----------------------------------------------------------------------
    const profileRole = this.makeLambdaRole(
      'CustomerProfileRole',
      `${prefix}-customer-profile-role`,
      permissionBoundary,
    );

    const customerProfile = new lambda.Function(this, 'CustomerProfileLambda', {
      functionName: `${prefix}-customer-profile`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.profile.handler.lambda_handler',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
      }),
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: profileRole,
      environment: {
        CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
        POWERTOOLS_SERVICE_NAME: 'u-05-customer-profile',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // Least-privilege: read the PROFILE item on CustomerHistory via the GSI.
    profileRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryProfileRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:Query'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    cmk.grantEncryptDecrypt(profileRole);

    // -----------------------------------------------------------------------
    // 4. CrmWriterLambda (30s, 256 MB) — SQS-triggered.
    // -----------------------------------------------------------------------
    const crmWriterRole = this.makeLambdaRole(
      'CrmWriterRole',
      `${prefix}-crm-writer-role`,
      permissionBoundary,
    );

    const crmWriter = new lambda.Function(this, 'CrmWriterLambda', {
      functionName: `${prefix}-crm-writer`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.profile.crm_writer.lambda_handler',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
      }),
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      role: crmWriterRole,
      environment: {
        CRM_ENDPOINT: props.crmEndpoint,
        CRM_API_KEY_ARN: crmApiKeyArn,
        CRM_DLQ_URL: dlq.queueUrl,
        POWERTOOLS_SERVICE_NAME: 'u-05-crm-writer',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    crmWriter.addEventSource(new SqsEventSource(crmWriteQueue, { batchSize: 5 }));

    // Least-privilege: consume the CRM write queue, send to DLQ, read the
    // CRM API key secret, and use the shared CMK for queue/secret decryption.
    crmWriterRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrmWriteQueueConsume',
        effect: iam.Effect.ALLOW,
        actions: [
          'sqs:ReceiveMessage',
          'sqs:DeleteMessage',
          'sqs:GetQueueAttributes',
        ],
        resources: [crmWriteQueue.queueArn],
      }),
    );
    crmWriterRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrmWriteDlqSend',
        effect: iam.Effect.ALLOW,
        actions: ['sqs:SendMessage'],
        resources: [dlq.queueArn],
      }),
    );
    crmWriterRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CrmApiKeyRead',
        effect: iam.Effect.ALLOW,
        actions: ['secretsmanager:GetSecretValue'],
        resources: [crmApiKeyArn],
      }),
    );
    cmk.grantEncryptDecrypt(crmWriterRole);

    // -----------------------------------------------------------------------
    // 5. CloudWatch error-rate alarms.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'CustomerProfileErrorAlarm', {
      alarmName: `${prefix}-customer-profile-errors`,
      metric: customerProfile.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'CrmWriterErrorAlarm', {
      alarmName: `${prefix}-crm-writer-errors`,
      metric: crmWriter.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 3,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 6. Tags + outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-05');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    new cdk.CfnOutput(this, 'CustomerProfileFunctionName', {
      value: customerProfile.functionName,
    });
    new cdk.CfnOutput(this, 'CrmWriterFunctionName', { value: crmWriter.functionName });
    new cdk.CfnOutput(this, 'CrmWriteQueueUrl', { value: crmWriteQueue.queueUrl });
    new cdk.CfnOutput(this, 'CrmWriteDlqUrl', { value: dlq.queueUrl });
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
