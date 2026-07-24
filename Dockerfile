FROM node:22-slim

# git é exigido/recomendado pelo Codex; python3 é usado pela API wrapper
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Instala o Codex CLI real da OpenAI
RUN npm install -g @openai/codex

WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

COPY ./app ./app

# Diretório onde o Codex vai trabalhar (montado como volume)
RUN mkdir -p /workspace /data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
