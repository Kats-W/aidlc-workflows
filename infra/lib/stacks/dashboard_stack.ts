import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import * as amplify from 'aws-cdk-lib/aws-amplify';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Props for {@link DashboardStack}.
 */
export interface DashboardStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * DashboardStack (U-07) provisions the admin dashboard backend:
 *
 *   - Cognito UserPool (MFA OPTIONAL TOTP, strong password policy, advanced
 *     security) + SPA UserPoolClient (no secret) for reviewer sign-in (US-7.3).
 *   - MetricsAggregatorLambda (Python 3.12, 256 MB, 60s): aggregates contact /
 *     CSAT / escalation metrics from CustomerHistory (US-7.2).
 *   - DashboardApiLambda (Python 3.12, 256 MB, 30s): HTTP API backend for
 *     suggestion listing / status changes / CSV export / metrics (US-7.1, 7.2).
 *   - API Gateway HTTP API + Cognito JWT authorizer (L1 constructs) routing all
 *     dashboard endpoints to DashboardApiLambda.
 *   - EventBridge Scheduler: weekly Sunday 18:30 UTC pre-aggregation.
 *   - Amplify CfnApp (config only; CI/CD performs the real build/deploy).
 *   - CloudWatch error alarms + SSM outputs (pool id, client id, api endpoint,
 *     amplify app id).
 *
 * All resource names/ARNs come from SharedInfraStack SSM parameters; IAM is
 * least-privilege (no "*" actions) and bounded by the shared permission boundary.
 */
export class DashboardStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: DashboardStackProps) {
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

    const improvementSuggestionsTableName = p(
      `${base}/dynamodb/improvement-suggestions-table-name`,
    );
    const customerHistoryTableName = p(`${base}/dynamodb/customer-history-table-name`);
    const contactAnalysisTableName = p(`${base}/dynamodb/contact-analysis-table-name`);
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

    const improvementSuggestionsArn = `arn:aws:dynamodb:${this.region}:${account}:table/${improvementSuggestionsTableName}`;
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;

    const code = lambda.Code.fromAsset('..', {
      exclude: ['infra', 'tests', 'aidlc-docs', 'frontend', '.git', '.venv'],
    });

    // -----------------------------------------------------------------------
    // 2. Cognito UserPool + SPA client (US-7.3).
    // -----------------------------------------------------------------------
    const userPool = new cognito.UserPool(this, 'DashboardUserPool', {
      userPoolName: `${prefix}-dashboard`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      mfa: cognito.Mfa.OPTIONAL,
      mfaSecondFactor: { sms: false, otp: true },
      advancedSecurityMode: cognito.AdvancedSecurityMode.ENFORCED,
      passwordPolicy: {
        minLength: 8,
        requireUppercase: true,
        requireLowercase: true,
        requireDigits: true,
        requireSymbols: true,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const userPoolClient = userPool.addClient('DashboardSpaClient', {
      userPoolClientName: `${prefix}-dashboard-spa`,
      generateSecret: false,
      authFlows: { userSrp: true },
      idTokenValidity: cdk.Duration.minutes(60),
      accessTokenValidity: cdk.Duration.minutes(60),
      refreshTokenValidity: cdk.Duration.days(30),
      preventUserExistenceErrors: true,
    });

    // -----------------------------------------------------------------------
    // 3. MetricsAggregatorLambda (created first; the API Lambda invokes it).
    // -----------------------------------------------------------------------
    const aggregatorRole = this.makeLambdaRole(
      'MetricsAggregatorRole',
      `${prefix}-metrics-aggregator-role`,
      permissionBoundary,
    );

    const metricsAggregator = new lambda.Function(this, 'MetricsAggregatorLambda', {
      functionName: `${prefix}-metrics-aggregator`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.dashboard_api.metrics_aggregator.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      role: aggregatorRole,
      environment: {
        CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
        CONTACT_ANALYSIS_TABLE_NAME: contactAnalysisTableName,
        POWERTOOLS_SERVICE_NAME: 'u-07-dashboard',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    aggregatorRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistoryRead',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:Query', 'dynamodb:Scan', 'dynamodb:GetItem'],
        resources: [customerHistoryArn, `${customerHistoryArn}/index/*`],
      }),
    );
    cmk.grantEncryptDecrypt(aggregatorRole);

    // -----------------------------------------------------------------------
    // 4. DashboardApiLambda (HTTP API backend).
    // -----------------------------------------------------------------------
    const apiRole = this.makeLambdaRole(
      'DashboardApiRole',
      `${prefix}-dashboard-api-role`,
      permissionBoundary,
    );

    const dashboardApi = new lambda.Function(this, 'DashboardApiLambda', {
      functionName: `${prefix}-dashboard-api`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.dashboard_api.handler.lambda_handler',
      code,
      layers: [pythonDepsLayer],
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      role: apiRole,
      environment: {
        IMPROVEMENT_SUGGESTIONS_TABLE_NAME: improvementSuggestionsTableName,
        METRICS_AGGREGATOR_FUNCTION_NAME: metricsAggregator.functionName,
        POWERTOOLS_SERVICE_NAME: 'u-07-dashboard',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    apiRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'ImprovementSuggestionsReadWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:Query', 'dynamodb:GetItem', 'dynamodb:UpdateItem'],
        resources: [improvementSuggestionsArn, `${improvementSuggestionsArn}/index/*`],
      }),
    );
    apiRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'InvokeMetricsAggregator',
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [metricsAggregator.functionArn],
      }),
    );
    cmk.grantEncryptDecrypt(apiRole);

    // -----------------------------------------------------------------------
    // 5. HTTP API + Cognito JWT authorizer + Lambda proxy routes (L1).
    // -----------------------------------------------------------------------
    const httpApi = new apigwv2.CfnApi(this, 'DashboardHttpApi', {
      name: `${prefix}-dashboard-api`,
      protocolType: 'HTTP',
      corsConfiguration: {
        allowMethods: ['GET', 'PATCH', 'OPTIONS'],
        allowOrigins: ['*'],
        allowHeaders: ['authorization', 'content-type'],
      },
    });

    const issuer = `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`;
    const authorizer = new apigwv2.CfnAuthorizer(this, 'DashboardJwtAuthorizer', {
      apiId: httpApi.ref,
      authorizerType: 'JWT',
      name: `${prefix}-dashboard-jwt`,
      identitySource: ['$request.header.Authorization'],
      jwtConfiguration: {
        audience: [userPoolClient.userPoolClientId],
        issuer,
      },
    });

    // Allow API Gateway to invoke the backend Lambda.
    dashboardApi.addPermission('ApiGatewayInvoke', {
      principal: new iam.ServicePrincipal('apigateway.amazonaws.com'),
      action: 'lambda:InvokeFunction',
      sourceArn: `arn:aws:execute-api:${this.region}:${account}:${httpApi.ref}/*`,
    });

    const integration = new apigwv2.CfnIntegration(this, 'DashboardApiIntegration', {
      apiId: httpApi.ref,
      integrationType: 'AWS_PROXY',
      integrationUri: dashboardApi.functionArn,
      payloadFormatVersion: '2.0',
      integrationMethod: 'POST',
    });

    const routeKeys = [
      'GET /suggestions',
      'GET /suggestions/csv',
      'PATCH /suggestions/{suggestion_id}',
      'GET /metrics',
    ];
    routeKeys.forEach((routeKey, idx) => {
      new apigwv2.CfnRoute(this, `DashboardRoute${idx}`, {
        apiId: httpApi.ref,
        routeKey,
        target: `integrations/${integration.ref}`,
        authorizationType: 'JWT',
        authorizerId: authorizer.ref,
      });
    });

    const stage = new apigwv2.CfnStage(this, 'DashboardApiStage', {
      apiId: httpApi.ref,
      stageName: '$default',
      autoDeploy: true,
    });
    const apiEndpoint = `https://${httpApi.ref}.execute-api.${this.region}.amazonaws.com`;

    // -----------------------------------------------------------------------
    // 6. EventBridge Scheduler: weekly Sunday 18:30 UTC pre-aggregation.
    // -----------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'MetricsScheduleRole', {
      roleName: `${prefix}-metrics-schedule-role`,
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Allows EventBridge Scheduler to invoke the MetricsAggregatorLambda',
    });
    schedulerRole.addToPolicy(
      new iam.PolicyStatement({
        effect: iam.Effect.ALLOW,
        actions: ['lambda:InvokeFunction'],
        resources: [metricsAggregator.functionArn],
      }),
    );

    new scheduler.CfnSchedule(this, 'WeeklyMetricsSchedule', {
      name: `${prefix}-weekly-metrics`,
      description: 'Weekly au Jibun Bank dashboard metrics pre-aggregation',
      flexibleTimeWindow: { mode: 'OFF' },
      scheduleExpression: 'cron(30 18 ? * SUN *)',
      scheduleExpressionTimezone: 'UTC',
      target: {
        arn: metricsAggregator.functionArn,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({ period_days: 30 }),
        retryPolicy: { maximumRetryAttempts: 2 },
      },
    });

    // -----------------------------------------------------------------------
    // 7. Amplify app (config only; CI/CD performs the real build & deploy).
    // -----------------------------------------------------------------------
    const amplifyApp = new amplify.CfnApp(this, 'DashboardAmplifyApp', {
      name: `${prefix}-dashboard`,
      description: 'au Jibun Bank admin dashboard SPA (deployed via CI/CD)',
      platform: 'WEB',
      environmentVariables: [
        { name: 'VITE_USER_POOL_ID', value: userPool.userPoolId },
        { name: 'VITE_USER_POOL_CLIENT_ID', value: userPoolClient.userPoolClientId },
        { name: 'VITE_API_ENDPOINT', value: apiEndpoint },
      ],
    });

    // -----------------------------------------------------------------------
    // 8. CloudWatch error alarms.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'DashboardApiErrorAlarm', {
      alarmName: `${prefix}-dashboard-api-errors`,
      metric: dashboardApi.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    new cloudwatch.Alarm(this, 'MetricsAggregatorErrorAlarm', {
      alarmName: `${prefix}-metrics-aggregator-errors`,
      metric: metricsAggregator.metricErrors({ period: cdk.Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 9. Tags + SSM outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-07');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    const outputs: Record<string, string> = {
      'user-pool-id': userPool.userPoolId,
      'user-pool-client-id': userPoolClient.userPoolClientId,
      'api-endpoint': apiEndpoint,
      'amplify-app-id': amplifyApp.attrAppId,
    };
    Object.entries(outputs).forEach(([key, value]) => {
      new ssm.StringParameter(this, `Param-${key}`, {
        parameterName: `${base}/dashboard/${key}`,
        stringValue: value,
      });
    });

    // Ensure the stage exists before the endpoint is consumed downstream.
    stage.node.addDependency(integration);

    new cdk.CfnOutput(this, 'DashboardUserPoolId', { value: userPool.userPoolId });
    new cdk.CfnOutput(this, 'DashboardUserPoolClientId', {
      value: userPoolClient.userPoolClientId,
    });
    new cdk.CfnOutput(this, 'DashboardApiEndpoint', { value: apiEndpoint });
    new cdk.CfnOutput(this, 'DashboardAmplifyAppId', { value: amplifyApp.attrAppId });
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
