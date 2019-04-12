from flask import Flask 
from flask import jsonify 
import json
from flask import Response

from isc_dhcp_leases import Lease, IscDhcpLeases
import functools
from concurrent import futures
import subprocess

app = Flask(__name__)

def ping(lease):
    p = subprocess.Popen(["ping", "-q", "-c", "1", "-W", "1",
                          lease.ip],
                         stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    return p.wait() == 0

@app.route('/')
def proxy():
    leases = IscDhcpLeases('/var/lib/dhcp/dhcpd.leases')

    ips = [lease for lease in leases.get()]

    with futures.ThreadPoolExecutor(max_workers=255) as executor:
        futs = [
            (host, executor.submit(functools.partial(ping, host)))
            for host in ips
        ]

    js = [{"ip": ip.ip, "mac": ip.ethernet, "hostname": ip.hostname} for ip, f in futs if f.result()]

    return Response(json.dumps(js),  mimetype='application/json')

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=9999)
