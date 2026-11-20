"""macos27tool-runtime-fold: macos27tool-runtime's install step, on the fold-in payload row."""
import json
from pathlib import Path
import unittest

PATCH = Path(__file__).resolve().parents[1]
BASE = PATCH.parent/'macos27tool-runtime'


class Recipe(unittest.TestCase):
    def test_same_install_as_hao_runtime_on_its_own_row(self):
        mine = (PATCH/'src/kc-hook.sh').read_text().splitlines()[-1]
        base = (BASE/'src/kc-hook.sh').read_text().splitlines()[-1]
        self.assertEqual(mine, base.replace('hao_runtime', 'hao_runtime_fold'))
        self.assertEqual(json.loads((PATCH/'patch.json').read_text())['inputs'], ['hao_runtime_fold'])

    def test_only_in_the_fold_sets_in_hao_runtimes_place(self):
        sets = PATCH.parents[1]/'pipeline/sets'
        names = lambda p: [l.split()[0] for l in p.read_text().splitlines() if l.strip() and not l.startswith('#')]
        users = sorted(p.name for p in sets.glob('*.txt') if 'macos27tool-runtime-fold' in names(p))
        self.assertEqual(users, ['alpha-v0.1.0-fold.txt', 'dev-beta-candidate-fold.txt', 'dev-bs-test-fold.txt'])
        for name in users:
            self.assertNotIn('macos27tool-runtime', names(sets/name))


if __name__ == '__main__':
    unittest.main()
