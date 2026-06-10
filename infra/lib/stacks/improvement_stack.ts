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
 * Props for {@link ImprovementStack}.
 */
export interface ImprovementStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * ImprovementStack (U-06) provisions the weekly Self-Improvement Pipeline:
 *
 *   - ContactLensAnalyzerLambda (Python 3.12, 512 MB, 300s): EventBridge-driven
 *     weekly detection of low-quality contacts from Amazon Connect Contact Lens
 *     (US-3.1). Persists to ContactAnalysis, then sync-invokes the gap analyzer.
 *   - GapAnalyzerLambda (Python 3.12, 256 MB, 120s): classifies knowledge gaps
 *     from PII-masked summaries via Claude (US-3.2), then invokes the generator.
 *   - SuggestionGeneratorLambda (Python 3.12, 256 MB, 120s): writes up to 10
 *     prioritised improvement suggestions, skipping pending duplicates (US-3.3).
 *   - EventBridge Scheduler: weekly Sunday 18:00 UTC (= Monday 03:00 JST).
 *   - CloudWatch error alarms for all three functions.
 *
 * The three Lambdas are chained with direct Lambda-to-Lambda async invokes (no
 * Step Functions). All resource names/ARNs come from SharedInfraStack SSM
 * parameters; IAM is least-privilege (no "*" actions) and bounded by the shared
 * permission boundary.
 */
export class ImprovementStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ImprovementStackProps) {
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

    const contactAnalysisTableName = p(`${base}/dynamodb/contact-analysis-table-name`);
    const improvementSuggestionsTableName = p(
      `${base}/dynamodb/improvement-suggestions-table-name`,
    );
    const customerHistoryTableName = p(`${base}/dynamodb/customer-history-table-name`);
    const cmkArn = p(`${base}/kms/cmk-arn`);
    const permBoundaryArn = p(`${base}/iam/lambda-permission-boundary-arn`);
    const connectInstanceId = p(`${base}/connect/instance-id`);
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

    // ARNs reconstructed from names (SSM exports names for tables).
    const contactAnalysisArn = `arn:aws:dynamodb:${this.region}:${account}:table/${contactAnalysisTableName}`;
    const improvementSuggestionsArn = `arn:aws:dynamodb:${this.region}:${account}:table/${improvementSuggestionsTableName}`;
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;
    const connectInstanceArn = `arn:aws:connect:${this.region}:${account}:instance/${connectInstanceId}`;
    const sonnetModelArn = `arn:aws:bedrock:${this.region}::foundation-model/anthropic.claude-sonnet-4-6`;
    const sonnetInferenceProfileArn = `arn:aws:bedrock:${this.region}:${account}:inference-profile/jp.anthropic.claude-sonnet-4-6`;

    const code = lambda.Code.fromAsset('..', {
      exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
    });

    const commonEnv: Record<string, string> = {
      CONTACT_ANALYSIS_TABLE_NAME: contactAnalysisTableName,
      IMPROVEMENT_SUGGESTIONS_TABLE_NAME: improvementSuggestionsTableName,
      CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
      POWERTOOLS_SERVICE_NAME: 'u-06-improvement',
      LOG_LEVEL: 'INFO',
    };

    // -----------------------------------------------------------------------
    // 2. SuggestionGeneratorLambda (created first; the gap analyzer invokes it).
    // -----------------------------------------------------------------------
    const suggestionRole = this.makeLambdaRole(
      'SuggestionGeneratorRole',
      `${prefix}-suggestion-generator-role`,
      permissionBoundary,
    );

    const suggestionGenerator = new lambda.Function(this, 'SuggestionGeneratorLambda', {
      functionName: `${prefix}-suggestion-generator`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.improvement_generator.suggestion_generator.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(120),
      memorySize: 256,
      role: suggestionRole,
      environment: commonEnv,
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    suggestionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ImprovementSuggestionsReadWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:PutItem', 'dynamodb:Query', 'dynamodb:GetItem'],
        resources: [improvementSuggestionsArn, `${improvementSuggestionsArn}/index/*`],
      }),
    );
    suggestionRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockSuggestion',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:InvokeModel'],
        resources: [sonnetModelArn, sonnetInferenceProfileArn],
      }),
    );
    cmk.grantEncryptDecrypt(suggestionRole);

    // -----------------------------------------------------------------------
    // 3. GapAnalyzerLambda (invokes the suggestion generator).
    // -----------------------------------------------------------------------
    const gapRole = this.makeLambdaRole(
      'GapAnalyzerRole',
      `${prefix}-gap-analyzer-role`,
      permissionBoundary,
    );

    const gapAnalyzer = new lambda.Function(this, 'GapAnalyzerLambda', {
      functionName: `${prefix}-gap-analyzer`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.improvement_generator.gap_analyzer.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(120),
      memorySize: 256,
      role: gapRole,
      environment: {
        ...commonEnv,
        SUGGESTION_GENERATOR_FUNCTION_NAME: suggestionGenerator.functionName,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    gapRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ContactAnalysisRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:Query'],
        resources: [contactAnalysisArn],
      }),
    );
    gapRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistorySummaryRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:Query', 'dynamodb:GetItem'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    gapRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'BedrockGapAnalysis',
        effect: iam.Effect.ALLOW,
        actions: ['bedrock:InvokeModel'],
        resources: [sonnetModelArn, sonnetInferenceProfileArn],
      }),
    );
    gapRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeSuggestionGenerator',
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [suggestionGenerator.functionArn],
      }),
    );
    cmk.grantEncryptDecrypt(gapRole);

    // -----------------------------------------------------------------------
    // 4. ContactLensAnalyzerLambda (EventBridge-triggered; invokes gap analyzer).
    // -----------------------------------------------------------------------
    const contactLensRole = this.makeLambdaRole(
      'ContactLensAnalyzerRole',
      `${prefix}-contact-lens-analyzer-role`,
      permissionBoundary,
    );

    const contactLensAnalyzer = new lambda.Function(this, 'ContactLensAnalyzerLambda', {
      functionName: `${prefix}-contact-lens-analyzer`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.improvement_generator.contact_lens_analyzer.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(300),
      memorySize: 512,
      role: contactLensRole,
      environment: {
        ...commonEnv,
        CONNECT_INSTANCE_ID: connectInstanceId,
        GAP_ANALYZER_FUNCTION_NAME: gapAnalyzer.functionName,
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    contactLensRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ContactAnalysisWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:PutItem', 'dynamodb:BatchWriteItem'],
        resources: [contactAnalysisArn],
      }),
    );
    contactLensRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ConnectContactLensRead',
        effect: iam.Effect.ALLOW,
        actions: [
          'connect:SearchContacts',
          'connect:ListContactAnalysis',
          'connect:GetContactAttributes',
        ],
        resources: [connectInstanceArn, `${connectInstanceArn}/contact/*`],
      }),
    );
    contactLensRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeGapAnalyzer',
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [gapAnalyzer.functionArn],
      }),
    );
    cmk.grantEncryptDecrypt(contactLensRole);

    // -----------------------------------------------------------------------
    // 5. EventBridge Scheduler: weekly Sunday 18:00 UTC (= Monday 03:00 JST).
    // -----------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'ImprovementScheduleRole', {
      roleName: `${prefix}-improvement-schedule-role`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Allows EventBridge Scheduler to invoke the ContactLensAnalyzerLambda',
    });
    schedulerRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [contactLensAnalyzer.functionArn],
      }),
    );

    new scheduler.CfnSchedule(this, 'WeeklyImprovementSchedule', {
      name: `${prefix}-weekly-improvement`,
      description: 'Weekly au Jibun Bank self-improvement pipeline (Mon 03:00 JST)',
      flexibleTimeWindow: { mode: 'OFF' },
      // cron(min hour day month day-of-week year); 18:00 UTC Sunday == 03:00 JST Monday.
      scheduleExpression: 'cron(0 18 ? * SUN *)',
      scheduleExpressionTimezone: 'UTC',
      target: {
        arn: contactLensAnalyzer.functionArn,
        roleArn: schedulerRole.roleArn,
        retryPolicy: { maximumRetryAttempts: 2 },
      },
    });

    // -----------------------------------------------------------------------
    // 6. CloudWatch error-rate alarms for all three functions.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'ContactLensAnalyzerErrorAlarm', {
      alarmName: `${prefix}-contact-lens-analyzer-errors`,
      metric: contactLensAnalyzer.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'GapAnalyzerErrorAlarm', {
      alarmName: `${prefix}-gap-analyzer-errors`,
      metric: gapAnalyzer.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'SuggestionGeneratorErrorAlarm', {
      alarmName: `${prefix}-suggestion-generator-errors`,
      metric: suggestionGenerator.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 7. Tags + outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-06');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    new cdk.CfnOutput(this, 'ContactLensAnalyzerFunctionName', {
      value: contactLensAnalyzer.functionName,
    });
    new cdk.CfnOutput(this, 'GapAnalyzerFunctionName', { value: gapAnalyzer.functionName });
    new cdk.CfnOutput(this, 'SuggestionGeneratorFunctionName', {
      value: suggestionGenerator.functionName,
    });
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
