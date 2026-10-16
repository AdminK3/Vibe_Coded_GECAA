# GECAA – Governmental Engine for Climate Adaptation Action

GECAA answers the question a municipal climate officer asks for every
adaptation measure: **can we build this here?** It checks the measure against
water law, technical thresholds and the site's geodata, and returns a result
with a full reasoning chain where every step cites its source.

This repository is the first working slice, built entirely from open-source
parts. It covers one measure, the **swale-trench system
(Mulden-Rigolen-System) in North Rhine-Westphalia**, end to end.

> **Status: prototype.** The blueprint's rules and thresholds are drafts that a
> regulatory domain expert has not yet reviewed, and the included site data is
> synthetic. Use it to see how GECAA works, not to make decisions.

## What it does

For one parcel, GECAA:

1. **Reads the site** from geodata: soil permeability, depth to groundwater,
   slope and water protection zones, plus what the municipality knows
   (contamination, available space).
2. **Keeps the uncertainty.** A soil map is not a measurement, so each input
   becomes a probability ("90 % chance the soil drains well enough") instead
   of a false yes or no.
3. **Runs the blueprint**, a Bayesian network whose rules come from WHG,
   LWG NRW and DWA-A 138-1 thresholds.
4. **Explains the result** step by step, with numbered citations to each
   paragraph or standard used.
5. **Says what would settle it.** When the data cannot decide, GECAA names
   the one check (for example an infiltration test) that would change the
   result most.

Example output for the three demo parcels:

```
demo-a     feasible                      99%  (clear)
demo-b     not feasible                  86%  (clear)
demo-c     not feasible                  52%  (open)
           decisive: Distance to groundwater - Check groundwater levels with the lower water authority or a monitoring well near the site.
```

## Try it

**With Docker** (nothing else to install):

```bash
docker compose up --build
```

Then open:

- http://localhost:8000/v1/demo/demo-c – a sample report
- http://localhost:8000/docs – the interactive API

**With Python 3.11 or newer:**

```bash
pip install -e ".[dev]"
gecaa validate                                         # check blueprints and citations
gecaa evaluate data/samples/parcels.geojson --out out  # JSON and HTML reports per parcel
pytest -q                                              # run the tests
uvicorn gecaa.api:app --reload                         # start the API
```

Evaluate your own parcel (GeoJSON in WGS84):

```bash
curl -X POST http://localhost:8000/v1/evaluations/report \
  -H "Content-Type: application/json" \
  -d '{"parcel": {"type": "Polygon", "coordinates": [[[6.9530, 51.2650], [6.9540, 51.2650], [6.9540, 51.2656], [6.9530, 51.2656], [6.9530, 51.2650]]]},
       "context": {"connected_area_m2": 2400, "available_area_m2": 420, "contamination": "none_known"}}'
```

The parcel must lie inside the site data. The sample data covers a 400 × 400 m
area in EPSG:25832.

## Using real data

Copy `data/samples/dataset.yaml`, point each layer at real data and set
`GECAA_DATASET` to the new file. Suitable open sources in NRW include:

| Layer | Source |
|---|---|
| Soil permeability | Geologischer Dienst NRW, soil map BK50 |
| Groundwater depth | Lower water authority or LANUK groundwater level data |
| Terrain | Geobasis NRW, DGM1 |
| Water protection zones | Open data portal NRW / ELWAS |

To fetch the current federal law texts from the federal legal information
system (NeuRIS), run:

```bash
gecaa knowledge refresh
```

Every retrieved text is stored as a snapshot with its source, date and hash.
A changed text is reported for expert review, and reports show which version
of the law they used.

## How it is built

| Part | What it does | Built on |
|---|---|---|
| Regulatory Knowledge Schema | Open data model for provisions and blueprints | [LinkML](https://linkml.io) (`schema/gecaa_rks.yaml`) |
| Knowledge Agent | Fetches law from NeuRIS, versions every text | [HTTPX](https://www.python-httpx.org), NeuRIS API |
| Bayesian Evaluation Engine | Compiles blueprints into Bayesian networks | [pgmpy](https://pgmpy.org) |
| Spatial pipeline | Turns geodata into probabilistic evidence | [rasterio](https://rasterio.readthedocs.io), [Shapely](https://shapely.readthedocs.io) |
| Reports | Self-contained HTML with numbered citations | [Jinja2](https://jinja.palletsprojects.com) |
| REST API | Evaluations, blueprints, provisions | [FastAPI](https://fastapi.tiangolo.com) |

All dependencies use permissive licences (MIT, BSD, Apache-2.0), so the whole
project can be released under Apache-2.0. See [docs/architecture.md](docs/architecture.md)
for the design and the roadmap.

### Repository layout

```
blueprints/          evaluation logic per measure (YAML)
data/knowledge/      provision catalogue with plain-language summaries
data/samples/        synthetic demo site data
schema/              Regulatory Knowledge Schema (LinkML)
src/gecaa/
  engine/            blueprint loading, validation and Bayesian evaluation
  knowledge/         NeuRIS client and versioned snapshot store
  spatial/           geodata to evidence
  report/            HTML report
  api.py, cli.py     interfaces
tests/               pytest suite
```

### Writing a blueprint

A blueprint is a YAML file in `blueprints/`. Root nodes take site evidence;
every other node is a rule table. The first matching rule decides, and each
rule names the provisions that justify it:

```yaml
- id: legal
  label: "Water-law status"
  states: [permissible, with_conditions, not_permissible]
  parents: [protection_zone]
  rules:
    - when: {protection_zone: zone_i_ii}
      then: not_permissible
      explanation: "Infiltration in the inner water protection zones is normally prohibited."
      basis: [whg_52, nrw_wsg_ordinance]
```

`then` can be a single state (where a law fixes the answer) or a probability
distribution (where experts judge an uncertain relationship). `gecaa validate`
checks that every combination of inputs is covered and every citation exists.

## Licence

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). Official legal
texts are not subject to copyright (section 5 UrhG). Technical standards such
as DWA-A 138-1 are licensed works: this repository contains only their
citations and derived threshold values, never their text.

GECAA supports decisions and does not replace them. The responsible officer
and the water authority decide.
