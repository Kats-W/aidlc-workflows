"""chat_api — customer-facing streaming RAG chat API (U-08).

A FastAPI app that exposes the existing RAG pipeline over HTTP with
Server-Sent Events, so a web chat UI can render answer tokens as they are
generated. Designed to run behind the AWS Lambda Web Adapter on a Lambda
Function URL in ``RESPONSE_STREAM`` mode.
"""
