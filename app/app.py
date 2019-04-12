import socket
import json
import openstack.config

from flask import Flask
from flask import request
from flask import render_template
from flask import jsonify
from flask import Response
from flask_cors import CORS

import novaclient.client
from keystoneauth1 import session
from keystoneauth1.identity import v3
from keystoneauth1 import loading

from flask_autodoc import Autodoc
from openstack import connection

from exceptions import NotFound

import gnocchiclient.v1.client
import gnocchiclient.auth  

import requests

app = Flask(__name__)
CORS(app)
auto = Autodoc(app)

@app.route('/login', methods=['POST', 'GET'])
@auto.doc()
def login():
    error = None
    if request.method == 'POST':
        if valid_login(request.form['username'],
                       request.form['password']):
            return log_the_user_in(request.form['username'])
        else:
            error = 'Invalid username/password'
    # the code below is executed if the request method
    # was GET or the credentials were invalid
    return render_template('login.html', error=error)


def valid_login(username, password):
    pass

def log_the_user_in(username):
    pass

@app.route("/usage", methods=['GET'])
@auto.doc()
def get_usage():
    """ Return data from cloud hypervisors """
    return jsonify(get_usage_from_openstack())

@app.route("/vcpes", methods=['GET'])
@auto.doc()
def get_vcpes():
    """ Return json data related to vCPE's """
    return jsonify(get_vcpes_from_openstack())

@app.route("/vcpe/<string:vcpe_name>/run", methods=['GET'])
@auto.doc()
def get_vcpe_run(vcpe_name):
    return jsonify(start_or_stop_all_vnf(vcpe_name, option="run"))

@app.route("/vcpe/<string:vcpe_name>/stop", methods=['GET'])
@auto.doc()
def get_vcpe_stop(vcpe_name):
    return jsonify(start_or_stop_all_vnf(vcpe_name, option="stop"))

@app.route("/vcpe/<string:vcpe_name>/clients", methods=['GET'])
@auto.doc()
def get_vcpe_clients(vcpe_name):
    return get_dhcp_clients(vcpe_name, 'dhcp')

@app.route("/vcpe/<string:vcpe_name>/consoles", methods=['GET'])
@auto.doc()
def get_vcpe_consoles(vcpe_name):
    return jsonify(get_console_from_openstack(vcpe_name))

@app.route("/vcpe/<string:vcpe_name>/add", methods=['GET'])
@auto.doc()
def get_vcpe_add(vcpe_name):
    pass

@app.route('/meters/<string:meter_type>/<string:vcpe_name>/<string:vnf_name>', methods=['GET'])
@auto.doc()
def get_meters(meter_type, vnf_name, vcpe_name):
    """ 
    possible values for meter_type: 
    memory_usage, cpu_usage, network_in_usage, network_out_usage
    """
    return jsonify(get_metrics_proxy(meter_type, vnf_name, vcpe_name))

@app.route('/documentation', methods=['GET'])
@auto.doc()
def documentation():
    return auto.html()

def get_usage_from_openstack():
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config)
    return [ hypervisor.to_dict() for hypervisor in conn.compute.hypervisors(details=True)]

def get_vcpes_from_openstack():
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config)
    projects = [ project.name for project in conn.identity.projects() if project.name not in ['service'] ]

    vcpes = []

    for network in conn.network.networks():
        # by standard, the network name follows the pattern [<project_name>] VLAN <VLAN Number>
        if network.provider_network_type == 'vlan':
            project_name = network.name.split("]")[0].lstrip("[").lower()

            if project_name in projects:
                network_name = network.name.split("]")[1].lstrip(" ")
                vcpe = {}
                vcpe["project_name"] = project_name
                vcpe["network"] = network_name
                vcpe["vlan_id"] = network.provider_segmentation_id
                vcpe["status"] = network.status
                vcpe["description"] = network.description
                vcpes.append(vcpe)
           
    return vcpes

def start_or_stop_all_vnf(vcpe_name, option):
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config) 
    project = conn.identity.find_project(vcpe_name)
    
    if project is None:
        raise NotFound('VCPE {} not found'.format(vcpe_name), status_code=404)

    if option == "run":
        shutoff_servers = [ server for server in conn.compute.servers(all_projects=True) if server.location.project.id == project.id and server.status == "SHUTOFF"]
        for server in shutoff_servers: conn.compute.start_server(server)
    elif option == "stop":
        active_servers = [ server for server in conn.compute.servers(all_projects=True) if server.location.project.id == project.id and server.status == "ACTIVE"]
        for server in active_servers: conn.compute.stop_server(server)
    else:
        raise NotFound('Option {} not found'.format(option), status_code=404)

def get_console_from_openstack(vcpe_name):
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config)

    # projects = [project.name for project in conn.identity.projects() if project.name not in ['service'] ]
    
    # if vcpe_name not in projects:
    #     raise NotFound('VCPE {} not found'.format(vcpe_name), status_code=404)

    vcpe = None
    for project in conn.identity.projects():
        if project.name == vcpe_name:
            vcpe = project

    if vcpe is None:
        raise NotFound('VCPE {} not found'.format(vcpe_name), status_code=404)

    vcpe_servers_id = [ server.id for server in conn.compute.servers(all_projects=True) if server.location.project.id == vcpe.id and server.status == "ACTIVE"]  
    
    print(vcpe_servers_id)

    nova =  get_connection_nova_client()
    servers_from_nova_client = [ server for server in nova.servers.list(search_opts={'all_tenants': 1}) if server.id in vcpe_servers_id]

    return [ { 'name': server.name, 'console': server.get_console_url("novnc")['console'] } for server in servers_from_nova_client ]

def get_connection_nova_client():

    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')

    auth = v3.Password(auth_url=config.config['auth']['auth_url'],
                       password=config.config['auth']['password'],
                       project_name=config.config['auth']['project_name'],
                       username=config.config['auth']['username'],
                       project_domain_name=config.config['auth']['project_domain_name'],
                       user_domain_name=config.config['auth']['user_domain_name'])

    sess = session.Session(auth=auth, verify=False)
    return novaclient.client.Client(2, session=sess)

def get_connection_ceilometer_client():   
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')

    auth = v3.Password(auth_url=config.config['auth']['auth_url'],
                       password=config.config['auth']['password'],
                       project_name=config.config['auth']['project_name'],
                       username=config.config['auth']['username'],
                       project_domain_name=config.config['auth']['project_domain_name'],
                       user_domain_name=config.config['auth']['user_domain_name'])
    
    sess = session.Session(auth=auth, verify=False)
    return gnocchiclient.v1.client.Client(session=sess)


def get_metrics_from_openstack():
    gnocchi = get_connection_ceilometer_client()
    meters = gnocchi.metric.list()
    return [ meter for meter in meters ]

def get_metrics_proxy(meter_type, vnf_name, vcpe_name):
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config)

    vcpe = None
    for project in conn.identity.projects():
        if project.name == vcpe_name:
            vcpe = project

    if vcpe is None:
        raise NotFound('VCPE {} not found'.format(vcpe_name), status_code=404)

    vnf = None
    for server in conn.compute.servers(details=True, all_projects=True):
        if server.location.project.id == vcpe.id and server.status == "ACTIVE" and vnf_name.lower() in server.name.lower():
            vnf = server
            break
    
    if vnf is None:
        raise NotFound('VNF {} not found in {}'.format(vnf_name, vcpe_name), status_code=404)

    if meter_type == "memory_usage":
        return get_metric_memory_usage(vnf.id)
    elif meter_type == "cpu_usage":
        return get_metric_cpu_utilization(vnf.id)
    elif meter_type == "network_in_usage":
        return get_metric_network_incoming(vnf.id)
    elif meter_type == "network_out_usage":
        return get_metric_network_outgoing(vnf.id)
    else:
        raise NotFound('Metric {} for VNF {} not found in {}'.format(type, vnf_name, vcpe_name), status_code=404)

def get_metric_memory_usage(resource_id):
    gnocchi_client = get_connection_ceilometer_client()
    meters = gnocchi_client.metric.get_measures('memory.usage', resource_id=resource_id)    
    return meters

def get_metric_cpu_utilization(resource_id):
    gnocchi_client = get_connection_ceilometer_client()
    meters = gnocchi_client.metric.get_measures('cpu_util', resource_id=resource_id)
    
    return meters

def get_metric_network_incoming(resource_id):
    gnocchi_client = get_connection_ceilometer_client()
    meter = gnocchi_client.metric.get_measures('network.incoming.bytes', resource_id=resource_id)
    return meter

def get_metric_network_outgoing(resource_id):
    gnocchi_client = get_connection_ceilometer_client()
    meter = gnocchi_client.metric.get_measures('network.outgoing.bytes', resource_id=resource_id)
    return meter

def get_dhcp_clients(vcpe_name, vnf_name='dhcp'):
    config = openstack.config.get_cloud_region(cloud='nerds', region_name='RegionOne')
    conn = connection.Connection(config=config)

    vcpe = None
    for project in conn.identity.projects():
        if project.name == vcpe_name:
            vcpe = project

    if vcpe is None:
        raise NotFound('VCPE {} not found'.format(vcpe_name), status_code=404)

    vnf = None
    for server in conn.compute.servers(details=True, all_projects=True):
        if server.location.project.id == vcpe.id and server.status == "ACTIVE" and vnf_name.lower() in server.name.lower():
            vnf = server
            break

    if vnf is None:
        raise NotFound('VNF {} not found in {}'.format(vnf_name, vcpe_name), status_code=404)

    vnf_ip = None

    print(vnf.addresses.keys())

    for net in vnf.addresses.keys():
        if vcpe_name.lower() in net.lower():
            for port in vnf.addresses[net]:
                if port['version'] == 4:
                    vnf_ip = port['addr']
                    break
    
    if vnf_ip is None:
        raise NotFound('VNF Interface not found', status_code=404)

    router_proxy = None
    for server in conn.compute.servers(details=True, all_projects=False):
        if server.status == "ACTIVE" and "router" in server.name.lower():
            router_proxy = server
            break

    if router_proxy is None:
        raise NotFound('Router {} not found in {}'.format(vnf_name, vcpe_name), status_code=404)

    router_proxy_ip = None
    for net in router_proxy.addresses.keys():
        if "ADMIN".lower() in net.lower():
            for port in router_proxy.addresses[net]:
                if port['version'] == 4:
                    router_proxy_ip = port['addr']
                    break
    
    if router_proxy_ip is None:
        raise NotFound('Router Interface not found', status_code=404)

    response = requests.get("http://{}:9999/{}".format(router_proxy_ip, vnf_ip))

    return Response(json.dumps(response),  mimetype='application/json')

@app.errorhandler(NotFound)
def handle_not_found(error):
    response = jsonify(error.to_dict())
    response.status_code = error.status_code
    return response

if __name__ == "__main__":
    app.run(host='0.0.0.0')
