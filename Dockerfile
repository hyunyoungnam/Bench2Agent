# Bench2Agent web host: the hosted conversation over HTTP MCP, one container.
#
#   docker build -t bench2agent-web .
#   docker run -p 8780:8780 -v bench2agent-data:/data \
#       -e ANTHROPIC_API_KEY=... \            # service credential mode (operator pays; per-user limits apply)
#       bench2agent-web
#
# /data holds the installed data revisions and the host's own store (users,
# invites, sessions, conversations). Mount a volume or the state is lost with
# the container. The first start installs the pinned data release unless a
# revision is already there. Put HTTPS termination (Caddy, nginx) in front;
# the container speaks plain HTTP on 8780.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 BENCH2AGENT_HOME=/data BENCH2AGENT_WEB_HOST=0.0.0.0 BENCH2AGENT_WEB_PORT=8780
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir '.[server]'
COPY scripts/docker-entrypoint.sh /usr/local/bin/bench2agent-entrypoint
RUN chmod +x /usr/local/bin/bench2agent-entrypoint && mkdir -p /data
VOLUME ["/data"]
EXPOSE 8780
ENTRYPOINT ["bench2agent-entrypoint"]
