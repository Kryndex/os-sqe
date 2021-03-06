from lab.with_log import WithLogMixIn
from lab import decorators


class OS(WithLogMixIn):
    def __repr__(self):
        return u'cloud {}'.format(self.name)

    def __init__(self, name, mediator, openrc_path):
        from lab.mercury.nodes import MercuryMgm

        self.name = name
        self.mediator = mediator    # Instance of Server to be used to execute CLI commands for this cloud
        if type(mediator) is MercuryMgm:
            self.pod = mediator.pod
        self.openrc_path = openrc_path
        self.controls, self.computes, self.images, self.servers, self.keypairs, self.nets, self.subnets, self.ports, self.flavors = [], [], [], [], [], [], [], [], []

    def os_cmd(self, cmds, comment='', server=None, is_warn_only=False):
        server = server or self.mediator
        cmd = 'source ' + self.openrc_path + ' && ' + ' ; '.join(cmds) + (' # ' + comment if comment else '')
        ans = server.exe(cmd=cmd, is_warn_only=is_warn_only)
        if ans:
            return self._process_output(answer=ans)
        else:
            return []

    @staticmethod
    def _process_output(answer):
        import json

        if not answer:
            return {}
        if '{' not in answer:
            return answer.split('\r\n')
        answer = '[' + answer.replace('}{', '},{') + ']'
        return json.loads(answer)

    def get_fip_network(self):
        ans = self.os_cmd('neutron net-list --router:external=True -c name')
        net_names = ans
        if not net_names:
            return '', 'physnet1'
        fip_network = net_names[0]
        ans = self.os_cmd('neutron net-list -c provider:physical_network --name {0}'.format(fip_network))
        physnet = filter(lambda x: x not in ['provider:physical_network', '|', '+---------------------------+'], ans.split())[0]
        return fip_network, physnet

    @staticmethod
    def _parse_cli_output(output_lines):
        """Borrowed from tempest-lib. Parse single table from cli output.
        :param output_lines:
        :returns: dict with list of column names in 'headers' key and rows in 'values' key.
        """

        if not isinstance(output_lines, list):
            output_lines = output_lines.split('\n')

        if not output_lines[-1]:
            output_lines = output_lines[:-1]  # skip last line if empty (just newline at the end)

        columns = OS._table_columns(output_lines[2])
        is_2_values = len(columns) == 2

        def line_to_row(input_line):
            return [input_line[col[0]:col[1]].strip() for col in columns]

        headers = None
        d2 = {}
        output_list = []
        for line in output_lines[3:-1]:
            row = line_to_row(line)
            if is_2_values:
                d2[row[0]] = row[1]
            else:
                headers = headers or line_to_row(output_lines[1])
                output_list.append({headers[i]: row[i] for i in range(len(columns))})

        return output_list if output_list else [d2]

    @staticmethod
    def _table_columns(first_table_row):
        """Borrowed from tempest-lib. Find column ranges in output line.
        :returns: list of tuples (start,end) for each column detected by plus (+) characters in delimiter line.
        """
        positions = []
        start = 1  # there is '+' at 0
        while start < len(first_table_row):
            end = first_table_row.find('+', start)
            if end == -1:
                break
            positions.append((start, end))
            start = end + 1
        return positions

    def os_create_fips(self, fip_net, how_many):
        cmds = ['neutron floatingip-create ' + fip_net.get_net_name() for _ in range(how_many)]
        return self.os_cmd(cmds=cmds)

    def os_quota_set(self):
        from lab.cloud.cloud_project import CloudProject

        admin_id = [x.id for x in CloudProject.list(cloud=self) if x.name == 'admin'][0]
        self.os_cmd(cmds=['openstack quota set --instances 1000 --cores 2000 --ram 512000 --networks 100 --subnets 300 --ports 500 {}'.format(admin_id)])

    @decorators.section('Clean cloud')
    def os_cleanup(self, is_all=False):
        from lab.cloud import CloudObject

        f = '' if is_all else CloudObject.UNIQUE_PATTERN_IN_NAME
        objs = filter(lambda x: f in x.name, self.servers + self.ports + self.subnets + self.nets + self.images + self.keypairs + self.flavors)

        ids = map(lambda x: '"' + x.role + ' delete ' + x.id + '"', objs)
        if not ids:  # nothing to clean
            return
        names = map(lambda x: x.name, objs)
        cmd = 'for cmd in ' + ' '.join(ids) + '; do openstack $cmd; done'
        self.os_cmd(cmds=[cmd], comment=' '.join(names))
        self.os_all()

    @decorators.section('Investigate cloud')
    def os_all(self):
        from lab.cloud.cloud_host import CloudHost
        from lab.cloud.cloud_server import CloudServer
        from lab.cloud.cloud_image import CloudImage
        from lab.cloud.cloud_network import CloudNetwork
        from lab.cloud.cloud_subnet import CloudSubnet
        from lab.cloud.cloud_key_pair import CloudKeyPair
        from lab.cloud.cloud_flavor import CloudFlavor

        self.controls, self.computes = CloudHost.host_list(cloud=self)

        self.images, self.servers, self.ports, self.subnets, self.nets, self.keypairs, self.flavors = [], [], [], [], [], [], []
        pattern = 'openstack {0} list | grep  -vE "\+|ID|Fingerprint" {{}} | cut -d " " -f 2 | while read id; do [ -n "$id" ] && openstack {0} {{}} $id -f json; done'
        cmds = map(lambda x: x.format('', 'show'), map(lambda x: pattern.format(x), ['image', 'network', 'subnet', 'port', 'keypair', 'server', 'flavor']))
        a = self.os_cmd(cmds=cmds, is_warn_only=True)
        count = 0
        for dic in a:
            count += 1
            if 'disk_format' in dic:
                CloudImage(cloud=self, dic=dic)
            elif 'hostId' in dic:
                CloudServer(cloud=self, dic=dic)
            elif 'fingerprint' in dic:
                CloudKeyPair(cloud=self, dic=dic)
            elif 'provider:network_type' in dic:
                CloudNetwork(cloud=self, dic=dic)
            elif 'subnetpool_id' in dic:
                CloudSubnet(cloud=self, dic=dic)
            elif 'os-flavor-access:is_public' in dic:
                CloudFlavor(cloud=self, dic=dic)
            else:
                raise RuntimeError('{}: add this ^^^ dic to this if!')
        return count
