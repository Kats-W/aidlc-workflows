import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudtrail from 'aws-cdk-lib/aws-cloudtrail';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as connect from 'aws-cdk-lib/aws-connect';
import * as lex from 'aws-cdk-lib/aws-lex';
import * as ssm from 'aws-cdk-lib/aws-ssm';

/**
 * Props for {@link SharedInfraStack}.
 */
export interface SharedInfraStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * SharedInfraStack provisions the cross-cutting core infrastructure for the
 * au Jibun Bank AI Agent (U-01): KMS CMK, five DynamoDB tables, the crawl
 * content S3 bucket, the CRM Secrets Manager secret, CloudWatch Logs,
 * CloudTrail, a VPC, IAM permission boundary + shared Lambda execution role,
 * an Amazon Connect instance, a Lex v2 bot shell, and the SSM parameters that
 * export every resource ARN/ID for downstream unit stacks.
 *
 * Region: ap-northeast-1. All data resources are encrypted with a single CMK.
 */
export class SharedInfraStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: SharedInfraStackProps) {
    super(scope, id, props);

    const env = props.envName;
    const account = cdk.Stack.of(this).account;
    const prefix = `au-jibun-bank-${env}`;

    // RemovalPolicy: dev tears down, staging/prod retains.
    const dataRemovalPolicy =
      env === 'dev' ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN;

    // -----------------------------------------------------------------------
    // 1. KMS CMK (shared by DynamoDB, S3, Logs, Secrets). Always RETAIN.
    // -----------------------------------------------------------------------
    const cmk = new kms.Key(this, 'SharedCmk', {
      alias: `alias/${prefix}-cmk`,
      description: `Shared CMK for au Jibun Bank AI Agent (${env})`,
      enableKeyRotation: true, // annual automatic rotation
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    // -----------------------------------------------------------------------
    // 2. DynamoDB x5 (On-Demand, CMK encrypted, PITR enabled)
    // -----------------------------------------------------------------------
    const commonTableProps = {
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: cmk,
      pointInTimeRecovery: true,
      removalPolicy: dataRemovalPolicy,
    } satisfies Partial<dynamodb.TableProps>;

    // VectorStore
    const vectorStore = new dynamodb.Table(this, 'VectorStore', {
      tableName: `${prefix}-vector-store`,
      partitionKey: { name: 'chunkId', type: dynamodb.AttributeType.STRING },
      ...commonTableProps,
    });
    vectorStore.addGlobalSecondaryIndex({
      indexName: 'gsi_sourceUrl',
      partitionKey: { name: 'sourceUrl', type: dynamodb.AttributeType.STRING },
    });

    // CustomerHistory
    const customerHistory = new dynamodb.Table(this, 'CustomerHistory', {
      tableName: `${prefix}-customer-history`,
      partitionKey: { name: 'customerId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'expiresAt',
      ...commonTableProps,
    });
    customerHistory.addGlobalSecondaryIndex({
      indexName: 'gsi_contactId',
      partitionKey: { name: 'contactId', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
    });

    // ImprovementSuggestions
    const improvementSuggestions = new dynamodb.Table(this, 'ImprovementSuggestions', {
      tableName: `${prefix}-improvement-suggestions`,
      partitionKey: { name: 'suggestionId', type: dynamodb.AttributeType.STRING },
      ...commonTableProps,
    });
    improvementSuggestions.addGlobalSecondaryIndex({
      indexName: 'gsi_status',
      partitionKey: { name: 'status', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'priorityScore', type: dynamodb.AttributeType.NUMBER },
    });
    improvementSuggestions.addGlobalSecondaryIndex({
      indexName: 'gsi_week',
      partitionKey: { name: 'weekStart', type: dynamodb.AttributeType.STRING },
    });

    // ContentDiff
    const contentDiff = new dynamodb.Table(this, 'ContentDiff', {
      tableName: `${prefix}-content-diff`,
      partitionKey: { name: 'chunkId', type: dynamodb.AttributeType.STRING },
      ...commonTableProps,
    });
    contentDiff.addGlobalSecondaryIndex({
      indexName: 'gsi_sourceUrl',
      partitionKey: { name: 'sourceUrl', type: dynamodb.AttributeType.STRING },
    });

    // ContactAnalysis
    const contactAnalysis = new dynamodb.Table(this, 'ContactAnalysis', {
      tableName: `${prefix}-contact-analysis`,
      partitionKey: { name: 'weekStart', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'contactId', type: dynamodb.AttributeType.STRING },
      ...commonTableProps,
    });

    const allTables = [
      vectorStore,
      customerHistory,
      improvementSuggestions,
      contentDiff,
      contactAnalysis,
    ];

    // -----------------------------------------------------------------------
    // 3. S3 bucket for crawled content
    // -----------------------------------------------------------------------
    const crawlBucket = new s3.Bucket(this, 'CrawlContent', {
      bucketName: `${prefix}-crawl-content-${account}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: cmk,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: dataRemovalPolicy,
      autoDeleteObjects: env === 'dev',
      lifecycleRules: [
        {
          noncurrentVersionExpiration: cdk.Duration.days(90),
          expiration: cdk.Duration.days(90),
        },
      ],
    });

    // -----------------------------------------------------------------------
    // 4. Secrets Manager: CRM API key placeholder
    // -----------------------------------------------------------------------
    const crmApiKey = new secretsmanager.Secret(this, 'CrmApiKey', {
      secretName: `${prefix}-crm-api-key`,
      description: `CRM API key placeholder for au Jibun Bank AI Agent (${env})`,
      encryptionKey: cmk,
      removalPolicy: env === 'dev' ? cdk.RemovalPolicy.DESTROY : cdk.RemovalPolicy.RETAIN,
      secretObjectValue: {
        apiKey: cdk.SecretValue.unsafePlainText('PLACEHOLDER_REPLACE_ME'),
      },
    });

    // -----------------------------------------------------------------------
    // 5. CloudWatch Logs group (90 day retention, CMK encrypted)
    // -----------------------------------------------------------------------
    // Allow CloudWatch Logs service to use the CMK in this region.
    cmk.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: 'AllowCloudWatchLogs',
        principals: [new iam.ServicePrincipal(`logs.${this.region}.amazonaws.com`)],
        actions: [
          'kms:Encrypt',
          'kms:Decrypt',
          'kms:ReEncrypt*',
          'kms:GenerateDataKey*',
          'kms:Describe*',
        ],
        resources: ['*'], // KMS key policy resources are scoped to this key by definition.
        conditions: {
          ArnLike: {
            'kms:EncryptionContext:aws:logs:arn': `arn:aws:logs:${this.region}:${account}:log-group:/aws/lambda/${prefix}`,
          },
        },
      }),
    );

    const appLogGroup = new logs.LogGroup(this, 'AppLogGroup', {
      logGroupName: `/aws/lambda/${prefix}`,
      retention: logs.RetentionDays.THREE_MONTHS, // 90 days
      encryptionKey: cmk,
      removalPolicy: dataRemovalPolicy,
    });

    // -----------------------------------------------------------------------
    // 6. CloudTrail (multi-region, file validation, CMK encrypted, own bucket)
    // -----------------------------------------------------------------------
    const trailBucket = new s3.Bucket(this, 'AuditTrailBucket', {
      bucketName: `${prefix}-cloudtrail-${account}`,
      encryption: s3.BucketEncryption.KMS,
      encryptionKey: cmk,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      enforceSSL: true,
      versioned: true,
      removalPolicy: dataRemovalPolicy,
      autoDeleteObjects: env === 'dev',
      lifecycleRules: [{ expiration: cdk.Duration.days(365) }],
    });

    const trail = new cloudtrail.Trail(this, 'AuditTrail', {
      trailName: `${prefix}-trail`,
      bucket: trailBucket,
      isMultiRegionTrail: true,
      enableFileValidation: true,
      encryptionKey: cmk,
      includeGlobalServiceEvents: true,
    });

    // -----------------------------------------------------------------------
    // 7. VPC (2 AZ, public + private subnets for future Lambda placement)
    // -----------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `${prefix}-vpc`,
      maxAzs: 2,
      natGateways: env === 'prod' ? 2 : 1, // cost-aware: single NAT outside prod
      subnetConfiguration: [
        {
          name: 'public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
        },
        {
          name: 'private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 24,
        },
      ],
    });

    // -----------------------------------------------------------------------
    // 8. IAM Permission Boundary (Lambda).
    //
    // ARN patterns (not Fn::GetAtt) are used here deliberately to avoid a
    // CloudFormation circular dependency:
    //   CrawlContent → SharedCmk (encryptionKey)
    //   SharedCmk    → SharedLambdaRole (key policy via grantEncryptDecrypt)
    //   SharedLambdaRole → LambdaPermBoundary (permissionsBoundary)
    //   LambdaPermBoundary → CrawlContent (if bucketArn was Fn::GetAtt)
    // Using literal ARN patterns breaks the cycle at LambdaPermBoundary.
    // -----------------------------------------------------------------------
    const permissionBoundary = new iam.ManagedPolicy(this, 'LambdaPermBoundary', {
      // No explicit managedPolicyName: CloudFormation generates one to avoid
      // AlreadyExists conflicts when redeploying after a failed rollback.
      description: 'Maximum permissions for au Jibun Bank Lambda execution roles',
      statements: [
        new iam.PolicyStatement({
          sid: 'DynamoDbAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'dynamodb:GetItem',
            'dynamodb:BatchGetItem',
            'dynamodb:Query',
            'dynamodb:PutItem',
            'dynamodb:BatchWriteItem',
            'dynamodb:UpdateItem',
            'dynamodb:DeleteItem',
            'dynamodb:ConditionCheckItem',
          ],
          resources: [
            `arn:aws:dynamodb:${this.region}:${account}:table/${prefix}-*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'S3Access',
          effect: iam.Effect.ALLOW,
          actions: ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:ListBucket'],
          resources: [
            `arn:aws:s3:::${prefix}-*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'LogsAccess',
          effect: iam.Effect.ALLOW,
          actions: ['logs:CreateLogStream', 'logs:PutLogEvents'],
          resources: [
            `arn:aws:logs:${this.region}:${account}:log-group:/aws/lambda/${prefix}*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'KmsAccess',
          effect: iam.Effect.ALLOW,
          actions: [
            'kms:Encrypt',
            'kms:Decrypt',
            'kms:ReEncrypt*',
            'kms:GenerateDataKey*',
            'kms:DescribeKey',
          ],
          // KMS key policies govern actual access; boundary uses account-scoped pattern.
          resources: [`arn:aws:kms:${this.region}:${account}:key/*`],
        }),
        new iam.PolicyStatement({
          sid: 'SecretsAccess',
          effect: iam.Effect.ALLOW,
          actions: ['secretsmanager:GetSecretValue', 'secretsmanager:DescribeSecret'],
          resources: [`arn:aws:secretsmanager:${this.region}:${account}:secret:${prefix}-*`],
        }),
        new iam.PolicyStatement({
          sid: 'BedrockInvoke',
          effect: iam.Effect.ALLOW,
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
          resources: [`arn:aws:bedrock:${this.region}::foundation-model/*`],
        }),
        new iam.PolicyStatement({
          sid: 'ComprehendDetect',
          effect: iam.Effect.ALLOW,
          actions: [
            'comprehend:DetectSentiment',
            'comprehend:DetectEntities',
            'comprehend:DetectKeyPhrases',
            'comprehend:DetectDominantLanguage',
          ],
          // Comprehend Detect* APIs do not support resource-level scoping.
          resources: ['*'],
        }),
        new iam.PolicyStatement({
          sid: 'SsmReadParameters',
          effect: iam.Effect.ALLOW,
          actions: ['ssm:GetParameter', 'ssm:GetParameters', 'ssm:GetParametersByPath'],
          resources: [
            `arn:aws:ssm:${this.region}:${account}:parameter/au-jibun-bank/${env}/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'VpcNetworkInterfaces',
          effect: iam.Effect.ALLOW,
          actions: [
            'ec2:CreateNetworkInterface',
            'ec2:DescribeNetworkInterfaces',
            'ec2:DeleteNetworkInterface',
            'ec2:AssignPrivateIpAddresses',
            'ec2:UnassignPrivateIpAddresses',
          ],
          resources: ['*'], // EC2 ENI actions for Lambda VPC access require "*".
        }),
      ],
    });
    permissionBoundary.applyRemovalPolicy(dataRemovalPolicy);

    // -----------------------------------------------------------------------
    // 9. Shared Lambda execution role (VPC access) + permission boundary
    // -----------------------------------------------------------------------
    const lambdaRole = new iam.Role(this, 'SharedLambdaRole', {
      roleName: `${prefix}-shared-lambda-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Shared Lambda execution role for au Jibun Bank AI Agent',
      permissionsBoundary: permissionBoundary,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName(
          'service-role/AWSLambdaVPCAccessExecutionRole',
        ),
      ],
    });
    lambdaRole.applyRemovalPolicy(dataRemovalPolicy);

    // Grant scoped data-plane access (also covered by the boundary above).
    allTables.forEach((t) => t.grantReadWriteData(lambdaRole));
    crawlBucket.grantReadWrite(lambdaRole);
    cmk.grantEncryptDecrypt(lambdaRole);
    crmApiKey.grantRead(lambdaRole);
    appLogGroup.grantWrite(lambdaRole);

    // -----------------------------------------------------------------------
    // 10. Amazon Connect instance (L1)
    // -----------------------------------------------------------------------
    const connectInstance = new connect.CfnInstance(this, 'ConnectInstance', {
      identityManagementType: 'CONNECT_MANAGED',
      // No explicit instanceAlias to avoid reuse-cooldown conflicts after rollback.
      // The instance ARN/ID are exported via SSM for downstream stacks.
      attributes: {
        inboundCalls: true,
        outboundCalls: false,
        contactflowLogs: true,
      },
    });

    // -----------------------------------------------------------------------
    // 11. Amazon Lex v2 bot shell (ja-JP) (L1)
    // -----------------------------------------------------------------------
    const lexServiceRole = new iam.Role(this, 'LexServiceRole', {
      roleName: `${prefix}-lex-service-role`,
      assumedBy: new iam.ServicePrincipal('lexv2.amazonaws.com'),
      description: 'Service role for the au Jibun Bank Lex v2 bot',
    });
    lexServiceRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['polly:SynthesizeSpeech', 'comprehend:DetectSentiment'],
        resources: ['*'], // Polly/Comprehend runtime actions are not resource-scoped.
      }),
    );

    const lexBot = new lex.CfnBot(this, 'LexBot', {
      name: `${prefix}-bot`,
      roleArn: lexServiceRole.roleArn,
      dataPrivacy: { ChildDirected: false },
      idleSessionTtlInSeconds: 300,
      botLocales: [
        {
          // TODO(prod): Lex v2 ja-JP is only available in us-east-1 (not ap-northeast-1).
          // For production, either deploy the Lex bot to us-east-1 or await regional expansion.
          // en_US is used here as a shell placeholder to pass CDK deploy in ap-northeast-1.
          localeId: 'en_US',
          nluConfidenceThreshold: 0.4,
          // Intents are defined in U-03; U-01 only provisions the shell with a
          // minimal fallback intent so the locale is valid.
          intents: [
            {
              name: 'FallbackIntent',
              parentIntentSignature: 'AMAZON.FallbackIntent',
            },
          ],
        },
      ],
    });

    // -----------------------------------------------------------------------
    // 12. SSM Parameter Store: export every ARN/ID (14 parameters)
    // -----------------------------------------------------------------------
    const base = `/au-jibun-bank/${env}`;
    const param = (scopeId: string, name: string, value: string): ssm.StringParameter =>
      new ssm.StringParameter(this, scopeId, { parameterName: name, stringValue: value });

    // DynamoDB table names (5)
    param('PVectorStoreName', `${base}/dynamodb/vector-store-table-name`, vectorStore.tableName);
    param(
      'PCustomerHistoryName',
      `${base}/dynamodb/customer-history-table-name`,
      customerHistory.tableName,
    );
    param(
      'PImprovementSuggestionsName',
      `${base}/dynamodb/improvement-suggestions-table-name`,
      improvementSuggestions.tableName,
    );
    param('PContentDiffName', `${base}/dynamodb/content-diff-table-name`, contentDiff.tableName);
    param(
      'PContactAnalysisName',
      `${base}/dynamodb/contact-analysis-table-name`,
      contactAnalysis.tableName,
    );

    // KMS (2)
    param('PCmkArn', `${base}/kms/cmk-arn`, cmk.keyArn);
    param('PCmkId', `${base}/kms/cmk-id`, cmk.keyId);

    // S3 (1)
    param('PCrawlBucketName', `${base}/s3/crawl-content-bucket-name`, crawlBucket.bucketName);

    // Secrets (1)
    param('PCrmApiKeyArn', `${base}/secrets/crm-api-key-arn`, crmApiKey.secretArn);

    // Connect (2)
    param('PConnectInstanceArn', `${base}/connect/instance-arn`, connectInstance.attrArn);
    param('PConnectInstanceId', `${base}/connect/instance-id`, connectInstance.attrId);

    // Lex (2)
    param('PLexBotId', `${base}/lex/bot-id`, lexBot.attrId);
    param('PLexBotAliasArn', `${base}/lex/bot-alias-arn`, lexBot.attrArn);

    // IAM (1)
    param(
      'PLambdaPermBoundaryArn',
      `${base}/iam/lambda-permission-boundary-arn`,
      permissionBoundary.managedPolicyArn,
    );

    // -----------------------------------------------------------------------
    // Tagging: every resource in the stack gets an Environment tag.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-01');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    // -----------------------------------------------------------------------
    // CloudFormation outputs (handy for manual inspection).
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'CmkArnOutput', { value: cmk.keyArn });
    new cdk.CfnOutput(this, 'CrawlBucketOutput', { value: crawlBucket.bucketName });
    new cdk.CfnOutput(this, 'VpcIdOutput', { value: vpc.vpcId });
    new cdk.CfnOutput(this, 'TrailArnOutput', { value: trail.trailArn });
  }
}
