FROM python:3.11-slim

WORKDIR /app

COPY requirements-proxy.txt .
RUN pip install -r requirements-proxy.txt

COPY proxy.py .
COPY flightclaim-demo.html .
COPY terms.html .
COPY privacy.html .

CMD ["python", "-c", "import os,uvicorn; uvicorn.run('proxy:app', host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))"]
