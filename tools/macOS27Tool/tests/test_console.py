"""src/console.py's colour: only on a terminal, never with NO_COLOR, TERM=dumb or --log json; events stay plain."""
import io
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'src'))
import console as CON      # noqa: E402
import installer as INST   # noqa: E402

ESC = '\033['
TTY_ENV = {'TERM': 'xterm-256color', 'M27_THEME': 'dark'}


class Tty(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return True


class Pipe(io.StringIO):
    encoding = 'utf-8'

    def isatty(self):
        return False


def run(level='steps', out=None, env=None):
    out = out if out is not None else Tty()
    err = Tty() if isinstance(out, Tty) else Pipe()
    c = CON.Console(level, out=out, err=err, ask=lambda _: 'ERASE', env=TTY_ENV if env is None else env)
    c.banner('macOS27Tool create-installer  macOS 27 26B131 for MacBookPro9,2  (proven model %s)' % c.ok)
    c.steps(3)
    c.step('Download', note='every file checked against release.json')
    c.item('%s %-10s %s' % (c.paint.origin('apple', 6), 'ipsw', 'sha256'), 'steps', ok=True)
    c.note('not the proven combination')
    c.print('disk  disk4 (USB, 32 GB)  everything on it will be erased')
    c.confirm('Everything on disk4 (USB, 32 GB) will be erased.', 'ERASE')
    c.warning('the cache is nearly full')
    c.done('the USB stick is ready')
    c.stopped(CON.Stopped('a download failed', why='m27 uses only pinned files', options=['run it again'],
                          untouched='the stick was not touched'))
    return c, out.getvalue(), err.getvalue()


class Colour(unittest.TestCase):
    def test_terminal_gets_the_palette(self):
        c, out, err = run()
        self.assertIn(ESC + '1mmacOS27Tool create-installer' + ESC + '0m', out)        # bold name
        self.assertIn(ESC + '1m[1/3] Download' + ESC + '0m', out)              # bold step header
        self.assertIn(ESC + '38;5;245mevery file checked', out)                         # dim note
        self.assertIn(ESC + '38;5;78msha256 ' + ESC + '0m' + ESC + '38;5;78m✓', out)   # green "sha256 ✓"
        self.assertIn(ESC + '38;5;251mApple ', out)                              # origin tag, Apple grey
        self.assertIn(ESC + '38;5;221mnote', out)                                # yellow note
        self.assertIn(ESC + '38;5;221meverything on it will be erased', out)
        self.assertIn(ESC + '38;5;221mEverything on disk4 (USB, 32 GB) will be erased', out)
        self.assertIn(ESC + '38;5;221mwarning:', err)
        self.assertIn(ESC + '1;38;5;210m  STOPPED  ', err)                         # the red panel's title                           # red STOPPED
        self.assertIn(ESC + '38;5;78m✓' + ESC + '0m the USB stick is ready', out)

    def test_events_hold_no_colour(self):
        c, _, _ = run()
        self.assertNotIn(ESC, json.dumps(c.events))
        self.assertEqual(c.events[0]['text'], 'macOS27Tool create-installer  macOS 27 26B131 for MacBookPro9,2  (proven model ✓)')

    def test_pipe_no_color_dumb_and_json_are_plain(self):
        for level, out, env in (('steps', Pipe(), TTY_ENV), ('steps', Tty(), dict(TTY_ENV, NO_COLOR='1')),
                                ('steps', Tty(), {'TERM': 'dumb'}), ('json', Tty(), TTY_ENV),
                                ('edits', Pipe(), TTY_ENV)):
            with self.subTest(level=level, out=type(out).__name__, env=env):
                _, text, err = run(level, out, env)
                self.assertNotIn(ESC, text + err)
        lines = run('json', Tty())[1].splitlines()   # events, plus the question confirm asks with no --confirm
        self.assertEqual([l for l in lines if not l.startswith('{')], ['Everything on disk4 (USB, 32 GB) will be erased.'])
        for line in lines[:1]:
            json.loads(line)

    def test_empty_no_color_does_not_disable(self):
        self.assertTrue(CON.colour_for(Tty(), dict(TTY_ENV, NO_COLOR='')))

    def test_eight_colour_terminal(self):
        _, out, _ = run(env={'TERM': 'xterm'})
        self.assertIn(ESC + '32m✓', out)
        self.assertNotIn('38;5;', out)

    def test_light_background_palette(self):
        # the Recovery's Terminal is white: no plain yellow 226/11 or white, every colour is a darker 256 value
        for kind, sgr in CON.SGR_256.items():
            self.assertNotIn(sgr, ('38;5;226', '38;5;11', '38;5;15', '38;5;231'), kind)

    def test_download_origins(self):
        self.assertEqual(INST.origin_of({'row': 'ipsw_27_0', 'name': 'UniversalMac_27.2.1_26B131_Restore.ipsw',
                                         'url': 'https://updates.cdn-apple.com/x.ipsw'}), 'apple')
        self.assertEqual(INST.origin_of({'row': 'installer', 'name': 'InstallAssistant-25G342.pkg',
                                         'url': 'https://swcdn.apple.com/x.pkg'}), 'tahoe')
        self.assertEqual(INST.origin_of({'row': 'psp_2.0.5', 'name': 'PatcherSupportPkg-2.0.5.dmg',
                                         'url': 'https://github.com/dortania/PatcherSupportPkg/x'}), 'oclp')
        self.assertEqual(INST.origin_of({'row': 'ripatch', 'name': 'ripatch', 'kind': 'asset',
                                         'url': 'http://127.0.0.1:8765/ripatch'}), 'm27')
        self.assertEqual(INST.origin_of({'row': 'python_site_idna', 'name': 'idna.whl',
                                         'url': 'https://files.pythonhosted.org/x'}), '')


if __name__ == '__main__':
    unittest.main()
