FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/servidor
COPY servidor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY servidor/ .
COPY web/ /app/web/
ENV CORTADOR_HOST=0.0.0.0 CORTADOR_SEM_NAVEGADOR=1
EXPOSE 8765
CMD ["python", "app.py"]
