FROM python:3.12-slim
WORKDIR /app
COPY demo_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY demo_service ./demo_service
EXPOSE 9100
CMD ["uvicorn", "demo_service.app:app", "--host", "0.0.0.0", "--port", "9100"]
