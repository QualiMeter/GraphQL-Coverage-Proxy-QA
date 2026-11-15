FROM python:3.12-slim
WORKDIR /app
COPY web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY web ./web
EXPOSE 9110
CMD ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "9110", "--no-access-log"]
