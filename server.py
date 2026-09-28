#!/usr/bin/env python3
"""Dependency-free reusable holiday planner service."""
from __future__ import annotations
import datetime as dt, html, json, math, os, re, sqlite3, time
import urllib.error, urllib.parse, urllib.request
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT=Path(__file__).resolve().parent
DB_PATH=Path(os.environ.get('HOLIDAY_DB_PATH',ROOT/'holiday-planner.sqlite3')).expanduser()
TILE_CACHE=Path(os.environ.get('HOLIDAY_TILE_CACHE',ROOT/'tile-cache')).expanduser()
LISTEN_HOST=os.environ.get('HOLIDAY_HOST','0.0.0.0'); LISTEN_PORT=int(os.environ.get('HOLIDAY_PORT','7070')); MAX_BODY=32768
CATEGORIES={'hotel','food','activity'}; PERIODS={'morning','afternoon','evening','night'}; LIST_TYPES={'packing','todo'}
HOLIDAY_STATUSES={'planning','booked'}; TRAVEL_STATUSES={'planned','booked'}
TRAVEL_TYPES={'flight','train','ferry','coach','car','taxi','other'}
THEMES={'city','beach','wildlife','winter','roadtrip','teal','blue','purple','orange','green','mono','custom'}
GOOGLE_HOSTS={'google.com','www.google.com','maps.google.com','maps.app.goo.gl','goo.gl','google.co.uk','www.google.co.uk'}
SHORT_GOOGLE_HOSTS={'maps.app.goo.gl','goo.gl'}
def cols(c,table): return {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
def db():
 c=sqlite3.connect(DB_PATH); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON')
 c.executescript('''
 CREATE TABLE IF NOT EXISTS holidays(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,subtitle TEXT NOT NULL DEFAULT '',start_date TEXT NOT NULL,end_date TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'planning',theme TEXT NOT NULL DEFAULT 'city',primary_color TEXT,accent_color TEXT,created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS destinations(id INTEGER PRIMARY KEY AUTOINCREMENT,holiday_id INTEGER NOT NULL REFERENCES holidays(id) ON DELETE CASCADE,slug TEXT NOT NULL,name TEXT NOT NULL,country TEXT NOT NULL,country_code TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,latitude REAL NOT NULL,longitude REAL NOT NULL,sort_order INTEGER NOT NULL DEFAULT 0,UNIQUE(holiday_id,slug));
 CREATE TABLE IF NOT EXISTS places(id INTEGER PRIMARY KEY AUTOINCREMENT,city TEXT,category TEXT NOT NULL,name TEXT NOT NULL,url TEXT,latitude REAL,longitude REAL,created_at INTEGER NOT NULL,schedule_date TEXT,schedule_period TEXT,source_type TEXT NOT NULL DEFAULT 'map',destination_id INTEGER);
 CREATE TABLE IF NOT EXISTS travel(id INTEGER PRIMARY KEY AUTOINCREMENT,holiday_id INTEGER NOT NULL REFERENCES holidays(id) ON DELETE CASCADE,travel_type TEXT NOT NULL,service_number TEXT NOT NULL DEFAULT '',from_name TEXT NOT NULL,to_name TEXT NOT NULL,departure_at TEXT NOT NULL,arrival_at TEXT,status TEXT NOT NULL DEFAULT 'planned',terminal TEXT NOT NULL DEFAULT '',booking_reference TEXT NOT NULL DEFAULT '',notes TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS info_cards(id INTEGER PRIMARY KEY AUTOINCREMENT,destination_id INTEGER NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,title TEXT NOT NULL,body TEXT NOT NULL DEFAULT '',link_url TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL);
 CREATE TABLE IF NOT EXISTS checklist_items(id INTEGER PRIMARY KEY AUTOINCREMENT,holiday_id INTEGER NOT NULL REFERENCES holidays(id) ON DELETE CASCADE,list_type TEXT NOT NULL,item_text TEXT NOT NULL,completed INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL);
 ''')
 pc=cols(c,'places')
 for name,definition in [('schedule_date','TEXT'),('schedule_period','TEXT'),('source_type',"TEXT NOT NULL DEFAULT 'map'"),('destination_id','INTEGER')]:
  if name not in pc: c.execute(f'ALTER TABLE places ADD COLUMN {name} {definition}')
 if c.execute('SELECT count(*) FROM places WHERE destination_id IS NULL').fetchone()[0]:
  hid=c.execute('SELECT id FROM holidays ORDER BY id LIMIT 1').fetchone()[0]
  for r in c.execute('SELECT id,slug FROM destinations WHERE holiday_id=?',(hid,)):
   c.execute('UPDATE places SET destination_id=? WHERE city=? AND destination_id IS NULL',(r['id'],r['slug']))
 c.commit(); return c

def clean(v,label,maxlen=120,required=True):
 s=str(v or '').strip()
 if required and not s: raise ValueError(f'Enter {label}')
 if len(s)>maxlen: raise ValueError(f'Keep {label} to {maxlen} characters or fewer')
 return s
def date_value(v,label):
 try:return dt.date.fromisoformat(str(v or '').strip()).isoformat()
 except ValueError as e: raise ValueError(f'Choose a valid {label}') from e
def datetime_value(v,label,optional=False):
 s=str(v or '').strip()
 if optional and not s:return None
 try:return dt.datetime.fromisoformat(s).isoformat(timespec='minutes')
 except ValueError as e: raise ValueError(f'Choose a valid {label}') from e
def web_url(v):
 s=clean(v,'link',2048,False)
 if not s:return ''
 u=urllib.parse.urlparse(s)
 if u.scheme not in {'http','https'} or not u.netloc:raise ValueError('Use a complete http or https link')
 return s
def body(h):
 n=int(h.headers.get('Content-Length','0'))
 if not 0<n<=MAX_BODY: raise ValueError('Invalid request size')
 p=json.loads(h.rfile.read(n))
 if not isinstance(p,dict):raise ValueError('Invalid request')
 return p
def google_host(url):
 host=(urllib.parse.urlparse(url).hostname or '').lower().rstrip('.')
 return host in GOOGLE_HOSTS or host.endswith('.google.com')
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,req,fp,code,msg,headers,newurl):return None
def resolve_url(url):
 if not google_host(url):raise ValueError('Please use a Google Maps link')
 headers={'User-Agent':'Mozilla/5.0 HolidayPlanner/2.0'}
 try:
  if (urllib.parse.urlparse(url).hostname or '').lower() in SHORT_GOOGLE_HOSTS:
   try:urllib.request.build_opener(NoRedirect()).open(urllib.request.Request(url,headers=headers),timeout=10)
   except urllib.error.HTTPError as r:
    if r.code in {301,302,303,307,308}:url=urllib.parse.urljoin(url,r.headers.get('Location',''))
  with urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=10) as r:return r.geturl(),r.read(300000).decode('utf-8','replace')
 except (urllib.error.URLError,TimeoutError) as e:raise ValueError('That Google Maps link could not be opened') from e
def coords_from(text):
 text=urllib.parse.unquote(text)
 for p in (r'!3d(-?\d{1,2}(?:\.\d+))!4d(-?\d{1,3}(?:\.\d+))',r'@(-?\d{1,2}(?:\.\d+)?),(-?\d{1,3}(?:\.\d+)?)',r'[?&](?:query|q|ll)=(-?\d{1,2}(?:\.\d+)?)(?:%2C|,)(-?\d{1,3}(?:\.\d+)?)',r'"latitude"\s*:\s*(-?\d{1,2}(?:\.\d+)).{0,100}?"longitude"\s*:\s*(-?\d{1,3}(?:\.\d+))'):
  m=re.search(p,text,re.I|re.S)
  if m:
   a,b=map(float,m.groups())
   if -90<=a<=90 and -180<=b<=180:return a,b
 return None
def place_name(url,page):
 m=re.search(r'/maps/(?:place|search)/([^/@?]+)',urllib.parse.unquote(url))
 if m:return urllib.parse.unquote_plus(m.group(1)).strip()[:120]
 m=re.search(r'<title>(.*?)</title>',page,re.I|re.S)
 return html.unescape(re.sub(r'\s*[-–|]\s*Google Maps.*$','',m.group(1))).strip()[:120] if m else 'Saved place'
def geocode(name,country):
 q=urllib.parse.urlencode({'q':f'{name}, {country}','format':'json','limit':1})
 req=urllib.request.Request(f'https://nominatim.openstreetmap.org/search?{q}',headers={'User-Agent':'HolidayPlanner/2.0 personal planner'})
 try:
  with urllib.request.urlopen(req,timeout=12) as r:result=json.load(r)
  if not result:raise ValueError('That destination could not be found on the map')
  return float(result[0]['lat']),float(result[0]['lon'])
 except (urllib.error.URLError,TimeoutError,KeyError,TypeError) as e:raise ValueError('That destination could not be found on the map') from e
def state(c):return {'holidays':[dict(r) for r in c.execute('SELECT * FROM holidays ORDER BY start_date,id')],'destinations':[dict(r) for r in c.execute('SELECT * FROM destinations ORDER BY holiday_id,sort_order,start_date,id')],'places':[dict(r) for r in c.execute('SELECT id,destination_id,category,name,url,latitude,longitude,schedule_date,schedule_period,source_type FROM places WHERE destination_id IS NOT NULL ORDER BY created_at,id')],'travel':[dict(r) for r in c.execute('SELECT * FROM travel ORDER BY holiday_id,departure_at,id')],'info_cards':[dict(r) for r in c.execute('SELECT * FROM info_cards ORDER BY created_at,id')],'checklist_items':[dict(r) for r in c.execute('SELECT * FROM checklist_items ORDER BY completed,created_at,id')]}

class Handler(SimpleHTTPRequestHandler):
 server_version='HolidayPlanner/2.0'
 def end_headers(self):
  for k,v in [('X-Content-Type-Options','nosniff'),('X-Frame-Options','DENY'),('Referrer-Policy','strict-origin-when-cross-origin'),('Permissions-Policy','camera=(), microphone=(), geolocation=()'),('Content-Security-Policy',"default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://cdn.jsdelivr.net; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")]:self.send_header(k,v)
  super().end_headers()
 def translate_path(self,path):
  p=(ROOT/(urllib.parse.urlparse(path).path.lstrip('/') or 'index.html')).resolve();return str(p if ROOT in p.parents or p==ROOT else ROOT/'404')
 def respond(self,payload,status=200):
  data=json.dumps(payload,separators=(',',':')).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data)
 def do_GET(self):
  path=urllib.parse.urlparse(self.path).path;m=re.fullmatch(r'/tiles/(\d{1,2})/(\d+)/(\d+)\.png',path)
  if m:
   z,x,y=map(int,m.groups())
   if z>19 or x>=2**z or y>=2**z:self.send_error(400);return
   target=TILE_CACHE/str(z)/str(x)/f'{y}.png'
   try:
    if not target.exists():
     target.parent.mkdir(parents=True,exist_ok=True);req=urllib.request.Request(f'https://tile.openstreetmap.org/{z}/{x}/{y}.png',headers={'User-Agent':'HolidayPlanner/2.0 personal planner'})
     with urllib.request.urlopen(req,timeout=15) as r:data=r.read(1000000)
     if not data.startswith(b'\x89PNG'):raise ValueError('Unexpected tile response')
     tmp=target.with_suffix('.tmp');tmp.write_bytes(data);tmp.replace(target)
    data=target.read_bytes();self.send_response(200);self.send_header('Content-Type','image/png');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','public, max-age=604800');self.end_headers();self.wfile.write(data)
   except (OSError,ValueError,urllib.error.URLError) as e:self.send_error(502,str(e))
   return
  if path=='/api/state':
   with db() as c:self.respond(state(c))
   return
  super().do_GET()
 def do_POST(self):
  path=urllib.parse.urlparse(self.path).path
  try:
   p=body(self)
   with db() as c:
    if path=='/api/holidays':
     start,end=date_value(p.get('start_date'),'start date'),date_value(p.get('end_date'),'end date');status=p.get('status','planning');theme=p.get('theme','city')
     if end<start:raise ValueError('The holiday end must be after its start')
     if status not in HOLIDAY_STATUSES or theme not in THEMES:raise ValueError('Invalid holiday option')
     item=c.execute('INSERT INTO holidays(name,subtitle,start_date,end_date,status,theme,primary_color,accent_color,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(clean(p.get('name'),'holiday name'),clean(p.get('subtitle'),'subtitle',180,False),start,end,status,theme,clean(p.get('primary_color'),'primary colour',20,False) or None,clean(p.get('accent_color'),'accent colour',20,False) or None,int(time.time()))).lastrowid
    elif path=='/api/destinations':
     hid=int(p.get('holiday_id',0));h=c.execute('SELECT * FROM holidays WHERE id=?',(hid,)).fetchone()
     if not h:raise ValueError('Holiday not found')
     name,country=clean(p.get('name'),'destination'),clean(p.get('country'),'country');start,end=date_value(p.get('start_date'),'arrival date'),date_value(p.get('end_date'),'departure date')
     if end<start or start<h['start_date'] or end>h['end_date']:raise ValueError('Destination dates must fall within the holiday')
     lat,lon=geocode(name,country);base=re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-') or 'destination';slug=base;n=2
     while c.execute('SELECT 1 FROM destinations WHERE holiday_id=? AND slug=?',(hid,slug)).fetchone():slug=f'{base}-{n}';n+=1
     order=c.execute('SELECT COALESCE(MAX(sort_order),0)+1 FROM destinations WHERE holiday_id=?',(hid,)).fetchone()[0]
     item=c.execute('INSERT INTO destinations(holiday_id,slug,name,country,country_code,start_date,end_date,latitude,longitude,sort_order) VALUES(?,?,?,?,?,?,?,?,?,?)',(hid,slug,name,country,clean(p.get('country_code'),'two-letter country code',2).upper(),start,end,lat,lon,order)).lastrowid
    elif path=='/api/travel':
     hid=int(p.get('holiday_id',0));typ=p.get('travel_type');status=p.get('status','planned')
     if not c.execute('SELECT 1 FROM holidays WHERE id=?',(hid,)).fetchone() or typ not in TRAVEL_TYPES or status not in TRAVEL_STATUSES:raise ValueError('Invalid journey')
     dep=datetime_value(p.get('departure_at'),'departure');arr=datetime_value(p.get('arrival_at'),'arrival',True)
     if arr and arr<dep:raise ValueError('Arrival must be after departure')
     item=c.execute('INSERT INTO travel(holiday_id,travel_type,service_number,from_name,to_name,departure_at,arrival_at,status,terminal,booking_reference,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(hid,typ,clean(p.get('service_number'),'service number',40,False),clean(p.get('from_name'),'departure place'),clean(p.get('to_name'),'arrival place'),dep,arr,status,clean(p.get('terminal'),'station or terminal',100,False),clean(p.get('booking_reference'),'booking reference',80,False),clean(p.get('notes'),'notes',500,False),int(time.time()))).lastrowid
    elif path=='/api/places':
     did=int(p.get('destination_id',0));category=str(p.get('category',''));d=c.execute('SELECT * FROM destinations WHERE id=?',(did,)).fetchone()
     if not d or category not in CATEGORIES:raise ValueError('Invalid destination or category')
     idea=clean(p.get('name'),'idea',120,False)
     if idea:
      if category!='activity':raise ValueError('Free text is only available for things to see and do')
      item=c.execute("INSERT INTO places(city,destination_id,category,name,created_at,source_type) VALUES(?,?,?,?,?,'text')",(d['slug'],did,category,idea,int(time.time()))).lastrowid
     else:
      url=clean(p.get('url'),'Google Maps link',2048);final,page=resolve_url(url);xy=coords_from(final) or coords_from(page)
      if not xy or not all(math.isfinite(v) for v in xy):raise ValueError('I could not find coordinates in that Google Maps link')
      item=c.execute("INSERT INTO places(city,destination_id,category,name,url,latitude,longitude,created_at,source_type) VALUES(?,?,?,?,?,?,?,?,'map')",(d['slug'],did,category,place_name(final,page),final,*xy,int(time.time()))).lastrowid
    elif path=='/api/info-cards':
     did=int(p.get('destination_id',0))
     if not c.execute('SELECT 1 FROM destinations WHERE id=?',(did,)).fetchone():raise ValueError('Destination not found')
     item=c.execute('INSERT INTO info_cards(destination_id,title,body,link_url,created_at) VALUES(?,?,?,?,?)',(did,clean(p.get('title'),'card title'),clean(p.get('body'),'information',4000),web_url(p.get('link_url')),int(time.time()))).lastrowid
    elif path=='/api/checklist-items':
     hid=int(p.get('holiday_id',0));typ=str(p.get('list_type',''))
     if typ not in LIST_TYPES or not c.execute('SELECT 1 FROM holidays WHERE id=?',(hid,)).fetchone():raise ValueError('Invalid checklist')
     item=c.execute('INSERT INTO checklist_items(holiday_id,list_type,item_text,created_at) VALUES(?,?,?,?)',(hid,typ,clean(p.get('item_text'),'list item',240),int(time.time()))).lastrowid
    else:self.send_error(404);return
   self.respond({'id':item},201)
  except (ValueError,json.JSONDecodeError,sqlite3.IntegrityError) as e:self.respond({'error':str(e)},400)
 def do_PATCH(self):
  path=urllib.parse.urlparse(self.path).path;m=re.fullmatch(r'/api/(holidays|destinations|places|travel|info-cards|checklist-items)/(\d+)',path)
  if not m:self.send_error(404);return
  try:
   p=body(self);kind,item=m.group(1),int(m.group(2))
   with db() as c:
    if kind=='places':
     row=c.execute('SELECT p.*,d.start_date,d.end_date FROM places p JOIN destinations d ON d.id=p.destination_id WHERE p.id=?',(item,)).fetchone()
     if not row:raise ValueError('Place not found')
     day=str(p.get('schedule_date','')).strip() or None;period=str(p.get('schedule_period','')).strip() or None
     if (day is None)!=(period is None) or (day and (not row['start_date']<=day<=row['end_date'] or period not in PERIODS)):raise ValueError('Choose a valid day and time for this destination')
     c.execute('UPDATE places SET schedule_date=?,schedule_period=? WHERE id=?',(day,period,item))
    elif kind=='info-cards':
     row=c.execute('SELECT * FROM info_cards WHERE id=?',(item,)).fetchone()
     if not row:raise ValueError('Information card not found')
     c.execute('UPDATE info_cards SET title=?,body=?,link_url=? WHERE id=?',(clean(p.get('title',row['title']),'card title'),clean(p.get('body',row['body']),'information',4000),web_url(p.get('link_url',row['link_url'])),item))
    elif kind=='checklist-items':
     row=c.execute('SELECT * FROM checklist_items WHERE id=?',(item,)).fetchone()
     if not row:raise ValueError('Checklist item not found')
     completed=1 if p.get('completed',bool(row['completed'])) else 0
     c.execute('UPDATE checklist_items SET item_text=?,completed=? WHERE id=?',(clean(p.get('item_text',row['item_text']),'list item',240),completed,item))
    elif kind=='holidays':
     row=c.execute('SELECT * FROM holidays WHERE id=?',(item,)).fetchone()
     if not row:raise ValueError('Holiday not found')
     start=date_value(p.get('start_date',row['start_date']),'start date');end=date_value(p.get('end_date',row['end_date']),'end date');status=p.get('status',row['status']);theme=p.get('theme',row['theme'])
     if end<start:raise ValueError('The holiday end must be after its start')
     if status not in HOLIDAY_STATUSES or theme not in THEMES:raise ValueError('Invalid holiday option')
     c.execute('UPDATE holidays SET name=?,subtitle=?,start_date=?,end_date=?,status=?,theme=?,primary_color=?,accent_color=? WHERE id=?',(clean(p.get('name',row['name']),'holiday name'),clean(p.get('subtitle',row['subtitle']),'subtitle',180,False),start,end,status,theme,clean(p.get('primary_color',row['primary_color']),'primary colour',20,False) or None,clean(p.get('accent_color',row['accent_color']),'accent colour',20,False) or None,item))
    elif kind=='destinations':
     row=c.execute('SELECT d.*,h.start_date holiday_start,h.end_date holiday_end FROM destinations d JOIN holidays h ON h.id=d.holiday_id WHERE d.id=?',(item,)).fetchone()
     if not row:raise ValueError('Destination not found')
     start=date_value(p.get('start_date',row['start_date']),'arrival date');end=date_value(p.get('end_date',row['end_date']),'departure date')
     if end<start or start<row['holiday_start'] or end>row['holiday_end']:raise ValueError('Destination dates must fall within the holiday')
     name=clean(p.get('name',row['name']),'destination');country=clean(p.get('country',row['country']),'country')
     lat,lon=(geocode(name,country) if name!=row['name'] or country!=row['country'] else (row['latitude'],row['longitude']))
     c.execute('UPDATE destinations SET name=?,country=?,country_code=?,start_date=?,end_date=?,latitude=?,longitude=? WHERE id=?',(name,country,clean(p.get('country_code',row['country_code']),'country code',2).upper(),start,end,lat,lon,item))
    else:
     row=c.execute('SELECT * FROM travel WHERE id=?',(item,)).fetchone()
     if not row:raise ValueError('Journey not found')
     status=p.get('status',row['status']);typ=p.get('travel_type',row['travel_type']);dep=datetime_value(p.get('departure_at',row['departure_at']),'departure');arr=datetime_value(p.get('arrival_at',row['arrival_at']),'arrival',True)
     if status not in TRAVEL_STATUSES or typ not in TRAVEL_TYPES:raise ValueError('Invalid journey')
     if arr and arr<dep:raise ValueError('Arrival must be after departure')
     c.execute('UPDATE travel SET travel_type=?,service_number=?,from_name=?,to_name=?,departure_at=?,arrival_at=?,status=?,terminal=?,booking_reference=?,notes=? WHERE id=?',(typ,clean(p.get('service_number',row['service_number']),'service number',40,False),clean(p.get('from_name',row['from_name']),'departure place'),clean(p.get('to_name',row['to_name']),'arrival place'),dep,arr,status,clean(p.get('terminal',row['terminal']),'station or terminal',100,False),clean(p.get('booking_reference',row['booking_reference']),'booking reference',80,False),clean(p.get('notes',row['notes']),'notes',500,False),item))
   self.respond({'updated':True})
  except (ValueError,json.JSONDecodeError) as e:self.respond({'error':str(e)},400)
 def do_DELETE(self):
  m=re.fullmatch(r'/api/(places|travel|destinations|holidays|info-cards|checklist-items)/(\d+)',urllib.parse.urlparse(self.path).path)
  if not m:self.send_error(404);return
  kind,item=m.group(1),int(m.group(2));table={'info-cards':'info_cards','checklist-items':'checklist_items'}.get(kind,kind)
  with db() as c:
   if table=='destinations':c.execute('DELETE FROM places WHERE destination_id=?',(item,))
   if table=='holidays':c.execute('DELETE FROM places WHERE destination_id IN (SELECT id FROM destinations WHERE holiday_id=?)',(item,))
   cur=c.execute(f'DELETE FROM {table} WHERE id=?',(item,))
  self.respond({'deleted':bool(cur.rowcount)})
 def log_message(self,fmt,*args):print(f'{self.client_address[0]} - {fmt%args}',flush=True)

if __name__=='__main__':db().close();ThreadingHTTPServer((LISTEN_HOST,LISTEN_PORT),Handler).serve_forever()
