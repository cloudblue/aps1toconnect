import json
import sys

from xml.etree import ElementTree as xml_et

import osaapi
import requests

from aps1toconnect.config import get_config, CFG_FILE_PATH

RPC_CONNECT_PARAMS = ('host', 'user', 'password', 'ssl', 'port')
APS_CONNECT_PARAMS = ('aps_host', 'aps_port', 'use_tls_aps')


def json_decode(content):
    if sys.version_info.major < 3 or (
            sys.version_info.major == 3 and sys.version_info.minor < 6
    ):
        return json.loads(content.decode('utf-8'))

    return json.loads(content)


def osaapi_raise_for_status(r):
    if r['status']:
        if 'error_message' in r:
            raise Exception("Error: {}".format(r['error_message']))
        else:
            raise Exception("Error: Unknown {}".format(r))


def apsapi_raise_for_status(r):
    try:
        r.raise_for_status()
    except Exception as e:
        if 'error' in r.json():
            err = "{} {}".format(r.json()['error'], r.json()['message'])
        else:
            err = str(e)
        print("Hub APS API response {} code.\n"
              "Error: {}".format(r.status_code, err))
        sys.exit(1)

class Hub(object):
    osaapi = None
    aps = None
    hub_id = None
    extension_id = None

    def __init__(self):
        config = get_config()
        self.osaapi = osaapi.OSA(**{k: config[k] for k in RPC_CONNECT_PARAMS})
        self.aps = APS(self.get_admin_token())
        self.hub_id = self._get_id()

    @staticmethod
    def configure(hub_host, user='admin', pwd='1q2w3e', use_tls=False, port=8440, aps_host=None,
                  aps_port=6308, use_tls_aps=True):
        if not aps_host:
            aps_host = hub_host
        use_tls = use_tls in ('Yes', 'True', '1')
        hub = osaapi.OSA(host=hub_host, user=user, password=pwd, ssl=use_tls, port=port)
        try:
            hub_version = Hub._get_hub_version(hub)
            print("Connectivity with Hub RPC API [ok]")
            aps_url = '{}://{}:{}'.format('https' if use_tls_aps else 'http', aps_host, aps_port)
            aps = APS(Hub._get_user_token(hub, 1), aps_url)
            response = aps.get('aps/2/applications/')
            response.raise_for_status()
            print("Connectivity with Hub APS API [ok]")

        except Exception as e:
            print("Unable to communicate with hub {}, error: {}".format(hub_host, e))
            sys.exit(1)

        else:
            with open(CFG_FILE_PATH, 'w+') as cfg:
                cfg.write(json.dumps({'host': hub_host, 'user': user, 'password': pwd,
                                      'ssl': use_tls, 'port': port, 'aps_port': aps_port,
                                      'aps_host': aps_host, 'use_tls_aps': use_tls_aps},
                                     indent=4))
                print("Config saved [{}]".format(CFG_FILE_PATH))


    @staticmethod
    def _get_hub_version(api):
        r = api.statistics.getStatisticsReport(reports=[{'name': 'BuildHistory', 'value': ''}])
        osaapi_raise_for_status(r)
        tree = xml_et.fromstring(r['result'][0]['value'])
        return tree.find('Build/Build').text

    @staticmethod
    def _get_user_token(hub, user_id):
        r = hub.APS.getUserToken(user_id=user_id)
        osaapi_raise_for_status(r)
        return {'APS-Token': r['result']['aps_token']}

    @staticmethod
    def _get_application_token(hub, instance_id):
        r = hub.APS.getApplicationInstanceToken(application_instance_id=instance_id)
        osaapi_raise_for_status(r)
        return {'APS-Token': r['result']['aps_token']}

    def _get_id(self):
        url = 'aps/2/resources?implementing(http://parallels.com/aps/types/pa/poa/1.0)'
        r = self.aps.get(url)
        r.raise_for_status()

        try:
            data = json_decode(r.content)
        except ValueError:
            print("APSController provided non-json format")
            sys.exit(1)
        else:
            return data[0]['aps']['id'] if data else None

    @staticmethod
    def _get_resclass_name(unit):
        resclass_name = {
            'Kbit/sec': 'rc.saas.resource.kbps',
            'kb': 'rc.saas.resource',
            'mb-h': 'rc.saas.resource.mbh',
            'mhz': 'rc.saas.resource.mhz',
            'mhzh': 'rc.saas.resource.mhzh',
            'unit': 'rc.saas.resource.unit',
            'unit-h': 'rc.saas.resource.unith'
        }.get(unit)

        return resclass_name or 'rc.saas.resource.unit'

    def get_admin_token(self):
        return Hub._get_user_token(self.osaapi, 1)

    def get_application_id(self, package_id):
        payload = {
            'aps_application_id': package_id,
        }

        r = self.osaapi.aps.getApplications(**payload)
        osaapi_raise_for_status(r)

        if len(r['result']) == 0:
            return None
        return r['result'][0]['application_id'] or None

    def get_application_instances(self, application_id):
        payload = {
            'app_id': application_id,
        }

        r = self.osaapi.aps.getApplicationInstances(**payload)
        osaapi_raise_for_status(r)

        return r['result']

    def get_application_instance(self, instance_id):
        payload = {
            'application_instance_id': instance_id
        }
        r = self.osaapi.aps.getApplicationInstance(**payload)
        osaapi_raise_for_status(r)
        return r['result']

    def get_application_settings(self, instance_id):
        payload = {
            'application_instance_id': instance_id
        }
        r = self.osaapi.aps.getApplicationInstanceSettings(**payload)
        osaapi_raise_for_status(r)
        return r['result']

    def get_applications(self, aps_application_id):
        payload = {
            'aps_application_id': aps_application_id
        }

        r = self.osaapi.APS.getApplications(**payload)

        osaapi_raise_for_status(r)

        if len(r['result']) == 0:
            return None
        return r['result'][0]['application_id'] or None


class APS(object):
    url = None
    token = None

    def __init__(self, token, url=None):
        if url:
            self.url = url
        else:
            config = get_config()
            self.url = APS._get_aps_url(**{k: config[k] for k in APS_CONNECT_PARAMS})
        self.token = token

    @staticmethod
    def _get_aps_url(aps_host, aps_port, use_tls_aps):
        return '{}://{}:{}'.format('https' if use_tls_aps else 'http', aps_host, aps_port)

    def get(self, uri):
        return requests.get('{}/{}'.format(self.url, uri), headers=self.token, verify=False)

    def post(self, uri, json=None, subscription=None):
        headers = self.token
        if subscription:
            headers['APS-Subscription-ID'] = subscription
        return requests.post('{}/{}'.format(self.url, uri), headers=headers, json=json,
                             verify=False)

    def put(self, uri, json=None):
        return requests.put('{}/{}'.format(self.url, uri), headers=self.token, json=json,
                            verify=False)

    def delete(self, uri):
        return requests.delete('{}/{}'.format(self.url, uri), headers=self.token, verify=False)
