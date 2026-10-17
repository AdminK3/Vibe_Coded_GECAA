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

## Version 4: deeper analysis and outputs

| Area | What is new | Open source / open data |
|---|---|---|
| **2D hydraulics** | "2D-Simulation" in the Starkregen tab: water depth over time instead of the static screening | LISFLOOD-FP 8.1 (separate GPL service, `services/lisflood_worker`); fallback: GECAA's own local-inertial solver (Bates et al. 2010) |
| **Cross-check** | Depression depth and wetness index | WhiteboxTools |
| **Sewer** | Sewer takes the design rain (DWA-A 118 / DIN EN 752, default 3-year); optional SWMM model upload, node flooding added to the surface | KOSTRA, PySWMM / EPA SWMM |
| **Measures** | Green roof (extensive/intensive), unsealing, trees (drawn canopy, tree pits), troughs; retained volume and heat note per measure | DIN 1986-100 (oriented), literature values with sources |
| **Buildings** | Measured heights and storeys, residents per building (Zensus 100 m cells shared by floor area), optional basements | NRW LoD2 (OGC API 3D), Zensus 2022 |
| **Uncertainty** | 90 % range of the expected annual damage and Sobol sensitivity | CLIMADA unsequa (worker), numpy Monte Carlo fallback |
| **Heat** | Heatwaves and tropical nights at the nearest DWD station; summer land surface temperature map | xclim (CLIMAAX thresholds), Landsat via STAC (Planetary Computer) |
| **Planning** | Development plans around the site with legal status and link | NRW INSPIRE Planned Land Use (OGC API) |
| **Outputs** | PDF report (all analyses), 3D view, GeoTIFF export, OGC API Processes under `/ogc`, QGIS plugin | Typst, MapLibre GL, rasterio, pygeoapi, QGIS |

Install all optional parts: `pip install -e '.[heat,stac,ogc,hydraulics,sewer]'`, then
`services/climada_worker/install.sh`, `services/lisflood_worker/install.sh` and `python scripts/prepare_zensus.py`
(Codespaces does this automatically on creation). Every part degrades gracefully when missing and says so.

## Version 3: climate risk at the site (start page)

Version 3 adds two analyses and a risk assessment to the permit check, all for any place in NRW with live open
data. The CLIMADA risk engine (ETH Zürich) computes the damages; methods and scales come from the EU project CLIMAAX.

| Tab | What it does | Data and methods |
|---|---|---|
| **Versickerung** | Permit check for troughs (K3 logic, see version 2) | ALKIS, BK50, protection zones |
| **Starkregen** | Water depth per return period (10/30/100 years) and climate scenario (today, +1 K, +2 K), damage per building, expected annual damage, effect and benefit of the troughs drawn in the permit check | DGM1 (1 m, resampled to 2 m), ALKIS buildings and land use, KOSTRA-DWD-2020, JRC depth-damage curves (via CLIMAAX), **CLIMADA** ImpactCalc and CostBenefit |
| **Hitze** | Heat stress at the site and observed hot days since 1961 with a robust trend | LANUK Klimaanalyse NRW (PET, night temperature, sealing), DWD 1 km grids, Theil-Sen trend |
| **Risikobewertung** | The five CLIMAAX risk-assessment steps, Key Risk Assessment (severity now/future, urgency, resilience, priority) proposed from the analyses and editable, risk matrix | CLIMAAX handbook scales 1–4 |

How the heavy-rain chain works (`src/gecaa/risk/`):

1. **Terrain**: DGM1 from the NRW WCS for the analysis area plus a 100 m border; buildings from ALKIS are raised
   0.5 m as obstacles (canopies excluded).
2. **Rain**: KOSTRA-DWD-2020 for the 5 km cell of the site (duration 15 min, 60 min or 4 h); climate scenarios
   scale it by 7 % per kelvin (Clausius-Clapeyron, Lenderink & van Meijgaard 2008). The CLIMAAX function
   `magnitude_frequency_shift` shows how often today's 100-year rain occurs with +1 K and +2 K.
3. **Runoff**: coefficients per ALKIS land-use class (oriented on DIN 1986-100), roofs 1.0.
4. **Ponding and flow**: priority-flood (Barnes et al. 2014), D8 flow directions, depressions fill and spill
   downstream in topological order with an exact volume balance; on flow paths the peak flow depth follows from
   the rational method and Manning-Strickler. Result: water depth per cell, about 5 s for 300 × 300 cells.
5. **Measures**: each trough with its roofs stores rain up to area × storage depth (default 0.3 m).
6. **Damage**: water depth in a 2 m ring around each building, minus a threshold height (default 10 cm), JRC
   curve for the building class, footprint × EU maximum damage (CLIMAAX values, price adjusted).
7. **Impact**: CLIMADA builds a Hazard (one event per return period, banded frequencies), Exposures and
   impact functions, computes the expected annual damage and, with a measure, `CostBenefit` over 30 years at
   2 %. Without the worker the built-in engine computes the same quantities; a test checks that both agree.
8. **Check**: the depths at the most affected buildings are compared with the BKG heavy-rain hazard map.

Every result lists its assumptions with kind (Messwert, Normwert, Annahme, Vereinfachung) and source. It is a
screening ("Senken- und Fließweganalyse"), not a hydrodynamic simulation; damages are an order of magnitude.

**CLIMADA is GPL-3.0** and runs as a separate service in `services/climada_worker` (own licence, own Python 3.11
environment). GECAA only exchanges JSON with it, so GECAA stays Apache-2.0. In Codespaces it installs itself in the
background on first start; by hand: `services/climada_worker/install.sh` and `services/climada_worker/run.sh`,
or `docker compose up`. The badge at the top right shows which engine is active.

Other improvements: shared location bar for all tabs, dark mode, mobile layout, map toolbar (return period,
scenario, with/without measure, flow paths, relief, opacity), SVG charts, project export as JSON, print view.
Version 2 stays at `/v2`, version 1 at `/v1`.

## Version 5: Umsetzung, Förderung, Berichte

After a heavy-rain run, **„Als Projekt planen“** turns the drawn measures into a project at `/projekte`:

- **Zeitplan and Aufgaben:** seven phases (Grundlagen … Pflege & Wirkungskontrolle) with generic and measure-specific
  tasks to clarify (e.g. kf-Wert for troughs, Statik for green roofs, Altlasten for unsealing). GECAA proposes no
  durations or prices: dates, owners and costs come from the user, missing ones show as OFFEN. Bar chart, ICS calendar
  and CSV task list.
- **Plan checks:** end before start, overdue tasks, missing owners/dates, funding rules such as "Zusage vor
  Vorhabenbeginn" (KfW 444) and closed funding windows.
- **Kosten:** cost lines with source; cost per m³ retained per measure (abgeleitet).
- **Förderung:** `data/knowledge/funding.yaml` holds KfW 444 NKK, Klimaanpassung.Kommunen.NRW, AnpaSo, DAS and local
  programmes, each with source, source date and check date. Only published figures are entered; the grant is computed
  only from user costs × published rate (with caps and minimums) and labelled abgeleitet.
- **Bericht:** report types (Beschlussvorlage, Förder-Projektskizze, Bürgerinfo, Sachstandsbericht) generate editable
  Markdown sections from the project data. Rewrite, reorder (drag or arrows), hide, regenerate single sections or add
  your own; export as PDF (Typst; text is passed as data, never as Typst markup) or Markdown.
- Projects are stored in SQLite (`data/cache/projects.sqlite`, or `GECAA_PROJECTS_DB`) with optimistic locking and a
  change log. API: `/v5/...` (see `/docs`).

## Version 2

Version 2 follows the K3 logic conventions and works anywhere in North Rhine-Westphalia
with live open data. The interface and the report are in German.

- **Find the place** by address, pick the parcel (ALKIS).
- **Draw each planned trough (Mulde) on unbuilt land** and click the roofs that drain
  into it. The unit of assessment is the location plus its catchment; roofs are only
  ever catchment.
- **GECAA fetches the inputs** for each location and states the source and the data
  quality (hoch / mittel / Annahme offen) of every value:

| Input | Source (live) | Licence |
|---|---|---|
| Parcel, building footprints, distance to buildings | Geobasis NRW, ALKIS vereinfacht (WFS) | dl-de/zero-2-0 |
| Permeability kf | GD NRW, soil map BK50 (WMS feature info), cm/d converted to m/s | dl-de/by-2-0 |
| Depth to groundwater | GD NRW, BK50 groundwater level class; shallow edge of the class is used (marked "abgeleitet") | dl-de/by-2-0 |
| Water protection zone | LANUK, Wasserschutzgebiete (WMS feature info); planned zones stay OPEN | free, commercial use needs permission |
| Contaminated sites, basement depth | asked from the user: there is no open statewide service | – |
| Map overlays: aerial photo, cadastral map, infiltration suitability, heavy-rain flow velocity | Geobasis NRW, GD NRW, BKG (display only) | dl-de |

- **Every check gives PASSED, OPEN or BLOCKED.** A missing input gives OPEN, never a
  guessed value; permits and case-by-case decisions are OPEN too. The site is
  *möglich* when all locations pass, *teilweise versickerbar* when some do, *nicht
  möglich* when none can.
- **Probabilities are kept as detail.** The soil map's uncertainty is shown as
  "Datensicherheit" next to a check; it never changes the result.
- **The Prüflogik tab** shows the logic (inputs → calculations → checks) as a graph,
  coloured with the result of the selected location, plus every parameter with its source.
- **The report** (sections 0–6) is German, costs are "nicht beziffert", and the next
  steps name the office that can settle each open point.

The logic lives in `blueprints/k3/dwa_a_138_1_mulden_rigolen.yaml` with K3 IDs
(PAxx parameters, CAxx calculations, CHxx checks). It is a draft until it has been audited.
Without internet, choose "Synthetische Demodaten" in the data menu. Version 1 stays at `/v1`.

## Try it

**In GitHub Codespaces** (nothing to install): on the repository page click
**Code → Codespaces → Create codespace on main**. The app installs and starts by
itself and opens in a new browser tab.

**With Docker:**

```bash
docker compose up --build
```

Then open http://localhost:8000 for the app, or http://localhost:8000/docs for the API.

### The app

- **Evaluate:** pick a sample parcel or draw your own on the map, enter the
  sealed and free area and what the contaminated sites register says, and
  press Evaluate. The parcel is coloured by the result, and the panel shows the
  verdict, what would settle an open result, the reasoning and the evidence.
  Every citation opens the source in the Knowledge tab. You can open a
  printable report or download the data as JSON.
- **Map layers:** soil permeability, groundwater depth, slope and water
  protection zones, classified with the same thresholds the evaluation uses.
- **Reasoning:** the blueprint's Bayesian network as a live graph. Click a node
  to see its rule table and sources; after an evaluation every node shows its
  most likely state.
- **Knowledge:** all provisions with summaries and sources. "Check federal law
  for updates" asks the federal legal information system (NeuRIS) for the
  current texts, stores them with date and fingerprint and reports changes.

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
| REST API | Evaluations, blueprints, provisions, map layers | [FastAPI](https://fastapi.tiangolo.com) |
| Web app | Map, drawing, reasoning graph | [Leaflet](https://leafletjs.com), [Leaflet-Geoman](https://geoman.io) (free edition), [Cytoscape.js](https://js.cytoscape.org), bundled locally, no CDN |

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
  web/               the web app (with bundled open-source libraries in web/vendor)
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
