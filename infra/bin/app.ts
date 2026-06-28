#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { SharedInfraStack } from '../lib/stacks/shared_infra_stack';
import { KnowledgePipelineStack } from '../lib/stacks/knowledge_pipeline_stack';
import { ConversationStack } from '../lib/stacks/conversation_stack';
import { OmnichannelStack } from '../lib/stacks/omnichannel_stack';
import { ProfileStack } from '../lib/stacks/profile_stack';
import { ImprovementStack } from '../lib/stacks/improvement_stack';
import { DashboardStack } from '../lib/stacks/dashboard_stack';
import { ChatStack } from '../lib/stacks/chat_stack';

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
    'https://help.jibunbank.co.jp/',
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

// U-06 Self-Improvement Pipeline: weekly Contact Lens low-quality detection +
// knowledge-gap analysis + prioritised improvement suggestion generation.
new ImprovementStack(app, `AuJibunBank-${env}-Improvement`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Self-Improvement Pipeline (U-06) (${env})`,
});

// U-07 Admin Dashboard: Cognito-protected HTTP API + React SPA for reviewing
// weekly improvement suggestions and viewing usage metrics.
new DashboardStack(app, `AuJibunBank-${env}-Dashboard`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Admin Dashboard (U-07) (${env})`,
});

// U-08 Web Chat API: customer-facing streaming RAG chat (FastAPI + Lambda Web
// Adapter on a Function URL) — the web counterpart to the Connect voice path.
new ChatStack(app, `AuJibunBank-${env}-Chat`, {
  env: cdkEnv,
  envName: env,
  description: `au Jibun Bank AI Agent — Web Chat API (U-08) (${env})`,
});

cdk.Tags.of(app).add('Project', 'au-jibun-bank-ai-agent');
cdk.Tags.of(app).add('Environment', env);

app.synth();
