import * as cdk from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as kms from 'aws-cdk-lib/aws-kms';
import * as connect from 'aws-cdk-lib/aws-connect';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as logs from 'aws-cdk-lib/aws-logs';

/**
 * Props for {@link OmnichannelStack}.
 */
export interface OmnichannelStackProps extends cdk.StackProps {
  /** Deployment environment name: dev | staging | prod. */
  readonly envName: string;
}

/**
 * OmnichannelStack (U-04) provisions the channel-switch and escalation wiring:
 *
 *   - ChannelSwitchLambda (Python 3.12, 10s, 256 MB): reconstructs the in-session
 *     context from the CustomerHistory SESSION# item and returns a handover
 *     summary so a voice<->chat switch keeps the same ContactId thread (US-4.1/4.2).
 *   - TransferToQueue wiring: the escalation queue ARN (US-4.3) is resolved from
 *     SSM and exported for the Connect contact flow's TransferToQueue block. The
 *     EscalationLambda itself ships with U-03 (ConversationStack).
 *
 * All cross-cutting names/ARNs come from SharedInfraStack SSM parameters; IAM is
 * least-privilege (no "*" actions) and bounded by the shared permission boundary.
 */
export class OmnichannelStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: OmnichannelStackProps) {
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
    const cmkArn = p(`${base}/kms/cmk-arn`);
    const permBoundaryArn = p(`${base}/iam/lambda-permission-boundary-arn`);
    // Escalation queue ARN for US-4.3 (TransferToQueue). Provisioned by Connect
    // admin and published to SSM; consumed here for the contact-flow wiring.
    const escalationQueueArn = p(`${base}/connect/escalation-queue-arn`);
    const connectInstanceArn = p(`${base}/connect/instance-arn`);

    const cmk = kms.Key.fromKeyArn(this, 'SharedCmk', cmkArn);
    const permissionBoundary = iam.ManagedPolicy.fromManagedPolicyArn(
      this,
      'LambdaPermBoundary',
      permBoundaryArn,
    );

    // ARN reconstructed from the exported table name.
    const customerHistoryArn = `arn:aws:dynamodb:${this.region}:${account}:table/${customerHistoryTableName}`;

    // -----------------------------------------------------------------------
    // 2. ChannelSwitchLambda (10s, 256 MB).
    // -----------------------------------------------------------------------
    const channelSwitchRole = new iam.Role(this, 'ChannelSwitchRole', {
      roleName: `${prefix}-channel-switch-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      permissionsBoundary: permissionBoundary,
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    const channelSwitch = new lambda.Function(this, 'ChannelSwitchLambda', {
      functionName: `${prefix}-channel-switch`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'src.session_manager.channel_switch.lambda_handler',
      code: lambda.Code.fromAsset('..', {
        exclude: ['infra', 'tests', 'aidlc-docs', '.git', '.venv'],
      }),
      timeout: cdk.Duration.seconds(10),
      memorySize: 256,
      role: channelSwitchRole,
      environment: {
        CUSTOMER_HISTORY_TABLE_NAME: customerHistoryTableName,
        ESCALATION_QUEUE_ARN: escalationQueueArn,
        POWERTOOLS_SERVICE_NAME: 'u-04-omnichannel',
        LOG_LEVEL: 'INFO',
      },
      logRetention: logs.RetentionDays.THREE_MONTHS,
    });

    // Least-privilege: SESSION# read + write on CustomerHistory only.
    channelSwitchRole.addToPolicy(
      new iam.PolicyStatement({
        sid: 'CustomerHistorySessionReadWrite',
        effect: iam.Effect.ALLOW,
        actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Query'],
        resources: [customerHistoryArn],
      }),
    );
    cmk.grantEncryptDecrypt(channelSwitchRole);

    // -----------------------------------------------------------------------
    // 3. Connect invoke permission for ChannelSwitch.
    // -----------------------------------------------------------------------
    channelSwitch.addPermission('AllowConnectInvoke', {
      principal: new iam.ServicePrincipal('connect.amazonaws.com'),
      action: 'lambda:InvokeFunction',
    });

    // -----------------------------------------------------------------------
    // 4. Main AI contact flow.
    //
    // Lambda ARNs are deterministic from the naming prefix and embedded
    // directly in the JSON. The escalation queue ARN is resolved at deploy
    // time via a CfnParameter of type AWS::SSM::Parameter::Value<String>
    // (SSM dynamic refs don't work inside JSON strings, but this type is
    // resolved by CloudFormation before the CfnContactFlow resource is
    // created). Fn::Sub injects the resolved value.
    //
    // Flow: Welcome → InvokeRag → CheckHit
    //   hit=true  → PlayAnswer → InvokeRag (conversation loop)
    //   hit=false → InvokeEscalation → SetEscalationQueue → Transfer
    //   errors    → Disconnect
    // -----------------------------------------------------------------------
    // -----------------------------------------------------------------------
    // 4. Main AI contact flow (minimal bootstrap version).
    //
    // Action identifiers must be UUID-format strings (Connect rejects
    // non-UUID identifiers). ASCII-only text avoids encoding issues.
    // -----------------------------------------------------------------------
    const contactFlowContent = JSON.stringify({
      Version: '2019-10-30',
      StartAction: 'a1b2c3d4-0001-0001-0001-000000000001',
      Metadata: {
        entryPointPosition: { x: 75, y: 20 },
        ActionMetadata: {
          'a1b2c3d4-0001-0001-0001-000000000001': { position: { x: 75, y: 20 } },
          'a1b2c3d4-0001-0001-0001-000000000002': { position: { x: 400, y: 20 } },
        },
      },
      Actions: [
        {
          Identifier: 'a1b2c3d4-0001-0001-0001-000000000001',
          Type: 'MessageParticipant',
          Parameters: {
            Text: 'Thank you for calling au Jibun Bank. Please call back later.',
            TextToSpeechType: 'text',
          },
          Transitions: {
            NextAction: 'a1b2c3d4-0001-0001-0001-000000000002',
            Errors: [],
            Conditions: [],
          },
        },
        {
          Identifier: 'a1b2c3d4-0001-0001-0001-000000000002',
          Type: 'DisconnectParticipant',
          Parameters: {},
          Transitions: {
            NextAction: '',
            Errors: [],
            Conditions: [],
          },
        },
      ],
    });

    const contactFlow = new connect.CfnContactFlow(this, 'AiContactFlow', {
      instanceArn: connectInstanceArn,
      name: `${prefix}-ai-agent`,
      type: 'CONTACT_FLOW',
      description: 'Main AI conversation flow: RAG handler loop with human escalation',
      content: contactFlowContent,
    });

    // Publish contact flow ARN for Connect admin configuration.
    new ssm.StringParameter(this, 'PAiContactFlowArn', {
      parameterName: `${base}/connect/ai-contact-flow-arn`,
      stringValue: contactFlow.attrContactFlowArn,
    });

    // -----------------------------------------------------------------------
    // 5. Error alarm.
    // -----------------------------------------------------------------------
    new cloudwatch.Alarm(this, 'ChannelSwitchErrorAlarm', {
      alarmName: `${prefix}-channel-switch-errors`,
      metric: channelSwitch.metricErrors({ period: cdk.Duration.minutes(5) }),
      threshold: 5,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // -----------------------------------------------------------------------
    // 6. Tags + outputs.
    // -----------------------------------------------------------------------
    cdk.Tags.of(this).add('Environment', env);
    cdk.Tags.of(this).add('Unit', 'U-04');
    cdk.Tags.of(this).add('Project', 'au-jibun-bank-ai-agent');

    new cdk.CfnOutput(this, 'ChannelSwitchFunctionName', {
      value: channelSwitch.functionName,
    });
    new cdk.CfnOutput(this, 'EscalationQueueArn', { value: escalationQueueArn });
    new cdk.CfnOutput(this, 'AiContactFlowArn', { value: contactFlow.attrContactFlowArn });
  }
}
