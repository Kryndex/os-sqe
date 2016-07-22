import json
import netaddr
import unicodedata

from fabric.api import local


def _local(cmd, split=True):
    r = local(cmd, capture=True).stdout
    return r.split('\n') if split else r


def get_ovs_ports(ovs_bridge):
    ovs_ports_dict = {}
    for port_name in _local('sudo ovs-vsctl list-ports {0}'.format(ovs_bridge)):
        print '\nGetting info of port ', port_name
        _uuid = _local('sudo ovs-vsctl find port name={0}'.format(port_name))[0].split(':')[1].strip()
        vlan = _local('sudo ovs-vsctl get port {0} tag'.format(_uuid), split=False)
        if vlan != '[]':
            ovs_ports_dict[vlan] = {'_uuid': _uuid, 'name': port_name}
    return ovs_ports_dict


def get_ovs_bridges():
    bridges = local('sudo ovs-vsctl list-br', capture=True)
    return bridges.stdout.split('\n')


def get_vtc_host_connection_id(vtc_ui_client, device_name, device_port):
    ethernet_mappings = vtc_ui_client.get_ethernet_mappings(device_name)
    for em in ethernet_mappings:
        if em['name'] == device_port:
            return em['connid']


def create_ports(vtc_ui_client, device_name, device_port, ovs_bridge):
    network_inventory = vtc_ui_client.get_network_inventory()
    devices = [ni for ni in network_inventory if ni['device_name'] == device_name]
    if len(devices) == 0:
        raise Exception('No device called {0}'.format(device_name))
    else:
        print '\nFound device {0} in VTC'.format(device_name)

    ethernet_mappings = vtc_ui_client.get_ethernet_mappings(device_name)
    connection_id = None
    for em in ethernet_mappings:
        if em['name'] == device_port:
            connection_id = em['connid']
            break
    if not connection_id:
        raise Exception('Could not fond interface {0} of {1}. Check hosts inventory'.format(device_port, device_name))

    tenant = vtc_ui_client.get_tenant()

    network_port_dict = {}
    ovs_ports_dict = get_ovs_ports(ovs_bridge)

    print "\nPorts on the server"
    print ovs_ports_dict

    cur_networks = {net['id']: net for net in vtc_ui_client.get_overlay_networks()}

    # Add ports
    for net_id, net in cur_networks.iteritems():
        print "\nNetwork ID: {0}".format(net_id)
        if net_id in network_port_dict:
            # Port has already been added
            continue

        cur_ports = vtc_ui_client.get_overlay_network_ports(net_id)
        created_ports = [p for p in cur_ports if p['connid'] == connection_id]
        if len(created_ports) == 0:
            # Create port in VTC
            def to_ascci(str):
                return unicodedata.normalize('NFKD', str).encode('ascii', 'ignore')

            # Create port
            data = {'resource': {
                'ToRPortToDelete': [to_ascci(p['id']) for p in cur_ports],
                'id': to_ascci(net['id']),
                'network': {
                    'network_name': to_ascci(net['name']),
                    #'router-external': net['router-external']},
                    'router-external': False},
                'tenant_id': to_ascci(tenant['vmm-tenant-id']),
                'tenant_name': to_ascci(tenant['name']),
                'tor_port': [
                    {
                        'binding_host_id': "vts-host",
                        'connid': [{'id': to_ascci(connection_id)}],
                        'device_id': device_name,
                        'mac': "",
                        'type': "virtual-server"
                    }
                ]}
            }
            response = vtc_ui_client.put_network_port(net['id'], json.dumps(data))
            if not response.status_code == 200:
                raise Exception(response)

            cur_ports = vtc_ui_client.get_overlay_network_ports(net_id)
            created_ports = [p for p in cur_ports if p['connid'] == connection_id]

        if len(created_ports) == 1:
            # Create OVS ports
            p = created_ports[0]
            vlan_number = str(p['vlan_number'])
            if vlan_number in ovs_ports_dict:
                print '\nPort with vlan {0} already exists: {1}'.format(vlan_number, ovs_ports_dict[vlan_number])
                network_port_dict[net_id] = p
                # Go to next network
                continue

            subnets = vtc_ui_client.get_overlay_network_subnets(net['id'])
            if len(subnets) == 0:
                print "\nThere are not subnets. Skipping network {0}".format(net)

            port_name = 'vlan{0}'.format(vlan_number)
            port_network = netaddr.IPNetwork(subnets[0]['cidr'])
            port_ip = port_network[254]
            local('sudo ovs-vsctl add-port br-tenant {0} -- set interface {0} type=internal'.format(port_name))
            local('sudo ovs-vsctl set port {0} tag={1}'.format(port_name, vlan_number))
            local('sudo ip address add {ip}/{cidr} dev {port_name}'.format(ip=port_ip, cidr=port_network.prefixlen, port_name=port_name))
            local('sudo ip link set dev {0} up'.format(port_name))
            _uuid = _local('sudo ovs-vsctl find port name={0}'.format(port_name))[0].split(':')[1].strip()

            ovs_ports_dict[vlan_number] = {'_uuid': _uuid, 'name': port_name, 'vlan_number': vlan_number}
            network_port_dict[net_id] = p


def delete_ports(vtc_ui_client, device_name, device_port, ovs_bridge):
    ethernet_mappings = vtc_ui_client.get_ethernet_mappings(device_name)
    connection_id = get_vtc_host_connection_id(vtc_ui_client, device_name, device_port)
    for em in ethernet_mappings:
        if em['name'] == device_port:
            connection_id = em['connid']
            break
    if not connection_id:
        raise Exception('Could not fond interface {0} of {1}. Check hosts inventory'.format(device_port, device_name))

    ovs_ports_dict = get_ovs_ports(ovs_bridge)
    cur_networks = {net['id']: net for net in vtc_ui_client.get_overlay_networks()}
    for net_id, net in cur_networks.iteritems():
        cur_ports = vtc_ui_client.get_overlay_network_ports(net_id)
        created_ports = [p for p in cur_ports if p['connid'] == connection_id]
        for p in created_ports:
            vlan_number = str(p['vlan_number'])
            if vlan_number in ovs_ports_dict:
                local('sudo ovs-vsctl del-port {0} {1}'.format(ovs_bridge, ovs_ports_dict[vlan_number]['name']))
                vtc_ui_client.delete_network_port(p['id'])
