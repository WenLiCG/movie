FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8
WORKDIR /app
COPY uploader/uploader.py uploader/cleanup.py archive.py configure.py /app/
COPY portal/versions.py /app/portal/versions.py
RUN mkdir -p /state /config /media && chown 1000:1000 /state /config /media
USER 1000:1000
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python", "/app/uploader.py"]
