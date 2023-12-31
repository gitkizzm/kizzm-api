FROM python:3.9

WORKDIR /tst-api

COPY requirements.txt .
COPY ./src ./src

RUN pip install -r requirements.txt

EXPOSE 8080

CMD ["python", "./src/main.py"]