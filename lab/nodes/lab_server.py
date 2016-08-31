from lab.nodes import LabNode
from lab.server import Server


class LabServer(LabNode, Server):

    def form_mac(self, pattern):
        pass

    def __init__(self, node_id, role, lab):
        self._tmp_dir_exists = False
        self._package_manager = None
        self._mac_server_part = None

        LabNode.__init__(self, node_id=node_id, role=role, lab=lab)
        Server.__init__(self, ip='Not defined in lab_server.py', username='Not defined in lab_server.py', password='Not defined in lab_server.py')

    def __repr__(self):
        ssh_ip, ssh_u, ssh_p = self.get_oob()
        oob_ip, oob_u, oob_p = self.get_oob()

        return u'{l} {n} | sshpass -p {p1} ssh {u1}@{ip1} ipmitool -I lanplus -H {ip2} -U {u2} -P {p2}'.format(l=self.lab(), n=self.get_id(), ip1=ssh_ip, p1=ssh_p, u1=ssh_u, ip2=oob_ip, p2=oob_p, u2=oob_u)

    def cmd(self, cmd):
        raise NotImplementedError

    def add_nic(self, nic_name, mac_or_pattern, ip_or_index, net, on_wires):
        import validators
        from lab.network import Nic

        ip_or_index = ip_or_index or self._assign_default_ip_index(net)

        try:
            index = int(ip_or_index)  # this is shift in the network
            if index in [0, 1, 2, 3, -1]:
                raise ValueError('{}:  index={} is not possible since 0 =>  network address [1,2,3] => GW addresses -1 => broadcast address'.format(self.get_id(), index))
            try:
                ip = net.get_ip_for_index(index)
            except (IndexError, ValueError):
                raise ValueError('{}: index {} is out of bound of {}'.format(self.get_id(), index, net))
        except ValueError:
            if validators.ipv4(str(ip_or_index)):
                try:
                    index, ip = {x: str(net.get_ip_for_index(x)) for x in range(net.get_size()) if str(ip_or_index) in str(net.get_ip_for_index(x))}.items()[0]
                except IndexError:
                    raise ValueError('{}: ip {} is out of bound of {}'.format(self.get_id(), ip_or_index, net))
            else:
                raise ValueError('{}: specified value "{}" is neither ip nor index in network'.format(self.get_id(), ip_or_index))

        self.lab().make_sure_that_object_is_unique(obj=ip, node_id=self.get_id())

        mac = mac_or_pattern if validators.mac_address(str(mac_or_pattern)) else self.form_mac(mac_or_pattern)

        self.lab().make_sure_that_object_is_unique(obj=mac, node_id=self.get_id())

        nic = Nic(name=nic_name, mac=mac, node=self, net=net, net_index=index, on_wires=on_wires)
        self._nics[nic_name] = nic
        if nic.is_ssh():
            self.set_ssh_ip(ip=nic.get_ip_and_mask()[0])
        return nic

    def is_nics_correct(self):
        actual_nics = self.list_ip_info(connection_attempts=1)
        if not actual_nics:
            return False

        for nic in self.get_nics().values():
            mac = nic.get_mac()  # be careful : after bonding all interfaces of the bond get mac of the first one
            ip, _ = nic.get_ip_and_mask()
            prefix_len = nic.get_net().get_prefix_len()
            ip = ip + '/' + str(prefix_len)
            master_nic_name = nic.get_name()
            if master_nic_name not in actual_nics:
                self.log(message='has no master NIC {}'.format(master_nic_name), level='warning')
                return False
            actual_ip = actual_nics[master_nic_name]['ipv4']
            if nic.is_pxe() is False and ip != actual_ip:  # this ip might be re-assign to the bridge which has this NIC inside
                br_name = 'br-' + master_nic_name
                if br_name not in actual_nics or ip != actual_nics[br_name]['ipv4']:
                    self.log(message='NIC "{}" has different IP  actual: {}  requested: {}'.format(nic.get_name(), actual_ip, ip), level='warning')
                    return False
            for slave_nic_name, _ in sorted(nic.get_slave_nics().items()):
                if slave_nic_name not in actual_nics:
                    self.log(message='has no slave NIC {}'.format(slave_nic_name), level='warning')
                    return False
                actual_mac = actual_nics[slave_nic_name]['mac'].upper()
                if actual_mac != mac.upper():
                    self.log(message='NIC {} has different mac: actual {} requested {}'.format(slave_nic_name, actual_mac, mac), level='warning')
                    return False
        return True