FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY boxzooka_mcp.py .

ENV MCP_TRANSPORT=streamable_http
ENV PORT=8000
EXPOSE 8000

CMD ["python", "boxzooka_mcp.py"]
