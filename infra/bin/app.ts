#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { SharedInfraStack } from '../lib/stacks/shared_infra_stack';
import { KnowledgePipelineStack } from '../lib/stacks/knowledge_pipeline_stack';
import { ConversationStack } from '../lib/stacks/conversation_stack';
import { OmnichannelStack } from '../lib/stacks/omnichannel_stack';
import { ProfileStack } from '../lib/stacks/profile_stack';

const app = new cdk.App();

// Environment selection: dev | staging | prod (CDK context -> default dev).
const env: string = app.node.tryGetContext('env') ?? 'dev';

// au Jibun Bank AI Agent is deployed to ap-northeast-1 (Tokyo).
const cdkEnv: cdk.Environment = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: 'ap-northeast-1',
};

new SharedInfraStack(app, `AuJibunBank-${env}-SharedInfra`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Shared core infrastructure (${env})`,
});

// U-02 Knowledge Pipeline: weekly crawl + diff + Titan v2 embedding.
new KnowledgePipelineStack(app, `AuJibunBank-${env}-KnowledgePipeline`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Knowledge Pipeline (U-02) (${env})`,
  targetUrls: [
    'https://www.jibunbank.co.jp/',
    'https://www.jibunbank.co.jp/faq/',
  ],
});

// U-03 Conversation Engine: Connect RAG hook, personalizer, escalation, CSAT.
new ConversationStack(app, `AuJibunBank-${env}-Conversation`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Conversation Engine (U-03) (${env})`,
});

// U-04 Omnichannel & Escalation: voice<->chat handover + escalation queue wiring.
new OmnichannelStack(app, `AuJibunBank-${env}-Omnichannel`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Omnichannel & Escalation (U-04) (${env})`,
});

// U-05 SDK & Customer Profile: au ID hash -> customerId attribution + async
// CRM conversation-summary write-back (SQS + DLQ).
new ProfileStack(app, `AuJibunBank-${env}-Profile`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — SDK & Customer Profile (U-05) (${env})`,
  crmEndpoint: app.node.tryGetContext('crmEndpoint') ?? 'https://crm.jibunbank.example/api/v1/summaries',
});

// ---------------------------------------------------------------------------
// Placeholders for the remaining follow-on unit stacks. These are intentionally
// commented out; each unit will instantiate its own stack and consume the
// SharedInfraStack exports via SSM Parameter Store.
//
//   new ImprovementStack(app, `AuJibunBank-${env}-Improvement`, { ... });                      // U-06
//   new OpsStack(app, `AuJibunBank-${env}-Ops`, { ... });                                      // U-07 (ops/dashboards)
// ---------------------------------------------------------------------------

cdk.Tags.of(app).add('Project', 'au-jibun-bank-ai-agent');
cdk.Tags.of(app).add('Environment', env);

app.synth();
