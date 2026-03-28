FROM python:3.11-slim

WORKDIR /app

COPY requirements-proxy.txt .
RUN pip install -r requirements-proxy.txt

COPY proxy.py .
COPY flightclaim-demo.html .

EXPOSE 8000

CMD ["uvicorn", "proxy:app", "--host", "0.0.0.0", "--port", "8000"]
