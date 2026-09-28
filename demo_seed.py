#!/usr/bin/env python3
"""Create a fictional two-week European holiday for screenshots or evaluation."""
import argparse, pathlib, sqlite3, time
import server

HOLIDAY=("European Rail Adventure", "Four cities, famous sights and scenic journeys across Europe.", "2027-05-01", "2027-05-14", "planning", "city")
DESTINATIONS=(
 ("amsterdam","Amsterdam","Netherlands","NL","2027-05-01","2027-05-04",52.3676,4.9041,1),
 ("paris","Paris","France","FR","2027-05-04","2027-05-08",48.8566,2.3522,2),
 ("zurich","Zürich","Switzerland","CH","2027-05-08","2027-05-10",47.3769,8.5417,3),
 ("rome","Rome","Italy","IT","2027-05-10","2027-05-14",41.9028,12.4964,4),
)
TRAVEL=(
 ("flight","EZY9000","London","Amsterdam Schiphol","2027-05-01T08:10","2027-05-01T10:25","booked","Schiphol Airport","DEMO-001","Fictional flight for demonstration"),
 ("train","Eurostar 9328","Amsterdam Centraal","Paris Gare du Nord","2027-05-04T09:10","2027-05-04T12:35","booked","Amsterdam Centraal","DEMO-002","Direct daytime train"),
 ("train","TGV Lyria 9219","Paris Gare de Lyon","Zürich HB","2027-05-08T10:20","2027-05-08T14:26","planned","Gare de Lyon","","Scenic rail journey"),
 ("train","EuroCity + FR","Zürich HB","Roma Termini","2027-05-10T07:33","2027-05-10T15:49","planned","Zürich HB","","Change in Milano Centrale"),
 ("flight","BA9999","Rome Fiumicino","London","2027-05-14T18:30","2027-05-14T20:15","planned","Fiumicino Airport","","Fictional flight for demonstration"),
)
PLACES={
 "amsterdam":(
  ("hotel","Canal House Hotel",52.3740,4.8830,"2027-05-01","evening"),("activity","Rijksmuseum",52.3600,4.8852,"2027-05-02","morning"),("activity","Anne Frank House",52.3752,4.8840,"2027-05-02","afternoon"),("food","Restaurant De Kas",52.3494,4.9403,"2027-05-02","evening"),("activity","Canal walk and free time",None,None,"2027-05-03","night")),
 "paris":(
  ("hotel","Left Bank Hotel",48.8529,2.3370,"2027-05-04","evening"),("activity","Louvre Museum",48.8606,2.3376,"2027-05-05","morning"),("activity","Eiffel Tower",48.8584,2.2945,"2027-05-05","night"),("activity","Montmartre",48.8867,2.3431,"2027-05-06","afternoon"),("food","Bouillon Chartier",48.8719,2.3430,"2027-05-06","evening")),
 "zurich":(
  ("hotel","Old Town Hotel",47.3726,8.5424,"2027-05-08","evening"),("activity","Zürich Old Town",47.3716,8.5424,"2027-05-09","morning"),("activity","Uetliberg viewpoint",47.3495,8.4910,"2027-05-09","afternoon"),("food","Zeughauskeller",47.3703,8.5397,"2027-05-09","evening")),
 "rome":(
  ("hotel","Centro Storico Hotel",41.8992,12.4768,"2027-05-10","evening"),("activity","Colosseum",41.8902,12.4922,"2027-05-11","morning"),("activity","Roman Forum",41.8925,12.4853,"2027-05-11","afternoon"),("activity","Trevi Fountain",41.9009,12.4833,"2027-05-11","night"),("food","Roscioli",41.8957,12.4746,"2027-05-12","evening"),("activity","Vatican Museums",41.9065,12.4536,"2027-05-13","morning")),
}

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',default='demo.sqlite3');parser.add_argument('--force',action='store_true');args=parser.parse_args()
 path=pathlib.Path(args.database).resolve()
 if path.exists() and not args.force: raise SystemExit(f'{path} already exists; choose another path or use --force')
 if path.exists(): path.unlink()
 server.DB_PATH=path;c=server.db()
 # Remove the built-in migration trip from a brand-new database and replace it with fictional data.
 c.execute('DELETE FROM places');c.execute('DELETE FROM travel');c.execute('DELETE FROM destinations');c.execute('DELETE FROM holidays')
 hid=c.execute('INSERT INTO holidays(name,subtitle,start_date,end_date,status,theme,created_at) VALUES(?,?,?,?,?,?,?)',(*HOLIDAY,int(time.time()))).lastrowid
 ids={}
 for d in DESTINATIONS:
  ids[d[0]]=c.execute('INSERT INTO destinations(holiday_id,slug,name,country,country_code,start_date,end_date,latitude,longitude,sort_order) VALUES(?,?,?,?,?,?,?,?,?,?)',(hid,*d)).lastrowid
 for j in TRAVEL:c.execute('INSERT INTO travel(holiday_id,travel_type,service_number,from_name,to_name,departure_at,arrival_at,status,terminal,booking_reference,notes,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(hid,*j,int(time.time())))
 for slug,places in PLACES.items():
  for category,name,lat,lon,day,period in places:
   source='map' if lat is not None else 'text';url=f'https://www.google.com/maps/search/?api=1&query={lat},{lon}' if lat is not None else None
   c.execute('INSERT INTO places(city,destination_id,category,name,url,latitude,longitude,created_at,schedule_date,schedule_period,source_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(slug,ids[slug],category,name,url,lat,lon,int(time.time()),day,period,source))
 c.commit();c.close();print(f'Created fictional demo at {path}')
if __name__=='__main__':main()
