FROM python:3.12

WORKDIR /code

COPY ./requirements.txt /code/requirements.txt

RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

COPY frontend /code/frontend

CMD gunicorn -b 0.0.0.0:${API_PORT} -w ${NUM_WORKERS} -k uvicorn.workers.UvicornWorker frontend.main:app --access-logfile - --error-logfile -
