# Usar una imagen base oficial de Python
FROM python:3.11-slim

# Instalar FFmpeg dentro del sistema operativo del servidor
RUN apt-get update && apt-get install -y ffmpeg

# Establecer el directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiar el archivo de requerimientos e instalar las librerías de Python
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copiar el resto del código de tu bot al contenedor
COPY . .

# Comando que se ejecutará para iniciar tu bot
CMD ["waitress-serve", "--host=0.0.0.0", "--port=10000", "app:app"]