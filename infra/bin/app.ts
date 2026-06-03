#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { SharedInfraStack } from '../lib/stacks/shared_infra_stack';
import { KnowledgePipelineStack } from '../lib/stacks/knowledge_pipeline_stack';

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

// ---------------------------------------------------------------------------
// Placeholders for the remaining follow-on unit stacks. These are intentionally
// commented out; each unit will instantiate its own stack and consume the
// SharedInfraStack exports via SSM Parameter Store.
//
//   new ChatStack(app, `AuJibunBank-${env}-Chat`, { env: cdkEnv, envName: env });              // U-03
//   new ContactAnalysisStack(app, `AuJibunBank-${env}-ContactAnalysis`, { ... });              // U-04
//   new CrmIntegrationStack(app, `AuJibunBank-${env}-CrmIntegration`, { ... });                // U-05
//   new ImprovementStack(app, `AuJibunBank-${env}-Improvement`, { ... });                      // U-06
//   new OpsStack(app, `AuJibunBank-${env}-Ops`, { ... });                                      // U-07 (ops/dashboards)
// ---------------------------------------------------------------------------

cdk.Tags.of(app).add('Project', 'au-jibun-bank-ai-agent');
cdk.Tags.of(app).add('Environment', env);

app.synth();
