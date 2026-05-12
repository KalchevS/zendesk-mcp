# Zendesk MCP Server — Docker Image
# Exposes the MCP server over HTTP/SSE on port 8080.
#
# Build:
#   docker build -t zendesk-mcp .
#
# The .env file is copied into the image at build time so credentials
# are embedded. Do NOT push this image to public registries.
#
# Run:
#   docker run -p 8080:8080 zendesk-mcp
#
# Override port:
#   docker run -p 9090:9090 -e MCP_PORT=9090 zendesk-mcp

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY config.py .
COPY zendesk_client.py .
COPY tools.py .
COPY tools_extra.py .
COPY mcp_server.py .
COPY mcp_server_http.py .

# Copy .env with credentials (only for standalone docker run;
# docker-compose uses env_file instead)
COPY .env* .

# Expose the HTTP/SSE port
EXPOSE 8080

# Health check for container orchestration
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Run the HTTP/SSE server
CMD ["python", "mcp_server_http.py"]
