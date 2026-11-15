FROM python:3.12-slim
WORKDIR /app
COPY mini_shop/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY mini_shop ./mini_shop
EXPOSE 9110
CMD ["uvicorn", "mini_shop.server:app", "--host", "0.0.0.0", "--port", "9110", "--no-access-log"]
