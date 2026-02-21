from __future__ import annotations

import random
import unittest

from kernelswarm.map_elites import (
    DescriptorAxes,
    IslandState,
    MapElitesArchive,
    default_island_policies,
    migrate_ring,
)
from kernelswarm.models import Descriptor


class MapElitesTests(unittest.TestCase):
    def test_archive_insert_and_replace(self) -> None:
        axes = DescriptorAxes()
        archive = MapElitesArchive(axes)
        descriptor = Descriptor(
            run_id="run-1",
            candidate_id="c1",
            descriptor_name="default_v1",
            values={"reg_pressure_bin": 1, "smem_bin": 2, "occupancy_bin": 3},
        )
        update = archive.insert(candidate_id="c1", fitness=10.0, descriptor=descriptor, iteration=1)
        self.assertTrue(update.accepted)
        self.assertEqual(archive.occupied_bins, 1)

        worse = archive.insert(candidate_id="c2", fitness=9.0, descriptor=descriptor, iteration=2)
        self.assertFalse(worse.accepted)
        self.assertEqual(archive.top_elites(1)[0].candidate_id, "c1")

        better = archive.insert(candidate_id="c3", fitness=11.0, descriptor=descriptor, iteration=3)
        self.assertTrue(better.accepted)
        self.assertEqual(better.replaced_candidate_id, "c1")
        self.assertEqual(archive.top_elites(1)[0].candidate_id, "c3")

    def test_parent_selection_returns_known_candidate(self) -> None:
        axes = DescriptorAxes()
        archive = MapElitesArchive(axes)
        for idx in range(4):
            descriptor = Descriptor(
                run_id="run-2",
                candidate_id=f"c{idx}",
                descriptor_name="default_v1",
                values={"reg_pressure_bin": idx, "smem_bin": 0, "occupancy_bin": 0},
            )
            archive.insert(candidate_id=f"c{idx}", fitness=float(idx + 1), descriptor=descriptor, iteration=idx)

        rng = random.Random(42)
        selected = {archive.select_parent(rng) for _ in range(50)}
        self.assertTrue(selected.issubset({"c0", "c1", "c2", "c3"}))
        self.assertGreaterEqual(len(selected), 2)

    def test_ring_migration_imports_elites(self) -> None:
        axes = DescriptorAxes()
        islands = [
            IslandState(policy=policy, archive=MapElitesArchive(axes))
            for policy in default_island_policies()
        ]
        for idx, island in enumerate(islands):
            descriptor = Descriptor(
                run_id="run-3",
                candidate_id=f"seed-{idx}",
                descriptor_name="default_v1",
                values={"reg_pressure_bin": idx, "smem_bin": 0, "occupancy_bin": 0},
            )
            island.archive.insert(candidate_id=f"seed-{idx}", fitness=10.0 + idx, descriptor=descriptor, iteration=1)

        migrations = migrate_ring(islands, packet_size=1)
        self.assertTrue(migrations)
        for island in islands:
            self.assertGreaterEqual(len(island.imported_parent_ids), 1)


if __name__ == "__main__":
    unittest.main()
