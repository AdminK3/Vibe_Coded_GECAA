# GECAA CLIMADA worker (GPL-3.0). Separate image so the GECAA image stays Apache-2.0.
FROM condaforge/miniforge3:24.9.2-0
RUN mamba install -y -q -c conda-forge python=3.11 climada=6.1.0 fastapi uvicorn && mamba clean -afy
WORKDIR /app
COPY app.py impact.py LICENSE ./
EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
