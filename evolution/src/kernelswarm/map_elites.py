from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from .models import Descriptor


@dataclass(slots=True)
class DescriptorAxes:
    keys: tuple[str, ...] = ("reg_pressure_bin", "occupancy_bin", "launch_block_bin", "source_ops_bin")
    bins: dict[str, int] = field(
        default_factory=lambda: {
            "occupancy_bin": 4,
            "launch_block_bin": 8,
            "source_ops_bin": 8,
            # Keep compatibility for plugins still emitting these axes.
            "reg_pressure_bin": 4,
            "smem_bin": 4,
        }
    )

    def key_for(self, descriptor: Descriptor) -> tuple[int, ...]:
        key: list[int] = []
        for axis in self.keys:
            raw = descriptor.values.get(axis, 0)
            try:
                value = int(float(raw))
            except (TypeError, ValueError):
                value = 0
            bins = max(1, int(self.bins.get(axis, 1)))
            value = max(0, min(value, bins - 1))
            key.append(value)
        return tuple(key)

    def total_bins(self) -> int:
        total = 1
        for axis in self.keys:
            total *= max(1, int(self.bins.get(axis, 1)))
        return total


@dataclass(slots=True)
class ArchiveCell:
    bin_key: tuple[int, ...]
    candidate_id: str
    fitness: float
    descriptor_values: dict[str, int | float]
    updated_iteration: int


@dataclass(slots=True)
class ArchiveUpdate:
    accepted: bool
    bin_key: tuple[int, ...]
    replaced_candidate_id: str | None = None
    replaced_fitness: float | None = None


class MapElitesArchive:
    def __init__(self, axes: DescriptorAxes, *, epsilon: float = 1e-9) -> None:
        self.axes = axes
        self.epsilon = epsilon
        self._cells: dict[tuple[int, ...], ArchiveCell] = {}

    @property
    def occupied_bins(self) -> int:
        return len(self._cells)

    def coverage_ratio(self) -> float:
        total = self.axes.total_bins()
        if total <= 0:
            return 0.0
        return float(self.occupied_bins) / float(total)

    def cell_for(self, bin_key: tuple[int, ...]) -> ArchiveCell | None:
        return self._cells.get(bin_key)

    def insert(
        self,
        *,
        candidate_id: str,
        fitness: float,
        descriptor: Descriptor,
        iteration: int,
    ) -> ArchiveUpdate:
        bin_key = self.axes.key_for(descriptor)
        existing = self._cells.get(bin_key)
        if existing is None:
            self._cells[bin_key] = ArchiveCell(
                bin_key=bin_key,
                candidate_id=candidate_id,
                fitness=fitness,
                descriptor_values=dict(descriptor.values),
                updated_iteration=iteration,
            )
            return ArchiveUpdate(accepted=True, bin_key=bin_key)

        if fitness > (existing.fitness + self.epsilon):
            replaced = existing.candidate_id
            replaced_fitness = existing.fitness
            self._cells[bin_key] = ArchiveCell(
                bin_key=bin_key,
                candidate_id=candidate_id,
                fitness=fitness,
                descriptor_values=dict(descriptor.values),
                updated_iteration=iteration,
            )
            return ArchiveUpdate(
                accepted=True,
                bin_key=bin_key,
                replaced_candidate_id=replaced,
                replaced_fitness=replaced_fitness,
            )

        return ArchiveUpdate(accepted=False, bin_key=bin_key)

    def select_parent(self, rng: random.Random) -> str | None:
        if not self._cells:
            return None

        cells = list(self._cells.values())
        draw = rng.random()
        if draw < 0.60:
            return rng.choice(cells).candidate_id
        if draw < 0.90:
            return self._fitness_biased_choice(cells, rng).candidate_id
        return self._novelty_biased_choice(cells, rng).candidate_id

    def top_elites(self, n: int) -> list[ArchiveCell]:
        if n <= 0:
            return []
        ranked = sorted(self._cells.values(), key=lambda cell: cell.fitness, reverse=True)
        return ranked[:n]

    def export_state(self) -> dict[str, Any]:
        return {
            "axes": {
                "keys": list(self.axes.keys),
                "bins": dict(self.axes.bins),
            },
            "epsilon": self.epsilon,
            "cells": [
                {
                    "bin_key": list(cell.bin_key),
                    "candidate_id": cell.candidate_id,
                    "fitness": cell.fitness,
                    "descriptor_values": dict(cell.descriptor_values),
                    "updated_iteration": cell.updated_iteration,
                }
                for cell in self._cells.values()
            ],
        }

    @classmethod
    def from_state(cls, payload: dict[str, Any]) -> "MapElitesArchive":
        axes_payload = payload.get("axes", {})
        axes = DescriptorAxes(
            keys=tuple(
                axes_payload.get("keys", ("reg_pressure_bin", "occupancy_bin", "launch_block_bin", "source_ops_bin"))
            ),
            bins=dict(axes_payload.get("bins", {})),
        )
        archive = cls(axes, epsilon=float(payload.get("epsilon", 1e-9)))
        for row in payload.get("cells", []):
            bin_key = tuple(int(v) for v in row.get("bin_key", []))
            archive._cells[bin_key] = ArchiveCell(
                bin_key=bin_key,
                candidate_id=str(row["candidate_id"]),
                fitness=float(row["fitness"]),
                descriptor_values=dict(row.get("descriptor_values", {})),
                updated_iteration=int(row.get("updated_iteration", 0)),
            )
        return archive

    @staticmethod
    def _fitness_biased_choice(cells: list[ArchiveCell], rng: random.Random) -> ArchiveCell:
        min_fit = min(cell.fitness for cell in cells)
        weights = [max(1e-9, cell.fitness - min_fit + 1e-6) for cell in cells]
        total = sum(weights)
        pick = rng.random() * total
        cumulative = 0.0
        for cell, weight in zip(cells, weights):
            cumulative += weight
            if cumulative >= pick:
                return cell
        return cells[-1]

    @staticmethod
    def _novelty_biased_choice(cells: list[ArchiveCell], rng: random.Random) -> ArchiveCell:
        latest = max(cell.updated_iteration for cell in cells)
        weights = [max(1.0, 1.0 + (cell.updated_iteration - latest + 8)) for cell in cells]
        total = float(sum(weights))
        pick = rng.random() * total
        cumulative = 0.0
        for cell, weight in zip(cells, weights):
            cumulative += float(weight)
            if cumulative >= pick:
                return cell
        return cells[-1]


@dataclass(slots=True)
class IslandPolicy:
    island_id: str
    style: str
    mutation_scale: float


@dataclass(slots=True)
class IslandState:
    policy: IslandPolicy
    archive: MapElitesArchive
    accepted_updates: int = 0
    imported_parent_ids: list[str] = field(default_factory=list)

    def select_parent(self, rng: random.Random) -> str | None:
        if self.imported_parent_ids:
            if rng.random() < 0.20:
                idx = rng.randrange(0, len(self.imported_parent_ids))
                return self.imported_parent_ids.pop(idx)
        return self.archive.select_parent(rng)


def default_island_policies() -> list[IslandPolicy]:
    return [
        IslandPolicy(island_id="island-a", style="correctness_first", mutation_scale=0.6),
        IslandPolicy(island_id="island-b", style="aggressive", mutation_scale=1.4),
        IslandPolicy(island_id="island-c", style="memory_explorer", mutation_scale=1.0),
        IslandPolicy(island_id="island-d", style="occupancy_tuner", mutation_scale=0.9),
    ]


def migrate_ring(
    islands: list[IslandState],
    *,
    packet_size: int,
    candidate_by_id: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    if packet_size <= 0 or len(islands) <= 1:
        return {}

    migrations: dict[str, list[str]] = {}
    for idx, island in enumerate(islands):
        target = islands[(idx + 1) % len(islands)]
        elites = island.archive.top_elites(packet_size)
        ids = [cell.candidate_id for cell in elites]
        if not ids:
            continue
        target.imported_parent_ids.extend(ids)
        migrations[target.policy.island_id] = list(ids)

    # Keep only known candidate ids if mapping is provided.
    if candidate_by_id is not None:
        for island in islands:
            island.imported_parent_ids = [
                cid for cid in island.imported_parent_ids if cid in candidate_by_id
            ]
    return migrations


def scalarize_raw_score(raw_score: float | dict[str, float]) -> float:
    if isinstance(raw_score, (int, float)):
        return float(raw_score)
    if "fitness" in raw_score:
        return float(raw_score["fitness"])
    if not raw_score:
        return float("-inf")
    return float(max(raw_score.values()))


def finite_fitness(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        return float("-inf")
    return value
