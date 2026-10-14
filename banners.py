"""Wikimedia Commons destination photography, cached locally with attribution."""
import hashlib
import html
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get('HOLIDAY_BANNER_CACHE', Path(__file__).parent / 'banner-cache'))
USER_AGENT = 'HolidayPlanner/2.1 (https://github.com/ChimeraX-CMRX/holiday-planner)'
KINDS = {'recommended': 'skyline', 'cityscape': 'skyline', 'aerial': 'aerial', 'landmark': 'landmark', 'landscape': 'landscape', 'streets': 'street architecture'}
FONTS = {'skyline', 'heritage', 'editorial', 'modern'}
MEDIA_HOSTS = {'upload.wikimedia.org', 'thumb.wikimedia.org'}
LOCK = threading.RLock()
IMAGE_LOCKS = [threading.Lock() for _ in range(32)]

def init_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS banner_photos(id TEXT PRIMARY KEY,metadata TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS banner_searches(query TEXT PRIMARY KEY,results TEXT NOT NULL,created_at INTEGER NOT NULL)')
    existing = {r[1] for r in c.execute('PRAGMA table_info(destinations)')}
    for name, definition in [('banner_mode', "TEXT NOT NULL DEFAULT 'auto'"), ('banner_kind', "TEXT NOT NULL DEFAULT 'recommended'"), ('banner_photo', "TEXT NOT NULL DEFAULT ''"), ('banner_auto_photo', "TEXT NOT NULL DEFAULT ''"), ('banner_font', "TEXT NOT NULL DEFAULT 'skyline'"), ('banner_x', 'INTEGER NOT NULL DEFAULT 50'), ('banner_y', 'INTEGER NOT NULL DEFAULT 50')]:
        if name not in existing:
            c.execute(f'ALTER TABLE destinations ADD COLUMN {name} {definition}')

def plain(value):
    return html.unescape(re.sub('<[^>]+>', '', str(value or ''))).strip()

def valid_media(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == 'https' and parsed.hostname in MEDIA_HOSTS and not parsed.username and parsed.port in (None, 443)

class MediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not valid_media(newurl):
            raise ValueError('Image redirect is not a Wikimedia image')
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def photo(c, photo_id):
    row = c.execute('SELECT metadata FROM banner_photos WHERE id=?', (photo_id,)).fetchone()
    return json.loads(row[0]) if row else None

def public_photo(item):
    return {k: v for k, v in item.items() if k != 'remote_url'}

def saved_photos(c):
    rows = c.execute('SELECT metadata FROM banner_photos WHERE id IN (SELECT banner_photo FROM destinations UNION SELECT banner_auto_photo FROM destinations)')
    return [public_photo(json.loads(r[0])) for r in rows]

def commons_search(query):
    params = dict(action='query', format='json', generator='search', gsrsearch=query+' filetype:bitmap', gsrnamespace=6, gsrlimit=24, prop='imageinfo', iiprop='url|size|mime|extmetadata', iiurlwidth=1600)
    request = urllib.request.Request('https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params), headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=18) as response:
        payload = json.loads(response.read(3000000))
    if 'error' in payload:
        raise ValueError('Photo search is temporarily unavailable. Please try again.')
    found = []
    for page in sorted(payload.get('query', {}).get('pages', {}).values(), key=lambda p: p.get('index', 100)):
        info = (page.get('imageinfo') or [{}])[0]
        meta = info.get('extmetadata', {})
        value = lambda key: plain(meta.get(key, {}).get('value', ''))
        licence = value('LicenseShortName')
        licence_url = value('LicenseUrl')
        if licence.lower() in {'public domain', 'cc0'} and not licence_url:
            licence_url = 'https://creativecommons.org/publicdomain/zero/1.0/' if licence.lower() == 'cc0' else 'https://creativecommons.org/publicdomain/mark/1.0/'
        if not re.fullmatch(r'CC BY(?:-SA)? [1-4]\.0|CC0|Public domain', licence, re.I):
            continue
        if not licence_url.startswith('https://creativecommons.org/'):
            continue
        width, height = info.get('width', 0), info.get('height', 0)
        if info.get('mime') != 'image/jpeg' or width < 1000 or height < 1 or width/height < 1.25:
            continue
        remote = info.get('thumburl', '')
        if not valid_media(remote):
            continue
        title = page['title']
        if re.search(r'\b(map|locator|diagram|coat of arms|flag)\b', title, re.I):
            continue
        ident = hashlib.sha256(title.encode()).hexdigest()[:24]
        found.append(dict(id=ident, title=plain(title.removeprefix('File:')), artist=value('Artist') or 'See source for author', credit=value('Attribution') or value('Credit'), licence=licence, licence_url=licence_url, source_url='https://commons.wikimedia.org/wiki/'+urllib.parse.quote(title.replace(' ', '_')), image_url=f'/banner-images/{ident}.jpg', remote_url=remote, width=width, height=height))
    return found[:9]

def search(db, name, country, kind='recommended', query=''):
    if kind not in KINDS:
        raise ValueError('Choose a photo category')
    if not name or len(name)>120 or len(country)>120 or len(query)>160:
        raise ValueError('Enter a destination and a shorter photo search')
    # Match the place in the file title, not an unrelated museum/author credit.
    location = 'intitle:"'+name.replace('"', '')+'"'
    query = query.strip() or f'{location} {country} {KINDS[kind]}'
    with LOCK:
        with db() as c:
            cached = c.execute('SELECT * FROM banner_searches WHERE query=?', (query,)).fetchone()
            if cached and time.time()-cached['created_at'] < 7*86400:
                return [public_photo(p) for ident in json.loads(cached['results']) if (p := photo(c, ident))]
        found = commons_search(query)
        if not found:
            found = commons_search(f'{location} {country}')
        with db() as c:
            for item in found:
                c.execute('INSERT OR REPLACE INTO banner_photos VALUES(?,?)', (item['id'], json.dumps(item)))
            c.execute('INSERT OR REPLACE INTO banner_searches VALUES(?,?,?)', (query, json.dumps([p['id'] for p in found]), int(time.time())))
        return [public_photo(p) for p in found]

def automatic(db, destination_id):
    with db() as c:
        row = c.execute('SELECT * FROM destinations WHERE id=?', (destination_id,)).fetchone()
        if not row:
            raise ValueError('Destination not found')
        d = dict(row)
        existing = photo(c, d['banner_auto_photo'])
        if existing:
            return public_photo(existing)
    choices = search(db, d['name'], d['country'], d['banner_kind'])
    if not choices:
        return None
    with db() as c:
        # Never overwrite a newer location/category selection made while searching.
        c.execute('UPDATE destinations SET banner_auto_photo=? WHERE id=? AND name=? AND country=? AND banner_kind=? AND banner_auto_photo=?', (choices[0]['id'], destination_id, d['name'], d['country'], d['banner_kind'], ''))
    return choices[0]

def settings(c, payload, old=None):
    old = dict(old or {})
    result = {key: payload.get(key, old.get(key, default)) for key, default in dict(banner_mode='auto', banner_kind='recommended', banner_photo='', banner_font='skyline', banner_x=50, banner_y=50).items()}
    if any(not isinstance(result[k], str) for k in ('banner_mode', 'banner_kind', 'banner_font')) or result['banner_mode'] not in {'auto', 'photo', 'none'} or result['banner_kind'] not in KINDS or result['banner_font'] not in FONTS:
        raise ValueError('Choose a valid banner appearance')
    for key in ('banner_x', 'banner_y'):
        try:
            result[key] = int(result[key])
        except (ValueError, TypeError):
            raise ValueError('Choose a valid banner crop position')
        if not 0 <= result[key] <= 100:
            raise ValueError('Choose a valid banner crop position')
    if not isinstance(result['banner_photo'], str) or (result['banner_photo'] and not photo(c, result['banner_photo'])):
        raise ValueError('Choose a photograph from the search results')
    if result['banner_mode'] == 'photo' and not result['banner_photo']:
        raise ValueError('Choose a banner photograph first')
    result['banner_auto_photo'] = old.get('banner_auto_photo', '')
    if any(payload.get(k, old.get(k)) != old.get(k) for k in ('name', 'country', 'banner_kind')):
        result['banner_auto_photo'] = ''
    return result

def save_settings(c, item, values):
    c.execute('UPDATE destinations SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(), item))

def image(db, ident):
    if not re.fullmatch('[0-9a-f]{24}', ident):
        raise ValueError('Invalid photo')
    target = CACHE / (ident+'.jpg')
    with IMAGE_LOCKS[int(ident, 16) % len(IMAGE_LOCKS)]:
        if target.exists():
            return target.read_bytes()
        with db() as c:
            item = photo(c, ident)
        if not item or not valid_media(item['remote_url']):
            raise ValueError('Photo not found')
        req = urllib.request.Request(item['remote_url'], headers={'User-Agent': USER_AGENT})
        with urllib.request.build_opener(MediaRedirect()).open(req, timeout=25) as response:
            content = response.read(8*1024*1024+1)
        if len(content)>8*1024*1024 or not content.startswith(b'\xff\xd8\xff'):
            raise ValueError('Could not load this photograph')
        CACHE.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return content
