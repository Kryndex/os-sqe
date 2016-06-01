import logging
import os
from fabric.api import local, hide, settings

with hide('running', 'warnings'):
    with settings(warn_only=True):
        REPO_TAG = local('git describe --always', capture=True)

JENKINS_TAG = os.getenv('BUILD_TAG', 'NA')
OSP7_INFO = 'NA'

formatter = logging.Formatter(fmt='[%(asctime)s %(levelname)s] %(name)s:  %(message)s')

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(formatter)

file_handler = logging.FileHandler('iron-lady.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)


class JsonFormatter(logging.Formatter):
    def __init__(self):
        super(JsonFormatter, self).__init__()

    def format(self, record):
        import re
        import json

        def split_pairs():
            for key_value in re.split(pattern='[ ,]', string=record.message):
                if '=' in key_value:
                    key, value = key_value.split('=')
                    key = key.strip()
                    if not key:
                        continue
                    try:
                        value = int(value)
                    except ValueError:
                        value = value.strip()
                        if '@timestamp' not in key:
                            value = value.replace('-', '')
                            value = value.replace(':', '')
                    d[key] = value

        d = {}
        if record.exc_text:
            d['EXCEPTION'] = record.exc_text.replace('\n', '')
        split_pairs()
        if '@timestamp' not in d:
            d['@timestamp'] = self.formatTime(record=record, datefmt="%Y-%m-%dT%H:%M:%S.000Z")
        d['name'] = record.name
        d['sqe-repo-tag'] = REPO_TAG
        d['osp7-info'] = OSP7_INFO
        d['jenkins'] = JENKINS_TAG
        return json.dumps(d)


class JsonFilter(logging.Filter):
    def filter(self, record):
        return record.exc_text or '=' in record.message


json_handler = logging.FileHandler('json.log')
json_handler.setLevel(logging.DEBUG)
json_handler.setFormatter(JsonFormatter())
json_handler.addFilter(JsonFilter())


def create_logger(name=None):
    import inspect

    stack = inspect.stack()
    logger = logging.getLogger(name or stack[1][3])
    logger.setLevel(level=logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.addHandler(json_handler)
    return logger


lab_logger = create_logger(name='LAB-LOG')
