# SPDX-License-Identifier: Apache-2.0
"""Heavy-rain screening on a terrain grid: where does water collect, and how deep?

Method ("Bluespot" screening, as used in Danish and German municipal heavy-rain checks):

1. Priority-flood (Barnes, Lehman & Mulla 2014, with epsilon) fills every depression of the
   terrain; filled minus terrain gives the depressions ("Senken") and their capacity.
2. D8 flow directions on the epsilon-filled surface route every cell towards the edge.
3. Each cell's runoff (rain x runoff coefficient x cell area) is sent downstream to the first
   depression it meets; depressions fill as level pools and pass their overflow on to the
   depression downstream (cascade, processed upstream first).

The result is the water depth after the rain event, per cell, without time dynamics,
infiltration during the event or sewer capacity. It is a screening model: it shows where water
collects, not a hydrodynamic simulation. Every number is reproducible from the inputs.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

EPS = 1e-4          # metres per step on flats, so filled flats still drain
MIN_DEPTH = 0.02    # depressions shallower than 2 cm are ignored (DGM1 vertical accuracy is about 0.15 m)
D8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def priority_flood(dem: np.ndarray, eps: float = EPS) -> np.ndarray:
    """Fill all depressions; flats get a tiny gradient towards the outlet (Barnes et al. 2014)."""
    rows, cols = dem.shape
    filled = dem.astype("float64").copy()
    closed = np.zeros(dem.shape, bool)
    heap: list[tuple[float, int, int, int]] = []
    order = 0
    for r in range(rows):
        for c in (0, cols - 1):
            heapq.heappush(heap, (filled[r, c], order, r, c)); closed[r, c] = True; order += 1
    for c in range(1, cols - 1):
        for r in (0, rows - 1):
            heapq.heappush(heap, (filled[r, c], order, r, c)); closed[r, c] = True; order += 1
    while heap:
        z, _, r, c = heapq.heappop(heap)
        for dr, dc in D8:
            rr, cc = r + dr, c + dc
            if 0 <= rr < rows and 0 <= cc < cols and not closed[rr, cc]:
                closed[rr, cc] = True
                if filled[rr, cc] <= z:
                    filled[rr, cc] = z + eps
                order += 1
                heapq.heappush(heap, (filled[rr, cc], order, rr, cc))
    return filled


def d8_receivers(surface: np.ndarray, cell: float) -> np.ndarray:
    """Flat index of the steepest-descent neighbour; -1 for cells that drain off the grid."""
    rows, cols = surface.shape
    pad = np.pad(surface, 1, constant_values=-np.inf)   # the edge is the lowest outlet
    best = np.zeros(surface.shape)
    recv = np.full(surface.shape, -1, dtype=np.int64)
    rr, cc = np.indices(surface.shape)
    for dr, dc in D8:
        dist = cell * (2 ** 0.5 if dr and dc else 1.0)
        nb = pad[1 + dr:1 + dr + rows, 1 + dc:1 + dc + cols]
        slope = (surface - nb) / dist
        better = slope > best
        best = np.where(better, slope, best)
        nr, nc = rr + dr, cc + dc
        inside = (nr >= 0) & (nr < rows) & (nc >= 0) & (nc < cols)
        recv = np.where(better, np.where(inside, nr * cols + nc, -1), recv)
    return recv.ravel()


@dataclass
class Depression:
    id: int
    cells: np.ndarray          # flat indices
    capacity_m3: float
    inflow_m3: float = 0.0
    stored_m3: float = 0.0
    level: float | None = None
    downstream: int | None = None


@dataclass
class PondingResult:
    depth: np.ndarray                     # water depth (m): max of ponded depth and flow depth
    capacity_depth: np.ndarray            # depth of the depressions when full (m)
    flow_acc_m3: np.ndarray               # runoff volume passing each cell (m3)
    depressions: list[Depression] = field(default_factory=list)
    rain_mm: float = 0.0
    runoff_m3: float = 0.0
    spilled_off_grid_m3: float = 0.0
    pond_depth: np.ndarray | None = None  # standing water in depressions (m)
    flow_depth: np.ndarray | None = None  # peak flow depth on flow paths (m), Manning-Strickler


def _level_for_volume(z: np.ndarray, volume: float, area: float) -> float:
    """Water level so that sum(max(0, level - z)) * area == volume (z sorted ascending)."""
    if volume <= 0:
        return float(z[0])
    csum = np.cumsum(z)
    for k in range(1, len(z) + 1):
        level = (volume / area + csum[k - 1]) / k
        if k == len(z) or level <= z[k]:
            return float(level)
    return float(z[-1])


MIN_SLOPE = 0.005   # flow-depth slope floor (flats)


def flow_depth(acc_m3: np.ndarray, filled: np.ndarray, recv: np.ndarray, cell: float, duration_s: float,
               manning_n: np.ndarray | float) -> np.ndarray:
    """Peak flow depth on each cell for steady flow (rational method + Manning-Strickler, wide channel).

    Q = upstream runoff volume / rain duration (block rain, concentration time below the duration),
    h = (Q * n / (b * sqrt(S))) ** 0.6 with flow width b = cell size.
    """
    z = filled.ravel()
    rows, cols = filled.shape
    r = np.where(recv >= 0, recv, np.arange(recv.size))
    dr = np.abs(r // cols - np.arange(recv.size) // cols)
    dc = np.abs(r % cols - np.arange(recv.size) % cols)
    dist = np.where((dr + dc) == 2, cell * 2 ** 0.5, cell)
    slope = np.maximum((z - z[r]) / dist, MIN_SLOPE)
    q = acc_m3.ravel() / duration_s
    n = np.broadcast_to(np.asarray(manning_n, dtype=float), filled.shape).ravel()
    h = (q * n / (cell * np.sqrt(slope))) ** 0.6
    return h.reshape(filled.shape)


def ponding(dem: np.ndarray, cell: float, rain_mm: float, runoff_coeff: np.ndarray | float,
            filled: np.ndarray | None = None, recv: np.ndarray | None = None,
            duration_s: float | None = None, manning_n: np.ndarray | float = 0.03) -> PondingResult:
    """Water depth after a rain event of `rain_mm`, with runoff coefficient per cell (0..1).

    With `duration_s`, the peak flow depth on flow paths is added (max of ponded and flow depth)."""
    rows, cols = dem.shape
    n = rows * cols
    area = cell * cell
    if filled is None:
        filled = priority_flood(dem)
    if recv is None:
        recv = d8_receivers(filled, cell)
    cap = np.where(filled - dem > MIN_DEPTH, filled - dem, 0.0)
    cap = np.where(cap > 0, filled - dem, 0.0)          # whole depression including its shallow rim
    labels, count = ndimage.label(cap > 0, structure=np.ones((3, 3)))
    lab = labels.ravel()

    runoff = (rain_mm / 1000.0) * np.broadcast_to(np.asarray(runoff_coeff, dtype=float), dem.shape) * area
    runoff_flat = runoff.ravel().copy()

    # route every cell to the first depression downstream (process from low to high surface)
    order = np.argsort(filled.ravel(), kind="stable")
    target = np.zeros(n, dtype=np.int64)               # 0 = leaves the grid
    for i in order:
        if lab[i]:
            target[i] = lab[i]
        else:
            r = recv[i]
            target[i] = 0 if r < 0 else target[r]

    # flow accumulation of runoff (upstream first = high to low)
    acc = runoff_flat.copy()
    for i in order[::-1]:
        r = recv[i]
        if r >= 0 and not lab[i]:
            acc[r] += acc[i]

    deps: dict[int, Depression] = {}
    objs = ndimage.find_objects(labels)
    for k in range(1, count + 1):
        sl = objs[k - 1]
        sub = labels[sl] == k
        idx = np.ravel_multi_index(np.nonzero(sub), sub.shape)
        rr, cc = np.unravel_index(idx, sub.shape)
        flat = (rr + sl[0].start) * cols + (cc + sl[1].start)
        deps[k] = Depression(id=k, cells=flat, capacity_m3=float(cap.ravel()[flat].sum() * area))
    # where does a full depression spill to? follow the flow from its highest-surface cell outwards
    for d in deps.values():
        start = d.cells[np.argmin(filled.ravel()[d.cells])]   # outlet side of the filled surface
        i, seen = start, 0
        while i >= 0 and lab[i] == d.id and seen < n:
            i = recv[i]; seen += 1
        d.downstream = None if i < 0 else (int(target[i]) or None)

    # direct runoff into each depression (including rain falling on it)
    inflow = np.bincount(target, weights=runoff_flat, minlength=count + 1)
    for k, d in deps.items():
        d.inflow_m3 = float(inflow[k])

    # cascade in topological order: a depression is processed only after everything spilling into it
    for d in deps.values():
        if d.downstream == d.id or d.downstream not in deps:
            d.downstream = None
    indeg = {k: 0 for k in deps}
    for d in deps.values():
        if d.downstream:
            indeg[d.downstream] += 1
    queue = [k for k, v in indeg.items() if v == 0]
    topo: list[Depression] = []
    while queue:
        k = queue.pop()
        topo.append(deps[k])
        nxt = deps[k].downstream
        if nxt:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    in_cycle = [deps[k] for k, v in indeg.items() if v > 0]   # numerical ties only; their spill leaves the grid
    for d in in_cycle:
        d.downstream = None
    off_grid = float(inflow[0])
    for d in topo + in_cycle:
        d.stored_m3 = min(d.inflow_m3, d.capacity_m3)
        spill = d.inflow_m3 - d.stored_m3
        if spill > 0:
            if d.downstream and d.downstream in deps and d.downstream != d.id:
                deps[d.downstream].inflow_m3 += spill
            else:
                off_grid += spill

    depth = np.zeros(n)
    z = dem.ravel()
    for d in deps.values():
        if d.stored_m3 <= 0:
            continue
        zc = np.sort(z[d.cells])
        d.level = _level_for_volume(zc, d.stored_m3, area)
        depth[d.cells] = np.clip(d.level - z[d.cells], 0, None)
    pond = depth.reshape(dem.shape)
    acc2 = acc.reshape(dem.shape)
    fdepth = None
    total = pond
    if duration_s:
        fdepth = flow_depth(acc2, filled, recv, cell, duration_s, manning_n)
        fdepth = np.where(cap > 0, 0.0, fdepth)          # inside depressions the ponded depth applies
        total = np.maximum(pond, fdepth)
    return PondingResult(depth=total, capacity_depth=cap, flow_acc_m3=acc2,
                         depressions=list(deps.values()), rain_mm=rain_mm,
                         runoff_m3=float(runoff_flat.sum()), spilled_off_grid_m3=off_grid,
                         pond_depth=pond, flow_depth=fdepth)
