FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# Upgrade pip, install pinned deps, then VERIFY the correct mcp (with FastMCP)
# actually installed. If an old/wrong mcp slips in, the BUILD fails here with a
# clear message instead of crash-looping at runtime with ModuleNotFoundError.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && python -c "import mcp.server.fastmcp, importlib.metadata as md; print('BUILD CHECK OK — mcp', md.version('mcp'))"

COPY boxzooka_mcp.py .

ENV MCP_TRANSPORT=streamable_http
ENV PORT=8000
EXPOSE 8000

CMD ["python", "boxzooka_mcp.py"]
