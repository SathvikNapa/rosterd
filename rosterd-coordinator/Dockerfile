# rosterd coordinator service. Single instance, federates every site's kernel.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8300
EXPOSE 8300

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8300"]
