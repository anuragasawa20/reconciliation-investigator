import importlib
import sys
import unittest


class StartupLazyInitTests(unittest.TestCase):
    def test_graph_module_does_not_build_app_on_import(self):
        sys.modules.pop("graph.reconciliation_graph", None)
        module = importlib.import_module("graph.reconciliation_graph")
        self.assertIsNone(getattr(module, "app", None))


if __name__ == "__main__":
    unittest.main()
