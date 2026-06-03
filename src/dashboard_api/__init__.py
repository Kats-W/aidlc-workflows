"""Admin Dashboard backend (U-07).

Provides the API Gateway (HTTP API) backend for the au Jibun Bank AI Agent admin
dashboard:

  - :mod:`handler` — DashboardApiLambda: routes ``GET /suggestions``,
    ``PATCH /suggestions/{id}``, ``GET /metrics`` and ``GET /suggestions/csv``
    (US-7.1, US-7.2). Cognito JWT authorization is enforced by the HTTP API
    authorizer, so the Lambda does not re-validate the token.
  - :mod:`metrics_aggregator` — MetricsAggregatorLambda: aggregates contact /
    CSAT / escalation metrics from the CustomerHistory table over a 7d or 30d
    window (US-7.2). Returns zero/null metrics for empty windows.
"""
