"""Behavioral regression tests; all persistence uses temporary directories."""

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from termin import ledger, paths, storage, terminal
from termin.model import Model
from termin.providers import berlin, doctolib
from termin.ui import dashboard, setup


def office(name='Mitte', book='https://example.test/book'):
    return {'sid': 1, 'name': name, 'district': name, 'book': book, 'tag': None}


class SelectionTests(unittest.TestCase):
    def test_service_aliases_ids_and_urls(self):
        for spec, expected in [('passport', 121151), ('anmeldung', 120686),
                               ('121151', 121151),
                               ('https://service.berlin.de/dienstleistung/121151/', 121151),
                               ('unknown', None)]:
            with self.subTest(spec=spec):
                self.assertEqual(berlin.resolve_service(spec), expected)

    def test_office_matching_deduplicates_and_skips_unbookable(self):
        offices = [office('Treptow-Köpenick'), office('Pankow', None)]
        with contextlib.redirect_stdout(io.StringIO()):
            selected = setup.match_offices(offices, ['treptow-koepenick', '1', 'all', '2'])
        self.assertEqual(selected, offices[:1])

    def test_url_cleaning_removes_patient_and_preserves_booking_parameters(self):
        cleaned = doctolib.clean_wizard_url(
            'https://www.doctolib.de/doctor/booking?masterPatientUuid=private'
            '&motiveIds%5B%5D=42&placeId=7#fragment')
        self.assertEqual(parse_qs(urlsplit(cleaned).query),
                         {'motiveIds[]': ['42'], 'placeId': ['7']})
        self.assertEqual(urlsplit(cleaned).fragment, '')

    def test_feed_accepts_existing_slot_representations(self):
        body = json.dumps({'availabilities': [{'slots': [
            '2099-10-20T11:30:00+02:00',
            {'start_date': '2099-10-20T09:00:00+02:00'},
            {'startDate': '2099-10-20T11:30:00+02:00'},
        ]}], 'next_slot': '2099-11-01T08:00:00+01:00'})
        self.assertEqual(doctolib.parse_doctolib_feed(body),
                         (['2099-10-20T09:00', '2099-10-20T11:30'], '2099-11-01'))


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now().timestamp()
        self.day = (datetime.now() + timedelta(days=30)).strftime('%Y-%m-%d')
        self.slot = self.day + 'T09:00'
        self.watch, self.slots = {}, {}

    def test_baseline_same_day_additions_and_reappearing_day(self):
        self.assertEqual(ledger.dl_apply_poll(self.watch, self.slots,
                                             [self.slot], self.now), ([], []))
        self.assertTrue(self.watch['seeded'])
        self.assertEqual(self.slots[self.slot]['first'], self.now - ledger.DL_NEW_SECS)
        extra = self.day + 'T10:00'
        self.assertEqual(ledger.dl_apply_poll(self.watch, self.slots,
                                             [self.slot, extra], self.now + 1), ([extra], []))
        ledger.dl_apply_poll(self.watch, self.slots, [], self.now + 2)
        self.assertEqual(self.slots[self.slot]['gone'], self.now + 2)
        self.assertEqual(ledger.dl_apply_poll(self.watch, self.slots,
                                             [self.slot], self.now + 3), ([self.slot], [self.day]))

    def test_expired_taken_slots_are_removed(self):
        self.slots[self.slot] = {'first': self.now - 100, 'gone': self.now - ledger.DL_GONE_SECS - 1}
        ledger.update_dl_ledger(self.slots, [], self.now)
        self.assertEqual(self.slots, {})

    def test_summary_copies_ledger_and_preserves_next_date(self):
        self.slots[self.slot] = {'first': self.now, 'gone': None}
        state = {}
        ledger._dl_summarize(state, self.slots, '2099-11-01', self.now)
        self.assertEqual(state['status'], 'available')
        self.assertEqual(state['summary'], '1 days · 1 slots · 1 new')
        self.assertEqual(state['next'], '01.11.2099')
        self.slots[self.slot]['gone'] = self.now
        self.assertIsNone(state['ledger'][self.slot]['gone'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, filename in [('CONFIG_FILE', 'config.json'),
                               ('DOCTOLIB_LEDGER_FILE', 'ledger.json')]:
            p = patch.object(storage, name, self.root / filename)
            p.start()
            self.addCleanup(p.stop)

    def test_modes_preserve_each_others_configuration(self):
        storage.save_config(121151, 'Passport', [office()])
        storage.save_doctolib([office('Doctor')])
        storage.save_mode('doctolib')
        cfg = storage.load_config()
        self.assertEqual(cfg['service_id'], 121151)
        self.assertEqual(cfg['doctolib'][0]['name'], 'Doctor')
        self.assertEqual(cfg['mode'], 'doctolib')

    def test_legacy_office_configuration_is_upgraded(self):
        storage.CONFIG_FILE.write_text(json.dumps({
            'service_id': 121151, 'offices': [{'name': 'Legacy', 'tag': 123}]}))
        saved = storage.load_config()['offices'][0]
        self.assertFalse(saved['paused'])
        self.assertIn('id=123&anliegen[]=121151', saved['book'])

    def test_missing_and_malformed_files_keep_existing_fallbacks(self):
        self.assertIsNone(storage.load_config())
        self.assertEqual(storage.load_ledger(), {})
        storage.CONFIG_FILE.write_text('{')
        storage.DOCTOLIB_LEDGER_FILE.write_text('{')
        self.assertIsNone(storage.load_config())
        self.assertEqual(storage.load_ledger(), {})

    def test_ledger_roundtrip(self):
        data = {'https://example.test/book': {'2099-10-20T09:00': {'first': 123, 'gone': None}}}
        storage.save_ledger(data)
        self.assertEqual(storage.load_ledger(), data)

    def test_runtime_locations_stay_beside_launcher(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(paths.SOURCE_FILE, root / 'termin_monitor.py')
        for path in (paths.CONFIG_FILE, paths.LOG_FILE, paths.DEBUG_DIR,
                     paths.DOCTOLIB_LEDGER_FILE, paths.DL_PROFILE_DIR):
            self.assertEqual(path.parent, root)


class DashboardTests(unittest.TestCase):
    def test_reset_preserves_existing_row_and_initializes_new_row(self):
        model = Model(121151, 'Passport', [office()], 240)
        model.state['Mitte']['dates'] = ['20.10.2099']
        model.offices.append(office('Spandau'))
        model.reset_rows()
        self.assertEqual(model.state['Mitte']['dates'], ['20.10.2099'])
        self.assertEqual(model.state['Spandau']['status'], 'pending')

    def test_both_modes_render_without_a_terminal(self):
        for mode in ('berlin', 'doctolib'):
            with self.subTest(mode=mode):
                model = Model(121151, 'Appointments', [office()], 240, mode)
                output = io.StringIO()
                with patch.object(dashboard, 'IS_TTY', False), patch.object(terminal, 'IS_TTY', False), contextlib.redirect_stdout(output):
                    dashboard.render_live(model, live=False)
                    dashboard.print_summary(model)
                self.assertIn('Appointments', output.getvalue())
                self.assertIn('Mitte', output.getvalue())
                self.assertNotIn('\033[', output.getvalue())


if __name__ == '__main__':
    unittest.main()


class FilterDlSlotsTests(unittest.TestCase):
    def test_time_window_and_horizon(self):
        from datetime import date
        from termin.ledger import filter_dl_slots
        slots = ["2026-11-07T16:59", "2026-11-07T17:00", "2026-11-10T20:00",
                 "2026-11-10T20:01", "2026-11-21T18:00", "2026-11-20T18:00"]
        got = filter_dl_slots(slots, "17:00", "20:00", 14, today=date(2026, 11, 6))
        self.assertEqual(got, ["2026-11-07T17:00", "2026-11-10T20:00", "2026-11-20T18:00"])
