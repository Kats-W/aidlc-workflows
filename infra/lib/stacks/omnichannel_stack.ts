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
    // 4. Main AI contact flow.
    //
    // JSON schema confirmed from exported sample flow:
    //   - MessageParticipant params: SkipWhenDTMFBufferEnabled + Text
    //   - MessageParticipant transitions: {NextAction} only (no Errors/Conditions)
    //   - DisconnectParticipant transitions: {} (empty)
    //   - Attribute checking: Compare (not CheckAttribute)
    //   - Condition nesting: {NextAction, Condition:{Operator, Operands}}
    //
    // Escalation queue ARN injected via CfnParameter + Fn::Sub at deploy time
    // (SSM dynamic refs don't resolve inside JSON strings).
    // -----------------------------------------------------------------------
    const escalationQueueArnForFlow = new cdk.CfnParameter(this, 'EscalationQueueArnForFlow', {
      type: 'AWS::SSM::Parameter::Value<String>',
      default: `${base}/connect/escalation-queue-arn`,
      description: 'Escalation queue ARN resolved from SSM for the contact flow',
    });

    const lexBotAliasArnForFlow = new cdk.CfnParameter(this, 'LexBotAliasArnForFlow', {
      type: 'AWS::SSM::Parameter::Value<String>',
      default: `${base}/lex/bot-alias-arn`,
      description: 'Lex bot alias ARN resolved from SSM for the contact flow GetParticipantInput block',
    });

    const ragHandlerArn = `arn:aws:lambda:${this.region}:${account}:function:${prefix}-rag-handler`;
    const escalationLambdaArn = `arn:aws:lambda:${this.region}:${account}:function:${prefix}-escalation`;

    // Flow: SetVoice(009) → Welcome(001) → GetParticipantInput/Lex(010) → RAG(002) →
    //   hit=true  → PlayAnswer(004) → GetParticipantInput(010) [conversation loop]
    //   hit=false → Escalation(005) → SetQueue(006) → Transfer(007) → Disconnect(008)
    //   errors    → Escalation(005) or Disconnect(008)
    const contactFlowTemplate = JSON.stringify({
      Version: '2019-10-30',
      StartAction: 'aab00001-0000-0000-0000-000000000009',
      Metadata: {
        entryPointPosition: { x: 14.4, y: 14.4 },
        Annotations: [],
        ActionMetadata: {
          'aab00001-0000-0000-0000-000000000009': { position: { x: 75, y: 20 } },
          'aab00001-0000-0000-0000-000000000001': { position: { x: 300, y: 20 } },
          'aab00001-0000-0000-0000-000000000010': { position: { x: 530, y: 20 } },
          'aab00001-0000-0000-0000-000000000002': { position: { x: 760, y: 20 } },
          'aab00001-0000-0000-0000-000000000003': { position: { x: 990, y: 20 } },
          'aab00001-0000-0000-0000-000000000004': { position: { x: 1220, y: 20 } },
          'aab00001-0000-0000-0000-000000000005': { position: { x: 760, y: 200 } },
          'aab00001-0000-0000-0000-000000000006': { position: { x: 990, y: 200 } },
          'aab00001-0000-0000-0000-000000000007': { position: { x: 1220, y: 200 } },
          'aab00001-0000-0000-0000-000000000008': { position: { x: 1220, y: 380 } },
        },
      },
      Actions: [
        {
          // 009: Set Japanese Polly voice before any TTS is played.
          Identifier: 'aab00001-0000-0000-0000-000000000009',
          Type: 'UpdateContactTextToSpeechVoice',
          Parameters: { VoiceId: 'Kazuha' },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000001',
          },
        },
        {
          // 001: Welcome message (plays once at call start).
          Identifier: 'aab00001-0000-0000-0000-000000000001',
          Type: 'MessageParticipant',
          Parameters: {
            SkipWhenDTMFBufferEnabled: 'false',
            Text: 'auじぶん銀行AIアシスタントです。ご質問をどうぞ。',
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000010',
          },
        },
        {
          // 010: Collect customer voice via Lex ASR. $.Lex.InputTranscript carries
          // the full utterance regardless of intent, passed to RAG as userInput.
          Identifier: 'aab00001-0000-0000-0000-000000000010',
          Type: 'GetParticipantInput',
          Parameters: {
            Text: 'ご質問をどうぞ。',
            LexV2Bot: { AliasArn: '${LexBotAliasArn}' },
            LexSessionAttributes: {},
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000002',
            Conditions: [],
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000005', ErrorType: 'InputTimeLimitExceeded' },
              { NextAction: 'aab00001-0000-0000-0000-000000000005', ErrorType: 'NoMatchingError' },
            ],
          },
        },
        {
          // 002: RAG Lambda — receives the Lex transcript as userInput.
          Identifier: 'aab00001-0000-0000-0000-000000000002',
          Type: 'InvokeLambdaFunction',
          Parameters: {
            LambdaFunctionARN: ragHandlerArn,
            InvocationTimeLimitSeconds: '8',
            LambdaInvocationAttributes: {
              userInput: '$.Lex.InputTranscript',
            },
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000003',
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000005', ErrorType: 'NoMatchingError' },
            ],
            Conditions: [],
          },
        },
        {
          // 003: Check RAG hit flag.
          Identifier: 'aab00001-0000-0000-0000-000000000003',
          Type: 'Compare',
          Parameters: {
            ComparisonValue: '$.External.hit',
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000005',
            Conditions: [
              {
                NextAction: 'aab00001-0000-0000-0000-000000000004',
                Condition: { Operator: 'Equals', Operands: ['true'] },
              },
            ],
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000005', ErrorType: 'NoMatchingCondition' },
            ],
          },
        },
        {
          // 004: Read RAG answer aloud, then loop back to collect next question.
          Identifier: 'aab00001-0000-0000-0000-000000000004',
          Type: 'MessageParticipant',
          Parameters: {
            SkipWhenDTMFBufferEnabled: 'false',
            Text: '$.External.response_text',
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000010',
          },
        },
        {
          Identifier: 'aab00001-0000-0000-0000-000000000005',
          Type: 'InvokeLambdaFunction',
          Parameters: {
            LambdaFunctionARN: escalationLambdaArn,
            InvocationTimeLimitSeconds: '8',
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000006',
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000008', ErrorType: 'NoMatchingError' },
            ],
            Conditions: [],
          },
        },
        {
          Identifier: 'aab00001-0000-0000-0000-000000000006',
          Type: 'UpdateContactTargetQueue',
          Parameters: {
            QueueId: '${EscalationQueueArn}',
          },
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000007',
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000008', ErrorType: 'NoMatchingError' },
            ],
            Conditions: [],
          },
        },
        {
          Identifier: 'aab00001-0000-0000-0000-000000000007',
          Type: 'TransferContactToQueue',
          Parameters: {},
          Transitions: {
            NextAction: 'aab00001-0000-0000-0000-000000000008',
            Errors: [
              { NextAction: 'aab00001-0000-0000-0000-000000000008', ErrorType: 'NoMatchingError' },
              { NextAction: 'aab00001-0000-0000-0000-000000000008', ErrorType: 'QueueAtCapacity' },
            ],
            Conditions: [],
          },
        },
        {
          Identifier: 'aab00001-0000-0000-0000-000000000008',
          Type: 'DisconnectParticipant',
          Parameters: {},
          Transitions: {},
        },
      ],
    });

    const contactFlowContent = cdk.Fn.sub(contactFlowTemplate, {
      EscalationQueueArn: escalationQueueArnForFlow.valueAsString,
      LexBotAliasArn: lexBotAliasArnForFlow.valueAsString,
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
