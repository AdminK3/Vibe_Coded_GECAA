# SPDX-License-Identifier: Apache-2.0
"""Command line interface.

  gecaa evaluate data/samples/parcels.geojson --out out/
  gecaa validate
  gecaa knowledge refresh
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)


def _cmd_evaluate(args) -> int:
    from .report import render_html
    from .service import Gecaa
    from .spatial import load_parcels

    gecaa = Gecaa.from_home(dataset=args.dataset)
    parcels = load_parcels(Path(args.parcels), default_crs=args.crs)
    if args.parcel_id:
        parcels = [p for p in parcels if p.id == args.parcel_id]
    context = {k: v for k, v in {"connected_area_m2": args.connected_area, "available_area_m2": args.available_area,
                                 "contamination": args.contamination}.items() if v is not None}
    out = Path(args.out) if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for parcel in parcels:
        evaluation, summary = gecaa.evaluate_parcel(args.blueprint, parcel, context)
        print(f"{parcel.id:<10} {evaluation.verdict_label:<26} {evaluation.confidence:6.0%}  ({evaluation.certainty})")
        for check in evaluation.next_checks:
            print(f"{'':<10} decisive: {check.label} - {check.advice}")
        if out:
            (out / f"{parcel.id}.json").write_text(evaluation.model_dump_json(indent=2), encoding="utf-8")
            (out / f"{parcel.id}.html").write_text(render_html(evaluation, summary, gecaa.dataset.synthetic), encoding="utf-8")
    if out:
        print(f"reports written to {out}/")
    return 0


def _cmd_validate(args) -> int:
    from . import data_dir
    from .engine import load_blueprints, validate_blueprint
    from .knowledge import load_provisions

    home = data_dir()
    provisions = load_provisions(home / "data" / "knowledge" / "provisions.yaml")
    ok = True
    for bp in load_blueprints(home / "blueprints").values():
        problems = validate_blueprint(bp, provisions)
        print(f"{bp.id}: {'ok' if not problems else 'INVALID'}")
        for p in problems:
            print(f"  - {p}")
        ok = ok and not problems
    return 0 if ok else 1


# Search terms that find each federal law in NeuRIS.
SEARCH_TERMS = {"eli/bund/bgbl-1/2009/s2585": "Wasserhaushaltsgesetz"}


def _cmd_refresh(args) -> int:
    from . import data_dir
    from .knowledge import SnapshotStore, load_provisions
    from .knowledge.neuris import NeurisClient

    home = data_dir()
    provisions = load_provisions(home / "data" / "knowledge" / "provisions.yaml")
    store = SnapshotStore(home / "data" / "knowledge" / "snapshots")
    client = NeurisClient(args.base_url)
    changes, failures = 0, 0
    for prov in provisions.values():
        if not prov.eli:
            continue
        try:
            event = client.refresh(prov, store, SEARCH_TERMS.get(prov.eli, prov.title.split(" - ")[0]))
        except Exception as exc:
            failures += 1
            print(f"{prov.id}: failed - {exc}")
            continue
        if event:
            changes += 1
            print(event.describe())
        else:
            print(f"{prov.id}: unchanged")
    print(f"{changes} new or changed, {failures} failed")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gecaa", description="GECAA measure evaluation")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ev = sub.add_parser("evaluate", help="evaluate parcels from a GeoJSON file")
    ev.add_argument("parcels")
    ev.add_argument("--blueprint", default="mulden_rigolen_system_nrw")
    ev.add_argument("--parcel-id")
    ev.add_argument("--dataset", help="dataset.yaml describing site data layers")
    ev.add_argument("--crs", default="EPSG:4326", help="CRS of the GeoJSON if it has no 'crs' member")
    ev.add_argument("--connected-area", type=float)
    ev.add_argument("--available-area", type=float)
    ev.add_argument("--contamination", choices=["none_known", "suspected", "unknown"])
    ev.add_argument("--out", help="folder for JSON and HTML reports")
    ev.set_defaults(func=_cmd_evaluate)

    va = sub.add_parser("validate", help="check all blueprints against the provision catalogue")
    va.set_defaults(func=_cmd_validate)

    kn = sub.add_parser("knowledge", help="Knowledge Agent commands")
    ksub = kn.add_subparsers(dest="kcmd", required=True)
    rf = ksub.add_parser("refresh", help="fetch federal provisions from NeuRIS and record changes")
    rf.add_argument("--base-url", default="https://testphase.rechtsinformationen.bund.de")
    rf.set_defaults(func=_cmd_refresh)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
