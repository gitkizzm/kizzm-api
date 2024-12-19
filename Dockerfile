# Basisimage
FROM python:3.10-slim

# Arbeitsverzeichnis erstellen
WORKDIR /app

# Abhängigkeiten kopieren und installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode kopieren
COPY backend ./backend

# Port definieren
EXPOSE 8000

# Startbefehl
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
