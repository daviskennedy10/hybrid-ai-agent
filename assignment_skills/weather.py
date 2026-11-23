"""Current + daily weather for a configured fictitious profile; no paid API key."""
from urllib.parse import urlencode
import math
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from assignment_skills.common import SkillError


def number(value):
    return type(value) in {int,float} and math.isfinite(value)


class WeatherFailure(SkillError):
    def __init__(self,kind,attempts):
        super().__init__(kind)
        self.provider_attempts=attempts


class Weather:
    def __init__(self,config,http,now=None):
        self.config,self.http=config,http
        self.now=now or (lambda:datetime.now(timezone.utc))
    def run(self,operation='forecast'):
        try:
            result=self._open_meteo(operation)
            result.update(provider_id='open_meteo',provider_fallback=False,primary_failure=None)
            return result
        except SkillError as exc:
            if exc.kind not in {'unavailable','timeout'}:
                raise WeatherFailure(exc.kind,[{'provider':'open_meteo','failure':exc.kind}]) from None
            primary_failure=exc.kind
        try:
            result=self._wttr(operation)
        except SkillError as exc:
            raise WeatherFailure(exc.kind,[{'provider':'open_meteo','failure':primary_failure},
                                           {'provider':'wttr','failure':exc.kind}]) from None
        result.update(provider_id='wttr',provider_fallback=True,primary_failure=primary_failure)
        return result

    def _open_meteo(self,operation='forecast'):
        if operation not in {'current','forecast'}: raise SkillError('forbidden')
        p=self.config.profile
        data=self.http.json('https://api.open-meteo.com/v1/forecast?'+urlencode({
            'latitude':p['latitude'],'longitude':p['longitude'],'timezone':p['timezone'],
            'current':'temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m',
            'daily':'temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code',
            'forecast_days':3,'temperature_unit':'celsius','wind_speed_unit':'kmh'}))
        current,daily=data.get('current'),data.get('daily')
        units=data.get('current_units',{})
        daily_units=data.get('daily_units',{})
        if (data.get('timezone')!=p['timezone'] or not isinstance(current,dict) or not isinstance(daily,dict)
                or not isinstance(units,dict) or not isinstance(daily_units,dict)
                or units.get('temperature_2m')!='°C' or units.get('relative_humidity_2m')!='%'
                or units.get('wind_speed_10m')!='km/h'
                or any(daily_units.get(k)!='°C' for k in ['temperature_2m_max','temperature_2m_min'])
                or daily_units.get('precipitation_probability_max')!='%'
                or not isinstance(current.get('time'),str)
                or not all(number(current.get(k)) for k in ['temperature_2m','relative_humidity_2m','weather_code','wind_speed_10m'])):
            raise SkillError('invalid_response')
        try: datetime.fromisoformat(current['time'])
        except ValueError: raise SkillError('invalid_response') from None
        if (not -100<=current['temperature_2m']<=70 or not 0<=current['relative_humidity_2m']<=100
                or not 0<=current['wind_speed_10m']<=500 or not 0<=current['weather_code']<=99):
            raise SkillError('invalid_response')
        dates=daily.get('time')
        keys=['temperature_2m_max','temperature_2m_min','precipitation_probability_max','weather_code']
        if not isinstance(dates,list) or not 1<=len(dates)<=3 or any(not isinstance(daily.get(k),list) or len(daily[k])!=len(dates) for k in keys):
            raise SkillError('invalid_response')
        days=[]
        for i,day in enumerate(dates):
            if not isinstance(day,str) or not all(number(daily[k][i]) for k in keys): raise SkillError('invalid_response')
            try: date.fromisoformat(day)
            except ValueError: raise SkillError('invalid_response') from None
            if (not -100<=daily['temperature_2m_min'][i]<=daily['temperature_2m_max'][i]<=70
                    or not 0<=daily['precipitation_probability_max'][i]<=100
                    or not 0<=daily['weather_code'][i]<=99):
                raise SkillError('invalid_response')
            days.append({'date':day,'high_c':daily['temperature_2m_max'][i],'low_c':daily['temperature_2m_min'][i],
                         'precipitation_probability_pct':daily['precipitation_probability_max'][i],
                         'weather_code':daily['weather_code'][i]})
        return {'source':'Open-Meteo','source_url':'https://open-meteo.com/',
                'profile':p['name'],'location':p['location'],'timezone':p['timezone'],
                'current':{'time':current['time'],'temperature_c':current['temperature_2m'],
                           'humidity_pct':current['relative_humidity_2m'],'weather_code':current['weather_code'],
                           'wind_kmh':current['wind_speed_10m']},'daily':days,'units':{'temperature':'°C','wind':'km/h'}}

    def _wttr(self,operation):
        p=self.config.profile
        data=self.http.json('https://wttr.in/'+str(p['latitude'])+','+str(p['longitude'])+'?format=j1')
        try:
            observations=data['current_condition'];forecast=data['weather'];areas=data['nearest_area']
            if not isinstance(observations,list) or len(observations)!=1 or not isinstance(forecast,list) or not 1<=len(forecast)<=3:
                raise ValueError
            area=areas[0]
            latitude,longitude=float(area['latitude']),float(area['longitude'])
            if (not number(latitude) or not number(longitude) or abs(latitude-p['latitude'])>0.5
                    or abs(longitude-p['longitude'])>0.5):
                raise ValueError
            current=observations[0]
            if current.get('localObsDateTime'):
                observed_at=datetime.strptime(current['localObsDateTime'],'%Y-%m-%d %I:%M %p').replace(tzinfo=ZoneInfo(p['timezone'])).isoformat(timespec='minutes')
                observed=observed_at;precision='datetime';observation_zone=p['timezone']
            else:
                # Actual wttr.in responses can omit localObsDateTime. A time-only
                # field does not establish an observation date or timezone.
                source_time=datetime.strptime(current['observation_time'],'%I:%M %p').strftime('%H:%M')
                observed=source_time+' (source time; date/timezone not supplied)'
                observed_at=None;precision='time_only';observation_zone=None
            temperature=float(current['temp_C']);humidity=float(current['humidity']);wind=float(current['windspeedKmph'])
            if (not all(number(v) for v in [temperature,humidity,wind]) or not -100<=temperature<=70
                    or not 0<=humidity<=100 or not 0<=wind<=500): raise ValueError
            days=[]
            today=self.now().astimezone(ZoneInfo(p['timezone'])).date()
            for row in forecast:
                day=date.fromisoformat(row['date']).isoformat()
                high,low=float(row['maxtempC']),float(row['mintempC'])
                hourly=row['hourly']
                if not isinstance(hourly,list) or not 1<=len(hourly)<=24: raise ValueError
                rain=[float(hour['chanceofrain']) for hour in hourly]
                if (not all(number(v) for v in [high,low,*rain]) or not -100<=low<=high<=70
                        or not all(0<=v<=100 for v in rain)): raise ValueError
                if today<=date.fromisoformat(day)<=today+timedelta(days=7):
                    days.append({'date':day,'high_c':high,'low_c':low,'precipitation_probability_pct':max(rain),
                                 'precipitation_definition':'daily maximum hourly chance of rain'})
            if not days: raise ValueError
            days=sorted(days,key=lambda d:d['date'])[:3]
            return {'source':'wttr.in','source_url':'https://wttr.in/',
                    'profile':p['name'],'location':p['location'],'timezone':p['timezone'],
                    'current':{'time':observed,'observed_at':observed_at,'time_precision':precision,
                               'observation_timezone':observation_zone,'temperature_c':temperature,
                               'humidity_pct':humidity,'wind_kmh':wind},
                    'forecast_days_available':len(days),'daily':days,'units':{'temperature':'°C','wind':'km/h'}}
        except (KeyError,ValueError,TypeError,IndexError,OverflowError):
            raise SkillError('invalid_response') from None
