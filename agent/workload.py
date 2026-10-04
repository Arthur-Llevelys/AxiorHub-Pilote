"""Occupancy, not an additive sum of overlapping appointments and task dues."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


def occupancy(events, start_day, days=14, timezone='Europe/Paris'):
    tz=ZoneInfo(timezone); buckets={}; whole_days={}; counts={}
    last=start_day+timedelta(days=days)
    for event in events:
        if not event.get('busy',True):continue
        try:
            a=datetime.fromisoformat(event['starts']);b=datetime.fromisoformat(event['ends'])
            if a.tzinfo is None or b.tzinfo is None:continue
            a=a.astimezone(tz);b=b.astimezone(tz)
        except (KeyError,ValueError,TypeError):continue
        if b<=a:continue
        # Midnight-to-midnight objects are date markers, not 24h labour.
        allday=a.time()==time.min and b.time()==time.min
        day=max(a.date(),start_day)
        while day<last and day<=b.date():
            lower=datetime.combine(day,time.min,tz);upper=lower+timedelta(days=1)
            x=max(a,lower);y=min(b,upper)
            if y>x:
                key=day.isoformat()
                if allday:whole_days[key]=whole_days.get(key,0)+1
                else:
                    buckets.setdefault(key,[]).append((x.timestamp(),y.timestamp()))
                    counts[key]=counts.get(key,0)+1
            day+=timedelta(days=1)
    result={}
    for day in set(buckets)|set(whole_days):
        merged=[]
        for a,b in sorted(buckets.get(day,[])):
            if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
            else:merged.append((a,b))
        result[day]={'planned_minutes':round(sum(b-a for a,b in merged)/60),
          'all_day_markers':whole_days.get(day,0),'timed_events':counts.get(day,0),
          'method':'union_intervals_local_timezone','task_due_dates_added':False}
    return result
