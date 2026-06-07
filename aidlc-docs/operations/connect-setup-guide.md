# Amazon Connect Setup Guide

Post-CDK steps required to complete the au Jibun Bank AI Agent contact flow setup.

## What CDK provisions automatically

| Resource | Stack | Name |
|---|---|---|
| Connect instance | SharedInfraStack | `au-jibun-bank-{env}` |
| Hours of operation (24/7 JST) | SharedInfraStack | `au-jibun-bank-{env}-24x7` |
| Escalation queue | SharedInfraStack | `au-jibun-bank-{env}-escalation` |
| AI contact flow | OmnichannelStack | `au-jibun-bank-{env}-ai-agent` |
| Lambda invoke permissions | ConversationStack | (resource policies on each function) |

All ARNs are published to SSM Parameter Store under `/au-jibun-bank/{env}/`.

---

## Manual steps (Connect admin console)

### 1. Claim a phone number

1. Open the Connect instance in the AWS console → **Phone numbers → Manage**.
2. Claim a DID (direct inward dial) or toll-free number in the `ap-northeast-1` region.
3. Assign the number to the **`au-jibun-bank-{env}-ai-agent`** contact flow.

### 2. Create a routing profile

1. In Connect → **Routing → Routing profiles → Add routing profile**.
2. Name: `au-jibun-bank-{env}-default`.
3. Add the **`au-jibun-bank-{env}-escalation`** queue with priority 1.
4. Assign this profile to the agent(s) who will handle escalated calls.

### 3. Associate Lambda functions

Connect must be granted access to each Lambda before the contact flow can invoke them.

In the Connect console → **Contact flows → AWS Lambda**, add each function:

- `au-jibun-bank-{env}-rag-handler`
- `au-jibun-bank-{env}-escalation`
- `au-jibun-bank-{env}-personalizer`
- `au-jibun-bank-{env}-csat-handler`
- `au-jibun-bank-{env}-channel-switch`

> The CDK already adds resource-based policies (`lambda:InvokeFunction` for
> `connect.amazonaws.com`). The console step registers the functions in the
> Connect instance's allowed list, which is a separate control.

### 4. Associate the Lex bot (future — when Lex ja-JP is available in ap-northeast-1)

The current contact flow does not include a Lex `GetCustomerInput` block because
Lex v2 Japanese locale (`ja-JP`) is not yet available in `ap-northeast-1`. When it
becomes available:

1. Update the Lex bot locale in SharedInfraStack from `en_US` to `ja-JP`.
2. Create a `CfnBotAlias` resource and export its ARN to SSM.
3. Insert a `GetCustomerInput` block (with the Lex bot alias ARN) into the
   OmnichannelStack contact flow, before each `InvokeRag` invocation.
4. Update the RAG Lambda to read the customer's utterance from
   `$.Lex.InputTranscript` in the Connect event.

### 5. CSAT post-contact survey

To collect CSAT scores after each call:

1. Create a separate **Post-call survey** contact flow that invokes
   `au-jibun-bank-{env}-csat-handler`.
2. In the main `au-jibun-bank-{env}-ai-agent` flow, add a
   **Set disconnect flow** block pointing to the survey flow.

---

## Verifying the deployment

```bash
# Get the contact flow ARN
aws ssm get-parameter \
  --name /au-jibun-bank/dev/connect/ai-contact-flow-arn \
  --query Parameter.Value --output text

# Confirm Lambda functions are accessible from Connect
aws connect list-lambda-functions \
  --instance-id $(aws ssm get-parameter \
    --name /au-jibun-bank/dev/connect/instance-id \
    --query Parameter.Value --output text) \
  --query 'LambdaFunctions'
```

## End-to-end test checklist

- [ ] Call the claimed phone number → hear welcome message in Japanese
- [ ] Speak a question → RAG Lambda invoked → answer spoken back
- [ ] Speak out-of-scope query → escalation Lambda invoked → transfer to queue
- [ ] Agent receives escalated call with session summary in contact attributes
- [ ] CSAT survey plays after disconnect
- [ ] CloudWatch alarms remain in OK state for 5 minutes of testing
