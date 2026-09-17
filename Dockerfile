# The web app image. fly.toml, the app name, and every secret live in the ops repo.
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .[web]
EXPOSE 8080
CMD ["python", "-m", "jobbrief.web"]
