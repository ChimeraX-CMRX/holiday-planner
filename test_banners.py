"""Offline regression checks: python3 -m unittest test_banners."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import banners


class BannerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        with self.db() as c:
            c.execute('CREATE TABLE destinations(id INTEGER PRIMARY KEY,name TEXT,country TEXT)')
            c.execute("INSERT INTO destinations VALUES(1,'Lisbon','Portugal')")
            banners.init_schema(c)
        self.item = dict(id='a'*24, title='Lisbon banner.jpg', remote_url='https://upload.wikimedia.org/test.jpg', image_url='/banner-images/'+('a'*24)+'.jpg')

    def tearDown(self):
        self.tmp.cleanup()

    def db(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def test_migration_preserves_destinations_and_is_repeatable(self):
        with self.db() as c:
            banners.init_schema(c)
            row = c.execute('SELECT * FROM destinations').fetchone()
            self.assertEqual(row['name'], 'Lisbon')
            self.assertEqual(row['banner_mode'], 'auto')

    def test_new_city_automatic_choice_and_search_are_cached(self):
        with patch.object(banners, 'commons_search', return_value=[self.item]) as lookup:
            first = banners.automatic(self.db, 1)
            self.assertEqual(first, banners.automatic(self.db, 1))
            self.assertEqual([first], banners.search(self.db, 'Lisbon', 'Portugal'))
            lookup.assert_called_once_with('intitle:"Lisbon" Portugal skyline')
            self.assertNotIn('remote_url', first)

    def test_settings_persist_and_location_change_resets_automatic_image(self):
        with self.db() as c:
            c.execute('INSERT INTO banner_photos VALUES(?,?)', (self.item['id'], json.dumps(self.item)))
            values = banners.settings(c, dict(banner_mode='photo', banner_photo=self.item['id'], banner_font='heritage', banner_x=20, banner_y=80))
            banners.save_settings(c, 1, values)
            row = c.execute('SELECT * FROM destinations WHERE id=1').fetchone()
            self.assertEqual(row['banner_y'], 80)
            self.assertEqual(banners.settings(c, {}, row)['banner_font'], 'heritage')
            old = dict(row, banner_auto_photo=self.item['id'])
            self.assertEqual(banners.settings(c, {'name':'Porto'}, old)['banner_auto_photo'], '')

    def test_invalid_settings_rejected(self):
        with self.db() as c:
            for payload in ({'banner_mode':[]}, {'banner_x':101}, {'banner_mode':'photo'}, {'banner_photo':'https://example.com/a.jpg'}, {'banner_font':'unknown'}):
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    banners.settings(c, payload)

    def test_images_only_from_wikimedia(self):
        self.assertTrue(banners.valid_media('https://thumb.wikimedia.org/a.jpg'))
        for url in ('http://upload.wikimedia.org/a.jpg', 'https://upload.wikimedia.org.evil.test/a.jpg', 'https://localhost/a.jpg', 'https://user@upload.wikimedia.org/a.jpg'):
            self.assertFalse(banners.valid_media(url))
        with self.assertRaises(ValueError):
            banners.image(self.db, '../../secret')


if __name__ == '__main__':
    unittest.main()
