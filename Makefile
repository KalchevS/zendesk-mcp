.PHONY: install run run-http docker-build docker-run docker-stop compose-up compose-down compose-logs

# Install Python dependencies
install:
	pip install -r requirements.txt

# Run the MCP server in stdio mode (for local subprocess usage)
run:
	python3 mcp_server.py

# Run the MCP server in HTTP/SSE mode (for remote/network usage)
run-http:
	python3 mcp_server_http.py

# Build the Docker image (embeds .env credentials)
docker-build:
	docker build -t zendesk-mcp .

# Run the Docker container (detached, port 8080)
docker-run:
	docker run -d --name zendesk-mcp -p 8080:8080 zendesk-mcp

# Stop and remove the Docker container
docker-stop:
	docker stop zendesk-mcp && docker rm zendesk-mcp

# Docker Compose — build and start
compose-up:
	docker compose up -d --build

# Docker Compose — stop and remove
compose-down:
	docker compose down

# Docker Compose — view logs
compose-logs:
	docker compose logs -f
