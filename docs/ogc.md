# OGC API – Processes (pygeoapi)

GECAA exposes two processes through pygeoapi (MIT), optional extra `ogc`: `pip install -e .[ogc]`.

| Process | Wraps | Inputs |
|---|---|---|
| `gecaa-starkregen` | `gecaa.risk.chain.run` | `lon`, `lat`, `provider` (`nrw`/`synthetic`), `box_m`, `rps`, `prefer_worker` |
| `gecaa-hitze` | `gecaa.risk.heat.assess` | `lon`, `lat`, `provider` |

**Mounted in FastAPI:** when pygeoapi is installed, `uvicorn gecaa.api:app` serves it under `/ogc`
(set `GECAA_OGC=0` to disable, `GECAA_OGC_URL` for the public base URL). Example:

    curl -X POST localhost:8000/ogc/processes/gecaa-starkregen/execution \
      -H 'Content-Type: application/json' -d '{"inputs": {"lon": 6.95, "lat": 51.25, "provider": "nrw"}}'

**Standalone:**

    export PYGEOAPI_CONFIG=ogc/pygeoapi-config.yml PYGEOAPI_OPENAPI=ogc/openapi.yml GECAA_OGC_URL=http://localhost:5000
    pygeoapi openapi generate $PYGEOAPI_CONFIG --output-file $PYGEOAPI_OPENAPI
    pygeoapi serve
