#!/bin/bash
# Lambda Web Adapter entrypoint (U-08 chat_api).
#
# The chat-api Lambda sets its handler to this script. With
# AWS_LAMBDA_EXEC_WRAPPER=/opt/bootstrap the Web Adapter starts, runs this
# script to bring up the ASGI server on $AWS_LWA_PORT, then proxies the
# Function URL request to it — streaming the SSE response back when
# AWS_LWA_INVOKE_MODE=response_stream.
# Lambda only puts the layer (/opt/python) and the function code (/var/task)
# on sys.path for the *runtime handler*, not for a process we spawn ourselves.
# Export them explicitly so `python -m uvicorn` (layer) can import the app
# (/var/task). Invoke via `python -m` since the layer's console scripts
# (/opt/python/bin) are not on PATH.
export PYTHONPATH="/opt/python:/var/task:${PYTHONPATH}"
exec python -m uvicorn src.chat_api.app:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
