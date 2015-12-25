def start(context, log, args):
    from fabric.api import settings

    node_name = args['node_name']
    process = args['process']
    signal = args.get('signal', 9)

    server = context.particular_node(node_name)
    with settings(warn_only=True):
        res = server.run('sudo pkill --signal {0} {1}'.format(signal, process))

    log.info('Killing process {0} on server {1}. Result {2}'.format(process, node_name, res.return_code))
