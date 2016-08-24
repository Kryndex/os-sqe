from lab.deployers import Deployer


class DeployerVts(Deployer):

    def sample_config(self):
        return {'images-location': 'http://172.29.173.233/vts/nightly-2016-03-14/', 'rhel-subsription-creds': 'http://172.29.173.233/redhat/subscriptions/rhel-subscription-chandra.json'}

    def __init__(self, config):
        super(DeployerVts, self).__init__(config=config)

        self._rhel_creds_source = config['rhel-subsription-creds']

        self._vts_service_dir = '/tmp/vts_preparation'

        self._libvirt_domain_tmpl = self.read_config_from_file(config_path='domain_template.txt', directory='libvirt', is_as_string=True)
        self._disk_part_tmpl = self.read_config_from_file(config_path='disk-part-of-libvirt-domain.template', directory='vts', is_as_string=True)

        self._vts_images_location = config['images-location']

    def deploy_vts(self, list_of_servers):
        from lab.vts_classes.vtf import Vtf
        from lab.vts_classes.vtc import VtsHost
        from lab.cimc import CimcController

        vts_hosts = filter(lambda host: type(host) in [VtsHost, CimcController], list_of_servers)
        if not vts_hosts:  # use controllers as VTS hosts if no special servers for VTS provided
            raise RuntimeError('Neither specival VTS hosts no controllers was provided')

        for vts_host in vts_hosts:
            self._install_needed_rpms(vts_host)
            self._make_netsted_libvirt(vts_host=vts_host)
            self._make_openvswitch(vts_host)

        for vts_host in vts_hosts:
            vtc = [x.get_peer_node(vts_host) for x in vts_host.get_all_wires() if x.get_peer_node(vts_host).is_vtc()][0]

            self.deploy_single_vtc(vts_host=vts_host, vtc=vtc)

        self.make_cluster(lab=vts_hosts[0].lab())

        vtcs = []
        xrncs = []
        for vts_host in vts_hosts:
            vtc = [x.get_peer_node(vts_host) for x in vts_host.get_all_wires() if x.get_peer_node(vts_host).is_vtc()][0]
            vtcs.append(vtc)
            xrnc = [x.get_peer_node(vts_host) for x in vts_host.get_all_wires() if x.get_peer_node(vts_host).is_xrvr()][0]
            xrncs.append(xrnc)
            self.deploy_single_xrnc(vts_host=vts_host, vtc=vtc, xrnc=xrnc)
        vtcs[0].get_all_logs('after_all_xrvr_registered')

        dl_server_status = map(lambda dl: dl.xrnc_start_dl(), xrncs)  # https://cisco.jiveon.com/docs/DOC-1455175 Step 11
        if not all(dl_server_status):
            raise RuntimeError('Failed to start DL servers')
        vtcs[0].get_all_logs('after_all_dl_servers_started')

        vtcs[0].vtc_day0_config()
        vtcs[0].get_all_logs('after_day0_config')

        for vtf in filter(lambda y: type(y) is Vtf, list_of_servers):  # mercury-VTS this list is empty
            self.deploy_single_vtf(vtf)

        if not self.is_valid_installation(vts_hosts):
            raise RuntimeError('VTS installation is invalid')

    def _delete_previous_libvirt_vms(self, vts_host):
        ans = vts_host.run('virsh list')
        for role in ['xrnc', 'vtc']:
            if role in ans:
                vts_host.run('virsh destroy {role}'.format(role=role))
        ans = vts_host.run('virsh list --all')
        for role in ['xrnc', 'vtc']:
            if role in ans:
                vts_host.run('virsh undefine {role}'.format(role=role))
        vts_host.run('rm -rf {}'.format(self._vts_service_dir))

    def make_cluster(self, lab):
        from lab.vts_classes.vtc import Vtc

        vtc_list = lab.get_nodes_by_class(Vtc)
        if vtc_list[0].check_cluster_is_formed():
            self.log('Cluster is already formed')
            return

        cisco_bin_dir = '/opt/cisco/package/vtc/bin/'
        for vtc in vtc_list:
            cfg_body = vtc.get_cluster_conf_body()  # https://cisco.jiveon.com/docs/DOC-1443548 VTS 2.2: L2 HA Installation Steps  Step 1
            vtc.put_string_as_file_in_dir(string_to_put=cfg_body, file_name='cluster.conf', in_directory=cisco_bin_dir)
            vtc.run(command='sudo ./modify_host_vtc.sh', in_directory=cisco_bin_dir)  # https://cisco.jiveon.com/docs/DOC-1443548 VTS 2.2: L2 HA Installation Steps  Step 2
        for vtc in vtc_list:
            vtc.run(command='sudo ./cluster_install.sh', in_directory=cisco_bin_dir)  # https://cisco.jiveon.com/docs/DOC-1443548 VTS 2.2: L2 HA Installation Steps  Step 3

        vtc_list[0].run(command='sudo ./master_node_install.sh', in_directory=cisco_bin_dir)  # https://cisco.jiveon.com/docs/DOC-1443548 VTS 2.2: L2 HA Installation Steps  Step 4

        if vtc_list[0].check_cluster_is_formed(n_retries=100):
            return  # cluster is successfully formed
        else:
            raise RuntimeError('Failed to form VTC cluster after 100 attempts')

    def _install_needed_rpms(self, vts_host):
        if self._vts_images_location not in vts_host.run(command='cat VTS-VERSION', warn_only=True):
            self.log('Installing  needed RPMS...')
            vts_host.register_rhel(self._rhel_creds_source)
            vts_host.run(command='sudo yum update -y')
            vts_host.run('yum groupinstall "Virtualization Platform" -y')
            vts_host.run('yum install genisoimage qemu-kvm expect -y')
            for rpm in ['sshpass-1.05-1.el7.rf.x86_64.rpm', 'openvswitch-2.5.0-1.el7.centos.x86_64.rpm']:
                vts_host.run('wget http://172.29.173.233/redhat/{}'.format(rpm))
                vts_host.run(command='rpm -ivh {}'.format(rpm), warn_only=True)
                vts_host.run(command='rm -f {}'.format(rpm))

            vts_host.run('systemctl start libvirtd')
            vts_host.run('systemctl start openvswitch')
            vts_host.put_string_as_file_in_dir(string_to_put='VTS from {}\n'.format(self._vts_images_location), file_name='VTS-VERSION')
            self.log('RPMS are installed')
        else:
            self.log('All needed RPMS are already installed in the previous run')

    @staticmethod
    def _make_netsted_libvirt(vts_host):
        if vts_host.run('cat /sys/module/kvm_intel/parameters/nested') == 'N':
            vts_host.run('echo "options kvm-intel nested=1" | sudo tee /etc/modprobe.d/kvm-intel.conf')
            vts_host.run('rmmod kvm_intel')
            vts_host.run('modprobe kvm_intel')
            if vts_host.run('cat /sys/module/kvm_intel/parameters/nested') != 'Y':
                raise RuntimeError('Failed to set libvirt to nested mode')

    def _make_openvswitch(self, vts_host):
        for nic_name in ['a', 'mx', 't']:
            nic = vts_host.get_nic(nic_name)
            if 'br-{}'.format(nic.get_name()) not in vts_host.run('ovs-vsctl show'):
                vts_host.run('ovs-vsctl add-br br-{0} && ip l s dev br-{0} up'.format(nic.get_name()))
                ip_nic, _ = nic.get_ip_and_mask()
                net_bits = nic.get_net().get_prefix_len()
                default_route_part = '&& ip r a default via {}'.format(nic.get_net().get_gw()) if nic.is_ssh() else ''
                vts_host.run('ip a flush dev {n} && ip a a {ip}/{nb} dev br-{n} && ovs-vsctl add-port br-{n} {n} {rp}'.format(n=nic.get_name(), ip=ip_nic, nb=net_bits, rp=default_route_part))
                vts_host.run('ip l a dev vlan{} type dummy'.format(nic.get_vlan()))
                vts_host.run('ovs-vsctl add-port br-{} vlan{}'.format(nic.get_name(), nic.get_vlan()))
                vts_host.run('ovs-vsctl set interface vlan{} type=internal'.format(nic.get_vlan()))
                vts_host.run('ip l s dev vlan{} up'.format(nic.get_vlan()))
            else:
                self.log('Bridge br-{} is already created in the previous run'.format(nic_name))

    def deploy_single_vtc(self, vts_host, vtc):
        if not vtc.ping():
            self._delete_previous_libvirt_vms(vts_host=vts_host)
            cfg_body, net_part = vtc.get_config_and_net_part_bodies()

            config_iso_path = self._vts_service_dir + '/vtc_config.iso'
            config_txt_path = vts_host.put_string_as_file_in_dir(string_to_put=cfg_body, file_name='config.txt', in_directory=self._vts_service_dir)
            vts_host.run('mkisofs -o {iso} {txt}'.format(iso=config_iso_path, txt=config_txt_path))

            self._get_image_and_run_virsh(server=vts_host, role='vtc', iso_path=config_iso_path, net_part=net_part)
            vtc.vtc_change_default_password()
        else:
            self.log('Vtc {} is already deployed in the previous run.'.format(vtc))

    def deploy_single_xrnc(self, vts_host, vtc, xrnc):
        if not xrnc.ping():

            cfg_body, net_part = xrnc.get_config_and_net_part_bodies()
            iso_path = self._vts_service_dir + '/xrnc_cfg.iso'

            cfg_name = 'xrnc.cfg'
            vtc.put_string_as_file_in_dir(string_to_put=cfg_body, file_name=cfg_name)
            vtc.run('cp /opt/cisco/package/vts/bin/build_vts_config_iso.sh $HOME')
            vtc.run('./build_vts_config_iso.sh xrnc {}'.format(cfg_name))  # https://cisco.jiveon.com/docs/DOC-1455175 step 8: use sudo /opt/cisco/package/vts/bin/build_vts_config_iso.sh xrnc xrnc.cfg
            ip, username, password = vtc.get_ssh()
            vts_host.run('sshpass -p {p} scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null {u}@{ip}:xrnc_cfg.iso {d}'.format(p=password, u=username, ip=ip, d=self._vts_service_dir))

            self._get_image_and_run_virsh(server=vts_host, role='xrnc', iso_path=iso_path, net_part=net_part)
        else:
            self.log('XRNC {} is already deployed in the previous run')

    def _get_image_and_run_virsh(self, server, role, iso_path, net_part):
        image_url = self._vts_images_location + role + '.qcow2'
        qcow_file = server.wget_file(url=image_url, to_directory=self._vts_service_dir, checksum=None)

        disk_part = self._disk_part_tmpl.format(qcow_path=qcow_file, iso_path=iso_path)
        domain_body = self._libvirt_domain_tmpl.format(hostname=role, disk_part=disk_part, net_part=net_part, emulator='/usr/libexec/qemu-kvm')
        domain_xml_path = server.put_string_as_file_in_dir(string_to_put=domain_body, file_name='{0}_domain.xml'.format(role), in_directory=self._vts_service_dir)

        server.run('virsh define {xml} && virsh start {role} && virsh autostart {role}'.format(xml=domain_xml_path, role=role))

    def deploy_single_vtf(self, vtf):
        net_part, cfg_body = vtf.get_domain_andconfig_body()
        compute = vtf.get_ocompute()
        config_iso_path = self._vts_service_dir + '/vtc_config.iso'
        config_txt_path = compute.put_string_as_file_in_dir(string_to_put=cfg_body, file_name='system.cfg', in_directory=self._vts_service_dir)
        compute.run('mkisofs -o {iso} {txt}'.format(iso=config_iso_path, txt=config_txt_path))
        self._get_image_and_run_virsh(server=compute, role='vtf', iso_path=config_iso_path, net_part=net_part)

        # on VTC ncs_cli: configure set devices device XT{TAB} asr -- bgp[TAB] bgp-asi 23 commit
        # on DL ps -ef | grep dl -> then restart dl_vts_reg.py

    def wait_for_cloud(self, list_of_servers):
        self.deploy_vts(list_of_servers=list_of_servers)

    @staticmethod
    def is_valid_installation(vts_hosts):
        import requests

        from lab.vts_classes.vtc import Vtc

        vtc = vts_hosts[0].lab().get_nodes_by_class(Vtc)[0]
        try:
            return len(vtc.vtc_get_xrvrs()) == 2
        except requests.exceptions.ConnectTimeout:
            return False
