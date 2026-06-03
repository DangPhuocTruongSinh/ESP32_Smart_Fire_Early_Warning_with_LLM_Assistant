import os
from dotenv import load_dotenv
from influxdb_client_3 import InfluxDBClient3

load_dotenv('.env')

url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
token = os.getenv('INFLUXDB_TOKEN', 'fire_alarm_token_123')
org = os.getenv('INFLUXDB_ORG', 'home')
bucket = os.getenv('BUCKET', 'environment')

client = InfluxDBClient3(host=url, token=token, org=org, database=bucket)
query = f"""
SELECT time, temperature, humidity, gas, ground_truth, ml_class, mode, device
FROM '{bucket}'
WHERE time >= now() - interval '5 minutes'
ORDER BY time DESC
LIMIT 20
"""
try:
    table = client.query(query=query, language='sql')
    for row in table.to_pylist():
        print(f'{row["time"]}: GT={row.get("ground_truth")}, ML={row.get("ml_class")}, mode={row.get("mode")}')
except Exception as e:
    print('Error:', e)
