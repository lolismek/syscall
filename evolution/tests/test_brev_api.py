from __future__ import annotations

import unittest

from kernelswarm.brev_api import BrevClient


class BrevApiTests(unittest.TestCase):
    def test_parse_ls_output(self) -> None:
        sample = """
You have 1 instances in Org EXAMPLE
 NAME              STATUS   BUILD      SHELL  ID         MACHINE
 kernelswarm-eval  RUNNING  COMPLETED  READY  pqa5huoq8  n1-highmem-4:nvidia-tesla-t4:1 (gpu)
"""
        rows = BrevClient._parse_ls(sample)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.name, "kernelswarm-eval")
        self.assertEqual(row.status, "RUNNING")
        self.assertEqual(row.shell, "READY")
        self.assertEqual(row.instance_id, "pqa5huoq8")
        self.assertIn("nvidia-tesla-t4", row.machine)


if __name__ == "__main__":
    unittest.main()
