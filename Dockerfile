FROM python:3.12

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY frontend /code/frontend

CMD ["uvicorn", "frontend.main:app", "--host", "0.0.0.0", "--port", "80", "--reload", "--log-config=frontend/log_conf.yaml"]

# If running behind a proxy like Nginx or Traefik add --proxy-headers
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80", "--proxy-headers"]