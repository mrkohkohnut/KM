FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir flask waitress
COPY app.py ./
COPY templates templates
COPY static static
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["python", "app.py"]
