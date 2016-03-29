import tempfile
from lab import with_config


class Laboratory(with_config.WithConfig):
    SUPPORTED_TOPOLOGIES = ['VLAN', 'VXLAN']
    TOPOLOGY_VLAN, TOPOLOGY_VXLAN = SUPPORTED_TOPOLOGIES

    temp_dir = tempfile.mkdtemp(prefix='runner-ha-')

    def sample_config(self):
        pass

    def __repr__(self):
        return self._lab_name

    def __init__(self, config_path):
        from netaddr import IPNetwork

        super(Laboratory, self).__init__(config=None)

        with open(with_config.KEY_PUBLIC_PATH) as f:
            self.public_key = f.read()
        self._nodes = dict()
        self._cfg = self.read_config_from_file(config_path=config_path)
        self._id = self._cfg['lab-id']
        self._lab_name = self._cfg['lab-name']
        self._user_net = IPNetwork(self._cfg['nets']['user']['cidr'])
        self._ipmi_net = IPNetwork(self._cfg['nets']['ipmi']['cidr'])
        self._net_vlans = {net: [str(x) for x in val['vlan']] for net, val in self._cfg['nets'].iteritems()}
        self._is_sriov = self._cfg['use-sr-iov']

        self._unique_dict = dict()  # to make sure that all needed objects are unique

        self._ssh_username, self._ssh_password = self._cfg['cred']['ssh_username'], self._cfg['cred']['ssh_password']
        self._ipmi_username, self._ipmi_password = self._cfg['cred']['ipmi_username'], self._cfg['cred']['ipmi_password']
        self._neutron_username, self._neutron_password = self._cfg['cred']['neutron_username'], self._cfg['cred']['neutron_password']

        for name, wires in self._cfg['wires'].iteritems():
            self._process_single_connection(name=name, wires=wires)

    def is_sriov(self):
        return self._is_sriov

    def make_sure_that_object_is_unique(self, type_of_object, obj, node_name):
        """check that given object is valid and unique
        :param type_of_object: IPv4 MAC service-profile
        :param obj: object
        :param node_name: node which tries to register the object
        """
        import validators

        self._unique_dict.setdefault(type_of_object, set())
        if obj in self._unique_dict[type_of_object]:
            raise ValueError('{0} node is trying to use {1}={2} which is already in use by others'.format(node_name, type_of_object, obj))
        else:
            if type_of_object == 'MAC':
                is_ok = validators.mac_address(obj)
            elif type_of_object == 'IPv4':
                is_ok = validators.ipv4(obj)
            else:
                is_ok = True
            if not is_ok:
                raise ValueError('{0} is not valid {1}'.format(obj, type_of_object))
            self._unique_dict[type_of_object].add(obj)

    def _process_single_connection(self, name, wires):
        from lab.wire import Wire

        def get_port_id(dev_port):
            try:
                dev_role, dev_id, port = dev_port.split('-')
                return port
            except (UnboundLocalError, ValueError):
                raise KeyError('you provided {0} as full port id, while expected like Nexus-1-1/31'.format(dev_port))

        for s, n in wires.iteritems():  # sample value: Director-1-3/1:  FI-1-1/10
            port_s = get_port_id(s)
            port_n = get_port_id(n)
            node_n = self._get_or_create_node(n)  # first north node, servers could not be north
            node_s = self._get_or_create_node(s, node_n)  # now create south node which might be server

            Wire(node_n=node_n, num_n=port_n, node_s=node_s, num_s=port_s, name=name)

    @staticmethod
    def _get_role_class(role, peer_device):
        from lab.fi import FI
        from lab.n9k import Nexus
        from lab.asr import Asr
        from lab.tor import Tor
        from lab.cobbler import CobblerServer
        from lab.fi import FiServer
        from lab.cimc import CimcServer

        possible_roles = ['cobbler', 'nexus', 'asr', 'fi', 'director', 'control', 'compute', 'ceph', 'tor']

        if role in ['director', 'control', 'compute', 'ceph']:
            klass = FiServer if type(peer_device) is FI else CimcServer
        elif role == 'cobbler':
            klass = CobblerServer
        elif role == 'asr':
            klass = Asr
        elif role == 'nexus':
            klass = Nexus
        elif role == 'fi':
            klass = FI
        elif role == 'tor':
            klass = Tor
        else:
            raise ValueError(role + ' is not known,  should be one of: ' + ', '.join(possible_roles))
        return klass

    def _get_or_create_node(self, node_port, peer_device=None):
        from lab.fi import FI, FiServer
        from lab.cimc import CimcServer

        try:
            role, id_in_role, port = node_port.split('-')
        except (UnboundLocalError, ValueError):
            raise ValueError(node_port + ' is not of role-id-port pattern.')

        node_name = '-'.join([role, id_in_role])

        if node_name in self._nodes:  # if we're lucky , this node was created on previous call to this method
            return self.get_node(node_name)

        if not role.islower():
            raise ValueError(role + ' is not lower case.')

        try:
            node_description = self._cfg['nodes'][node_name]
        except KeyError:
            raise ValueError(node_name + ' is not found in section "nodes"')

        klass = self._get_role_class(role=role, peer_device=peer_device)

        ssh_username, ssh_password = node_description.get('username', self._ssh_username), node_description.get('password', self._ssh_password)
        ipmi_ip, ipmi_username, ipmi_password = 'No ipmi', node_description.get('ipmi_username', self._ipmi_username), node_description.get('ipmi_password', self._ipmi_password)
        hostname = node_description.get('hostname', 'NotConfiguredInYaml')

        if klass in [FiServer, CimcServer]:
            def get_ip_in_net_by_index(net):
                if ip_index_in_net in [0, 1, 2, 3, -1]:
                    raise ValueError('IP address index {0} is not possible since 0 is network , 1,2,3 are GWs and -1 is broadcast'.format(node_name))
                try:
                    return net[int(ip_index_in_net)]
                except (IndexError, ValueError):
                    raise ValueError('{0} index {1} is not in {2}'.format(ip_index_in_net, net))

            ip_index_in_net = node_description['ip']
            ssh_ip = get_ip_in_net_by_index(net=self._user_net)
            ipmi_ip = node_description.get('ipmi_ip', get_ip_in_net_by_index(net=self._ipmi_net))
        else:
            ssh_ip = node_description['ip']

        node = klass(lab=self, name=node_name, ip=ssh_ip, username=ssh_username, password=ssh_password, hostname=hostname)

        if klass in [FiServer, CimcServer]:
            node.set_ipmi(ip=ipmi_ip, username=ipmi_username, password=ipmi_password)

            if type(node) is FiServer:
                node.set_ucsm_id(port)

            for element in node_description['nets']:  # here element might be either just NIC name or {nic: mac}
                if type(element) is dict:
                    (nic_name, mac), = element.items()
                else:
                    nic_name, mac = element, node.form_mac(lab_id=self._id, net_octet=self._cfg['nets'][element]['mac-net-part'])
                self.make_sure_that_object_is_unique(type_of_object='MAC', obj=mac, node_name=node.name())
                node.add_nic(nic_name=nic_name, mac=mac)
        elif type(node) is FI:
            self._ucsm_vip = node_description['vip']
            node.set_vip(self._ucsm_vip)
        self._nodes[node_name] = node
        return node

    def get_id(self):
        return self._id

    def get_nodes(self, klass=None):
        if klass:
            return filter(lambda x: isinstance(x, klass), self._nodes.values())
        else:
            return self._nodes

    def get_node(self, name):
        return self._nodes[name]

    def get_fi(self):
        from lab.fi import FI

        return self.get_nodes(FI)

    def get_n9(self):
        from lab.n9k import Nexus

        return self.get_nodes(Nexus)

    def get_asr1ks(self):
        from lab.asr import Asr

        return self.get_nodes(Asr)

    def get_cobbler(self):
        return self._nodes['cobbler-1']

    def get_director(self):
        return self._nodes['director-1']

    def _get_servers_for_role(self, role):
        keys = filter(lambda x: role in x, self._nodes.keys())
        return [self._nodes[key] for key in keys]

    def get_controllers(self):
        return self._get_servers_for_role('control')

    def get_computes(self):
        return self._get_servers_for_role('compute')

    def get_cimc_servers(self):
        from lab.cimc import CimcServer

        return self.get_nodes(klass=CimcServer)

    def get_user_net_info(self):
        return self._user_net.cidr, str(self._user_net[1]), self._user_net.netmask, str(self._user_net[4]), str(self._user_net[-3])

    def get_ipmi_net_info(self):
        return self._ipmi_net.cidr, str(self._ipmi_net[1]), self._ipmi_net.netmask, str(self._ipmi_net[4]), str(self._ipmi_net[-3])

    def get_all_vlans(self):
        return sorted(set(reduce(lambda l, x: l + (x['vlan']), self._cfg['nets'].values(), [])))

    def get_net_vlans(self, net_name):
        return self._net_vlans[net_name]

    def get_neutron_creds(self):
        return self._neutron_username, self._neutron_password

    def ucsm_nets_with_pxe(self):
        return [x for x in self._cfg['nets'].keys() if 'pxe' in x]

    def vlan_range(self):
        return self._cfg['vlan_range']

    def count_role(self, role_name):
        return len([x for x in self._nodes.keys() if role_name in x])

    def logstash_creds(self):
        return self._cfg['logstash']

    def configure_for_osp7(self, topology=TOPOLOGY_VLAN):
        if topology not in self.SUPPORTED_TOPOLOGIES:
            raise ValueError('"{0}" topology is not supported. Correct values: {1}'.format(topology, self.SUPPORTED_TOPOLOGIES))
        self.get_cobbler().configure_for_osp7()
        map(lambda x: x.cleanup(), self.get_n9())
        map(lambda x: x.configure_for_osp7(topology), self.get_n9())
        map(lambda x: x.configure_for_osp7(), self.get_cimc_servers())
        map(lambda x: x.configure_for_osp7(topology), self.get_asr1ks())
        self.get_fi()[0].configure_for_osp7()
        self.create_config_file_for_osp7_install(topology)

    def configure_for_mercury(self):
        self.get_cobbler().configure_for_osp7()
        map(lambda x: x.configure_for_mercury(), self.get_n9())
        map(lambda x: x.configure_for_mercury(), self.get_cimc_servers())

    def create_config_file_for_osp7_install(self, topology=TOPOLOGY_VLAN):
        import os
        from lab.logger import lab_logger
        from lab.with_config import read_config_from_file
        from lab.cimc import CimcServer

        lab_logger.info('Creating config for osp7_bootstrap')
        osp7_install_template = read_config_from_file(yaml_path='./configs/osp7/osp7-install.yaml', is_as_string=True)

        # Calculate IPs for user net, VIPs and director IP
        overcloud_network_cidr, overcloud_external_gateway, overcloud_external_ip_start, overcloud_external_ip_end = self._user_net.cidr, self._user_net[1], self._user_net[4+1], self._user_net[-3]

        eth0_mac_versus_service_profile = {}
        overcloud_section = []

        for server in self.get_controllers() + self.get_computes():
            service_profile_name = '""' if isinstance(server, CimcServer) else server.get_ucsm_info()[1]

            try:
                eth0_nic = server.get_nic(nic='eth0')[0]
            except IndexError:
                raise ValueError('{0} has no eth0'.format(server.name()))

            eth0_mac = eth0_nic.get_mac()
            eth0_mac_versus_service_profile[eth0_mac] = service_profile_name

            try:
                pxe_int_nic = server.get_nic(nic='pxe-int')[0]
            except IndexError:
                raise ValueError('{0} has no pxe-int'.format(server.name()))

            pxe_mac = pxe_int_nic.get_mac()
            ipmi_ip, ipmi_username, ipmi_password = server.get_ipmi()
            role = server.name().split('-')[0]
            descriptor = {'"arch"': '"x86_64"', '"cpu"': '"2"', '"memory"': '"8256"', '"disk"': '"1112"',
                          '"name"': '"{0}"'.format(server.name()),
                          '"capabilities"':  '"profile:{0},boot_option:local"'.format(role),
                          '"mac"': '["{0}"]'.format(pxe_mac),
                          '"pm_type"': '"pxe_ipmitool"',
                          '"pm_addr"': '"{0}"'.format(ipmi_ip),
                          '"pm_user"': '"{0}"'.format(ipmi_username),
                          '"pm_password"': '"{0}"'.format(ipmi_password)}
            overcloud_section.append(',\n\t  '.join(['{0}:{1}'.format(x, y) for x, y in sorted(descriptor.iteritems())]))

        network_ucsm_host_list = ','.join(['{0}:{1}'.format(name, mac) for name, mac in eth0_mac_versus_service_profile.iteritems()])

        overcloud_nodes = '{{"nodes":[\n\t{{\n\t  {0}\n\t}}\n    ]\n }}'.format('\n\t},\n\t{\n\t  '.join(overcloud_section))

        nexus_section = []
        switch_tempest_section = []
        for n9 in self.get_n9():
            common_pcs_part = ': {"ports": "port-channel:' + str(n9.get_peer_link_id())  # all pcs n9k-n9k and n9k-fi
            fi_pc_part = ',port-channel:' + ',port-channel:'.join(n9.get_pcs_to_fi())
            mac_port_lines = []
            for server in self.get_controllers() + self.get_computes():
                mac = server.get_nic('pxe-int')[0].get_mac()
                if isinstance(server, CimcServer):
                    individual_ports_part = ','.join([x.get_port_n() for x in server.get_all_wires() if x.get_node_n() == n9])  # add if wired to this n9k only
                    if individual_ports_part:
                        individual_ports_part = ',' + individual_ports_part
                else:
                    individual_ports_part = fi_pc_part
                mac_port_lines.append('"' + mac + '"' + common_pcs_part + individual_ports_part + '" }')

            nexus_servers_section = ',\n\t\t\t\t\t\t'.join(mac_port_lines)

            ssh_ip, ssh_username, ssh_password, hostname = n9.get_ssh()
            switch_tempest_section.append({'hostname': hostname, 'username': ssh_username, 'password': ssh_password, 'sw': ssh_ip})
            n9k_description = ['"' + hostname + '": {',
                               '"ip_address": "' + ssh_ip + '",',
                               '"username": "' + ssh_username + '",',
                               '"password": "' + ssh_password + '",',
                               '"nve_src_intf": 2,',
                               '"ssh_port": 22,',
                               '"physnet": "datacentre",',
                               '"servers": {' + nexus_servers_section + '}}',
                               ]
            nexus_section.append('\n\t\t\t'.join(n9k_description))

        network_nexus_config = '{\n\t\t' + ',\n\t\t'.join(nexus_section) + '}'

        n_controls, n_computes, n_ceph = self.count_role(role_name='control'), self.count_role(role_name='compute'), self.count_role(role_name='ceph')

        director_node_ssh_ip, _, _, director_hostname = self.get_director().get_ssh()

        pxe_int_vlans = self._cfg['nets']['pxe-int']['vlan']
        eth1_vlans = self._cfg['nets']['eth1']['vlan']
        ext_vlan, test_vlan, stor_vlan, stor_mgmt_vlan, tenant_vlan, fip_vlan = eth1_vlans[1], pxe_int_vlans[1], pxe_int_vlans[2], pxe_int_vlans[3], pxe_int_vlans[4], eth1_vlans[0]
        cfg = osp7_install_template.format(director_node_hostname=director_hostname, director_node_ssh_ip=director_node_ssh_ip,

                                           ext_vlan=ext_vlan, test_vlan=test_vlan, stor_vlan=stor_vlan, stor_mgmt_vlan=stor_mgmt_vlan, tenant_vlan=tenant_vlan, fip_vlan=fip_vlan,
                                           vlan_range=self.vlan_range(),

                                           overcloud_network_cidr=overcloud_network_cidr, overcloud_external_ip_start=overcloud_external_ip_start, overcloud_external_gateway=overcloud_external_gateway,
                                           overcloud_external_ip_end=overcloud_external_ip_end,

                                           overcloud_nodes=overcloud_nodes,

                                           overcloud_control_scale=n_controls, overcloud_ceph_storage_scale=n_ceph, overcloud_compute_scale=n_computes,

                                           network_ucsm_ip=self._ucsm_vip, network_ucsm_username=self._neutron_username, network_ucsm_password=self._neutron_password, network_ucsm_host_list=network_ucsm_host_list,

                                           undercloud_lab_pxe_interface='pxe-ext', undercloud_local_interface='pxe-int', undercloud_fake_gateway_interface='eth1',

                                           provisioning_nic='nic4', tenant_nic='nic1', external_nic='nic2',

                                           cobbler_system='G{0}-DIRECTOR'.format(self._id),

                                           network_nexus_config=network_nexus_config,

                                           switch_tempest_section=switch_tempest_section
                                           )

        if topology == self.TOPOLOGY_VXLAN:
            pass

        folder = 'artifacts'
        file_path = os.path.join(folder, 'g{0}-osp7-install-config.conf'.format(self._id))
        if not os.path.exists(folder):
            os.makedirs(folder)

        with open(file_path, 'w') as f:
            f.write(cfg)
        lab_logger.info('finished. Execute osp7_bootstrap --config {0}'.format(file_path))