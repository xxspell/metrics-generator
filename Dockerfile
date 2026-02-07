FROM python:3.12-slim

# Install runtime tools
RUN apt-get update && apt-get install -y git cron && rm -rf /var/lib/apt/lists/*

RUN pip install uv

# Copy project
COPY . /app

WORKDIR /app

# Make run.sh executable
RUN chmod +x run.sh
RUN chmod +x start-cron.sh

# Install dependencies
RUN uv sync

# Default command
CMD ["./start-cron.sh"]
