FROM python:3.12

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY frontend /code/frontend

# --timeout restarts a worker whose event loop stops responding; it does not
# limit request length (Solr timeouts are in frontend/main.py).
CMD gunicorn -b 0.0.0.0:${API_PORT} -w ${NUM_WORKERS} -k uvicorn_worker.UvicornWorker --timeout 90 frontend.main:app --access-logfile - --error-logfile -
