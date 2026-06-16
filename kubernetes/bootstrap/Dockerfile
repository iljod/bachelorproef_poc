FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 1000 --create-home appuser
USER appuser
CMD ["python", "bootstrap.py"]
