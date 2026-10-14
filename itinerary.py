"""Private itinerary data, explicit contact lookups and printable PDF output."""
import datetime as dt
import html
import io
import json
import re
import threading
import time
import urllib.parse
import urllib.request

FIELDS = {
    'hotel': ['address', 'phone', 'website', 'sources', 'verification'],
    'travel': ['airline', 'operator', 'operator_number', 'from_address', 'to_address',
               'from_timezone', 'to_timezone', 'sources', 'verification'],
}
LOOKUP_LOCK = threading.Lock()
LAST_LOOKUP = 0
AIRLINES = {'BA':'British Airways', 'QR':'Qatar Airways', 'AK':'AirAsia', 'SQ':'Singapore Airlines',
            'MH':'Malaysia Airlines', 'KL':'KLM', 'AF':'Air France', 'LH':'Lufthansa', 'U2':'easyJet',
            'EK':'Emirates', 'FR':'Ryanair', 'LX':'SWISS', 'TR':'Scoot'}


def init_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS itinerary_details(kind TEXT NOT NULL,item_id INTEGER NOT NULL,data TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(kind,item_id))')
    c.execute('CREATE TABLE IF NOT EXISTS itinerary_lookups(query TEXT PRIMARY KEY,data TEXT NOT NULL,created_at INTEGER NOT NULL)')


def details(c, kind, ident):
    row = c.execute('SELECT data,updated_at FROM itinerary_details WHERE kind=? AND item_id=?', (kind, ident)).fetchone()
    return dict(json.loads(row[0]), updated_at=row[1]) if row else {}


def member(c, holiday_id, kind, ident):
    if kind == 'hotel':
        row = c.execute("SELECT p.*,d.name destination,d.country FROM places p JOIN destinations d ON d.id=p.destination_id WHERE d.holiday_id=? AND p.id=? AND p.category='hotel'", (holiday_id, ident)).fetchone()
    elif kind == 'travel':
        row = c.execute('SELECT * FROM travel WHERE holiday_id=? AND id=?', (holiday_id, ident)).fetchone()
    else:
        raise ValueError('Choose a hotel or journey')
    if not row:
        raise ValueError('Item not found in this holiday')
    return dict(row)


def save(c, holiday_id, payload):
    kind, ident = payload.get('kind'), int(payload.get('id', 0))
    member(c, holiday_id, kind, ident)
    values = details(c, kind, ident)
    values.pop('updated_at', None)
    incoming = payload.get('details', {})
    if not isinstance(incoming, dict):
        raise ValueError('Invalid contact details')
    for key in FIELDS[kind]:
        if key not in incoming:
            continue
        value = incoming[key]
        if not isinstance(value, str) or len(value) > (3000 if key == 'sources' else 600):
            raise ValueError('Keep contact details shorter')
        if key in ('sources', 'website'):
            for url in value.splitlines():
                u = urllib.parse.urlparse(url.strip())
                if url.strip() and (u.scheme not in ('http', 'https') or not u.netloc or u.username):
                    raise ValueError('Use full http or https source links')
        values[key] = value.strip()
    c.execute('INSERT OR REPLACE INTO itinerary_details VALUES(?,?,?,?)', (kind, ident, json.dumps(values), dt.date.today().isoformat()))


def day_range(start, end):
    day, last = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while day <= last:
        yield day.isoformat()
        day += dt.timedelta(days=1)


def get(c, holiday_id):
    h = c.execute('SELECT * FROM holidays WHERE id=?', (holiday_id,)).fetchone()
    if not h:
        raise ValueError('Holiday not found')
    ds = [dict(d) for d in c.execute('SELECT * FROM destinations WHERE holiday_id=? ORDER BY start_date,sort_order,id', (holiday_id,))]
    hotels = [dict(p) for p in c.execute("SELECT p.id,p.name,p.url,p.stay_start_date,p.stay_end_date,p.destination_id,d.name destination,d.start_date destination_start,d.end_date destination_end FROM places p JOIN destinations d ON d.id=p.destination_id WHERE d.holiday_id=? AND p.category='hotel' ORDER BY d.start_date,p.stay_start_date,p.id", (holiday_id,))]
    journeys = [dict(j) for j in c.execute('SELECT id,travel_type,service_number,from_name,to_name,departure_at,arrival_at,status,terminal,notes FROM travel WHERE holiday_id=? ORDER BY departure_at,id', (holiday_id,))]
    for p in hotels:
        p['details'] = details(c, 'hotel', p['id'])
        p['dates_valid'] = bool(p['stay_start_date'] and p['stay_end_date'] and p['destination_start'] <= p['stay_start_date'] <= p['stay_end_date'] <= p['destination_end'])
    for j in journeys:
        j['details'] = details(c, 'travel', j['id'])
        code = re.search(r'\b([A-Z0-9]{2})\s*\d{1,4}\b', j['service_number'].upper())
        j['airline_hint'] = AIRLINES.get(code[1], '') if code and j['travel_type'] == 'flight' else ''
    calendar = []
    for day in day_range(h['start_date'], h['end_date']):
        areas = [d['name'] for d in ds if d['start_date'] <= day <= d['end_date']]
        stays = []
        for p in hotels:
            if p['dates_valid'] and p['stay_start_date'] <= day <= p['stay_end_date']:
                phase = 'Check-in / check-out' if p['stay_start_date'] == p['stay_end_date'] == day else 'Check-in' if day == p['stay_start_date'] else 'Check-out' if day == p['stay_end_date'] else 'Staying'
                stays.append(f"{phase}: {p['name']}")
        events = []
        for j in journeys:
            for key, label in [('departure_at','Depart'), ('arrival_at','Arrive')]:
                if j[key] and j[key][:10] == day:
                    place = j['from_name'] if key == 'departure_at' else j['to_name']
                    events.append(f"{j[key][11:16]} {label} {j['service_number'] or j['travel_type'].title()}: {place} ({j['status']})")
        calendar.append(dict(date=day, areas=areas, accommodation=stays, travel=events))
    return dict(holiday=dict(h), destinations=ds, hotels=hotels, travel=journeys, calendar=calendar,
                generated_at=dt.datetime.now(dt.timezone.utc).strftime('%d %b %Y %H:%M UTC'),
                notice='Times are the saved booking/planning times, local to each departure or arrival location. This is not a live flight-status feed. Reconfirm times, terminals and operating carriers with your booking before travel. Missing details are explicitly marked; lookup suggestions need checking.')


def lookup(db, holiday_id, payload):
    """User-triggered candidates only. No silent overwrites or inferred phone numbers."""
    kind, ident = payload.get('kind'), int(payload.get('id', 0))
    with db() as c:
        item = member(c, holiday_id, kind, ident)
    query = str(payload.get('query', '')).strip()
    if not query:
        if kind == 'hotel':
            query = f"{item['name']}, {item['destination']}, {item['country']}"
        else:
            side = payload.get('side', 'from')
            if side not in ('from', 'to'):
                raise ValueError('Choose departure or arrival')
            query = item[side+'_name'] + (' airport' if item['travel_type'] == 'flight' else ' station' if item['travel_type'] == 'train' else '')
    if not query or len(query) > 240:
        raise ValueError('Enter a shorter place search')
    global LAST_LOOKUP
    with LOOKUP_LOCK:
        with db() as c:
            row = c.execute('SELECT * FROM itinerary_lookups WHERE query=?', (query,)).fetchone()
            if row and time.time()-row['created_at'] < 30*86400:
                return json.loads(row['data'])
        time.sleep(max(0, 1.1-(time.monotonic()-LAST_LOOKUP)))
        params = urllib.parse.urlencode(dict(q=query, format='jsonv2', limit=5, addressdetails=1, extratags=1))
        req = urllib.request.Request('https://nominatim.openstreetmap.org/search?'+params, headers={'User-Agent':'HolidayPlanner/2.2 (https://github.com/ChimeraX-CMRX/holiday-planner)'})
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                rows = json.loads(response.read(1000000))
        finally:
            LAST_LOOKUP = time.monotonic()
        results = []
        for row in rows:
            tags = row.get('extratags') or {}
            typ = row.get('osm_type')
            if typ not in ('node','way','relation'):
                continue
            results.append(dict(name=row.get('name',''), address=row.get('display_name',''), phone=tags.get('contact:phone',tags.get('phone','')), source=f"https://www.openstreetmap.org/{typ}/{int(row['osm_id'])}"))
        with db() as c:
            c.execute('INSERT OR REPLACE INTO itinerary_lookups VALUES(?,?,?)', (query, json.dumps(results), int(time.time())))
        return results


def pdf(data):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pathlib import Path
    if 'Itinerary' not in pdfmetrics.getRegisteredFontNames():
        fonts = Path(__file__).parent/'fonts'
        pdfmetrics.registerFont(TTFont('Itinerary', str(fonts/'Vera.ttf')))
        pdfmetrics.registerFont(TTFont('ItineraryBold', str(fonts/'VeraBd.ttf')))
        pdfmetrics.registerFontFamily('Itinerary', normal='Itinerary', bold='ItineraryBold', italic='Itinerary', boldItalic='ItineraryBold')
    output = io.BytesIO()
    styles = getSampleStyleSheet()
    for s in styles.byName.values():
        s.fontName='Itinerary'
    styles.add(ParagraphStyle('SmallText', fontName='Itinerary', fontSize=8, leading=11, textColor=colors.HexColor('#52636b'), spaceAfter=4))
    styles['Normal'].fontSize=9; styles['Normal'].leading=13; styles['Normal'].spaceAfter=5
    styles['Title'].fontName='ItineraryBold'; styles['Title'].fontSize=23; styles['Title'].leading=28; styles['Title'].alignment=TA_LEFT
    styles['Heading2'].fontName='ItineraryBold'; styles['Heading2'].fontSize=13; styles['Heading2'].textColor=colors.HexColor('#284957')
    def text(s):
        return html.escape(str(s or '')).replace('→',' - ').replace('–','-').replace('—','-').replace('\n','<br/>')
    def p(s, style='Normal'):
        return Paragraph(text(s), styles[style])
    def date(s):
        if not s:return 'Not entered'
        return dt.datetime.fromisoformat(s).strftime('%a %d %b %Y'+(' %H:%M' if 'T' in s else ''))
    story=[p(data['holiday']['name'],'Title'),p(f"{date(data['holiday']['start_date'])} - {date(data['holiday']['end_date'])} | {data['holiday']['status'].title()}"),p('Sharable travel itinerary','SmallText'),Spacer(1,4*mm),p('At a glance','Heading2')]
    rows=[[p('Date'),p('Area'),p('Travel - local times')]]
    for day in data['calendar']:
        rows.append([p(date(day['date']),'SmallText'),p(' / '.join(day['areas']) or 'Travel day / no destination assigned','SmallText'),p('\n'.join(day['travel']),'SmallText')])
    table=Table(rows,colWidths=[32*mm,52*mm,90*mm],repeatRows=1,hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e4edef')),('VALIGN',(0,0),(-1,-1),'TOP'),('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,-1),.3,colors.HexColor('#d5dfe2')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f6f8f8')])]))
    story.extend([table,PageBreak(),p('Travel details','Title')])
    if not data['travel']:story.append(p('No journeys added.'))
    for j in data['travel']:
        block_start = len(story)
        d=j['details']
        story.extend([p(f"{j['service_number'] or j['travel_type'].title()} | {j['status'].upper()}",'Heading2'),p(j['from_name']+' - '+j['to_name'])])
        if j['travel_type']=='flight':
            story.extend([p('Airline: '+(d.get('airline') or (j['airline_hint']+' (prefix only; verify)') if j['airline_hint'] else d.get('airline') or 'Not confirmed')),p('Operated by: '+(d.get('operator') or 'Not confirmed')+(' / '+d['operator_number'] if d.get('operator_number') else ''))])
        elif d.get('operator'):
            story.append(p('Operator: '+d['operator']))
        for side,label,key in [('from','Departure','departure_at'),('to','Arrival','arrival_at')]:
            story.extend([p(label+': '+date(j[key])+' | '+(d.get(side+'_timezone') or 'local time; zone not recorded')),p('Address: '+(d.get(side+'_address') or 'Not entered - check booking'),'SmallText')])
        if j['terminal']:story.append(p('Terminal / station: '+j['terminal']))
        if j['notes']:story.append(p(j['notes'],'SmallText'))
        story.append(Spacer(1,4*mm))
        story[block_start:] = [KeepTogether(story[block_start:])]
    story.extend([PageBreak(),p('Accommodation','Title')])
    if not data['hotels']:story.append(p('No hotels added.'))
    for hotel in data['hotels']:
        block_start = len(story)
        d=hotel['details']
        story.extend([p(hotel['name'],'Heading2'),p(hotel['destination']),p('Check-in: '+date(hotel['stay_start_date'])+' | Check-out: '+date(hotel['stay_end_date'])),p('Address: '+(d.get('address') or 'Not entered')),p('Telephone: '+(d.get('phone') or 'Not found - confirm with hotel'))])
        if not hotel['dates_valid']:story.append(p('Stay dates missing or outside destination dates; not shown in the calendar.','SmallText'))
        if d.get('website'):story.append(p(d['website'],'SmallText'))
        story.append(Spacer(1,5*mm))
        story[block_start:] = [KeepTogether(story[block_start:])]
    def footer(canvas,doc):
        canvas.setFont('Itinerary',7);canvas.setFillColor(colors.HexColor('#52636b'))
        canvas.drawString(18*mm,12*mm,'Generated '+data['generated_at'])
        canvas.drawRightString(192*mm,12*mm,str(doc.page))
    SimpleDocTemplate(output,pagesize=A4,rightMargin=18*mm,leftMargin=18*mm,topMargin=17*mm,bottomMargin=20*mm,title=data['holiday']['name']+' - Sharable travel itinerary',author='Holiday Planner').build(story,onFirstPage=footer,onLaterPages=footer)
    return output.getvalue()
