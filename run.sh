#!/bin/bash
# Lambda Web Adapter entrypoint (U-08 chat_api).
#
# The chat-api Lambda sets its handler to this script. With
# AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap the Web Adapter starts, runs this
# script to bring up the ASGI server on $AWS_LWA_PORT, then proxies the
# Function URL request to it — streaming the SSE response back when
# AWS_LWA_INVOKE_MODE=response_stream.
# Invoke via `python -m` so it works even though the layer's console scripts
# (/opt/python/bin) are not on PATH in the Lambda runtime.
exec python -m uvicorn src.chat_api.app:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
