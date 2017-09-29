from lab.with_config import WithConfig
from lab.with_log import WithLogMixIn


class TestCaseResult(WithLogMixIn):
    PASSED = 'passed'
    FAILED = 'failed'
    SKIPED = 'skipped'

    def __init__(self, tect_case):
        self.name = 'TCR ' + tect_case.path.split('-')[0] + ' '
        self.text = ''
        self.status = ''
        self.tims_url = ''

    def __repr__(self):
        return self.name + self.status.upper() + ' ' + self.tims_url


class TestCase(WithConfig, WithLogMixIn):

    def __init__(self, path, is_noclean, is_debug, cloud):
        import yaml
        import time

        self.path = path
        self.is_noclean = is_noclean
        self.is_debug = is_debug
        self.body_text = self.read_config_from_file(config_path=path, directory='ha', is_as_string=True)

        test_dic = yaml.load(self.body_text)
        must_be = {'Title', 'Folder', 'Description', 'UniqueID', 'Workers'}
        actual = set(test_dic.keys())
        assert actual == must_be, 'actual="{}", must be "{}"'.format(actual, must_be)
        assert test_dic['Folder'] in self.KNOWN_LABS['tims']['folders']

        self.title = test_dic['Title']
        self.folder = test_dic['Folder']
        self.description = test_dic['Description']
        self.unique_id = test_dic['UniqueID']

        self.workers = self.create_test_workers(test_dic.pop('Workers'))

        self.cloud = cloud
        self.time = time.time()  # time when the object was constructed

        self.tcr = None   # will be object of TestCaseResult() in self.after_run()
        self.tims_id = None
        self.tims_url = ''
        self.cloud.pod.tims.publish(self)  # sould be the last oper in ctor since tims.publish checks some of attributes

    def __repr__(self):
        return 'TC ' + self.path.split('-')[0] + ' ' + self.tims_url

    def set_tims_info(self, tims_id, url_tmpl):
        self.tims_id = tims_id
        self.tims_url = url_tmpl.format(tims_id, 'test case')

    def create_test_workers(self, workers_lst):
        import importlib

        assert type(workers_lst) is list and len(workers_lst) >= 1

        worker_names_already_seen = []
        workers = []
        for worker_dic in workers_lst:  # list of dicts
            klass = worker_dic['class']
            path_to_module, class_name = klass.rsplit('.', 1)
            try:
                mod = importlib.import_module(path_to_module)
            except ImportError:
                raise ValueError('{}: tries to run {}.py which does not exist'.format(self.path, path_to_module))
            try:
                klass = getattr(mod, class_name)
            except AttributeError:
                raise ValueError('Please create class {} in {}.py'.format(class_name, path_to_module))
            worker = klass(test_case=self, args_dict=worker_dic)
            if worker.name in worker_names_already_seen:
                worker.raise_exception('uses name which is not unique in this test')
            else:
                worker_names_already_seen.append(worker.name)
            workers.append(worker)

        for worker in workers:
            for attr_name in [worker.ARG_RUN, worker.ARG_DELAY]:
                value = getattr(worker, attr_name)
                if type(value) is int:
                    continue
                wrong_names = set(value) - set(worker_names_already_seen)
                assert len(wrong_names) == 0, '{}.{} has invalid names: "{}". Valid: {}'.format(worker, attr_name, wrong_names, worker_names_already_seen)
        return workers

    def after_run(self, results):
        import time

        self.time = time.time() - self.time
        self.tcr = TestCaseResult(tect_case=self)
        failes = []
        for w in results:
            self.tcr.text += str(w.worker_data)
            failes.append(w.is_failed)
            exceptions_text = '\n'.join(w.exceptions)
            if exceptions_text:
                self.tcr.text += exceptions_text
        self.tcr.status = self.tcr.FAILED if any(failes) else self.tcr.PASSED
        self.cloud.pod.tims.publish(tc=self)