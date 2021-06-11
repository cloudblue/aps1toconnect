import json
import os
import sys
import uuid
import warnings
import traceback
from distutils.util import strtobool

import fire
import pkg_resources
from requests import get
from six.moves import input

from aps1toconnect.action_logger import Logger
from aps1toconnect.config import CFG_FILE_PATH, NULL_CFG_INFO
from aps1toconnect.hub import Hub
from aps1toconnect.migration_config import get_config
from aps1toconnect import constants
from connect.client import ConnectClient, ClientError, R
LOG_DIR = os.path.expanduser('~/.connect')

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE = os.path.join(LOG_DIR, "migration.log")

sys.stdout = Logger(LOG_FILE, sys.stdout)
sys.stdout.isatty = lambda: False
sys.stderr = Logger(LOG_FILE, sys.stderr)

warnings.filterwarnings('ignore')

IS_PYTHON3 = sys.version_info >= (3,)


class Migrator:
    def init_hub(self, hub_host, user='admin', pwd='1q2w3e', use_tls=False, port=8440,
                 aps_host=None, aps_port=6308, use_tls_aps=True):
        """ Connect your CloudBlue Commerce Instance (Hub)"""
        Hub.configure(hub_host, user, pwd, use_tls, port, aps_host, aps_port, use_tls_aps)

    def info(self):
        """ Show current state of migration binding with OA Hub"""
        print("OA Hub:")
        print(_check_binding(lambda: os.path.exists(CFG_FILE_PATH), _get_hub_info))

    def hub_token(self):
        """ Provides the ID of the Commerce Installation """
        hub = Hub()
        print(hub.hub_id)

    def initiate_migration(self):
        """ Starts migration process"""
        migration_config = get_config()
        hub = Hub()
        print("Migration config ok")
        mappings = _load_mappings(migration_config['RESOURCE_MAPPING'], hub.aps)
        app_instance_id = hub.get_applications(migration_config['APP_APP_ID'])
        if not app_instance_id:
            print(f"Application for {migration_config['APP_APP_ID']} not found in the hub")
            sys.exit(1)
        instances = hub.get_application_instances(app_instance_id)
        if len(instances) == 0:
            print("Nothing to migrate")
            sys.exit(1)
        print(f"Found {len(instances)} instances of application {migration_config['APP_APP_ID']}")
        for inst in instances:
            instance_details = hub.get_application_instance(inst['application_instance_id'])
            if instance_details.get('status') != 'Ready':
                print(f"Instance {inst['application_instance_id']} is not in Ready status, skipping")
                _confirm("Do you want to try with next? [y/n]")
                continue
            if instance_details.get('package_version') != migration_config['APP_SAFE_DELETE_VERSION']:
                print(f"Instance {inst['application_instance_id']} is not in proper version for safe upgrade")
                _confirm("Do you want to try with next? [y/n]")
                continue
            settings = hub.get_application_settings(inst['application_instance_id'])
            subscription = _setting_from_settings(settings, migration_config['SUBSCRIPTION_ID_SETTING'])
            activation_params = _populate_params(settings, migration_config['PARAMS_MAPPING'])
            if not subscription:
                print(f"Instance {inst['application_instance_id']} has no setting for "
                      f"{migration_config['SUBSCRIPTION_ID_SETTING']} and due it can't "
                      f"be discovered the subscription"
                      )
                exit(1)
            print(f"Instance {inst['application_instance_id']} is from subscription {subscription}")
            bss_subscriptions = hub.aps.get(
                f'aps/2/resources?implementing({constants.BSS_SUBSCRIPTION}),eq(subscriptionId,{subscription}),select(servicePlan,account)'
            ).json()
            bss_subscription = bss_subscriptions[0]
            if bss_subscription['status'] != 'ACTIVE':
                print(
                    f"Subscription {subscription} is not active, is in status {bss_subscription['status']} "
                    "and due it can't be migrated"
                )
                _confirm("Do you want to try with next? [y/n]")
                continue
            period_switches = hub.aps.post(
                f'aps/2/resources/{bss_subscription["servicePlan"]["aps"]["id"]}/planPeriodSwitches',
                json=bss_subscription['subscriptionPeriod']
            ).json()

            if _potential_plans(period_switches) != 2:
                print(
                    f"Source plan for subscription {subscription} has more than one upgrade path,"
                    f"Is not possible to migrate."
                )
                exit(1)
            new_plan = _select_new_plan(bss_subscription["servicePlan"]["aps"]["id"], period_switches)
            resources = hub.aps.get(f'aps/2/resources/{bss_subscription["aps"]["id"]}/resources').json()
            new_plan_data = hub.aps.get(f'aps/2/resources/{new_plan}').json()
            if not new_plan_data:
                print("Error obtaining new plan")
                exit(1)

            order = {
                "type": "CHANGE",
                "subscriptionId": bss_subscription['aps']['id'],
                "period": bss_subscription['subscriptionPeriod'],
                "planId": new_plan,
                "resources": []
            }

            for resource in resources:
                if not resource['resourceId'] in mappings:
                    print(f"Resource {resource['id']} not present in mappings! unsure what to do")
                    exit(1)
                new_resource_id = ""
                for new_resource in new_plan_data['resourceRates']:
                    if new_resource['resourceId'] in mappings[resource['resourceId']]:
                        new_resource_id = new_resource['resourceId']
                order['resources'].append({
                    "resourceId": new_resource_id,
                    "amount": int(resource['included'] + resource['additional'])
                })
            print(f"Change order with following data: {order}")
            change_order = hub.aps.post('/aps/2/services/order-manager/orders', json=order).json()
            print(f"Order created {change_order}")
            oa_subscription = hub.aps.get(
                f'aps/2/resources?implementing({constants.OSS_SUBSCRIPTION}),eq(subscriptionId,{subscription})'
            ).json()
            tenant = _get_new_tenant(activation_params, migration_config['CONNECT_PRODUCT_ID'])
            print(f"The tenant to be activated: {json.dumps(tenant)}")
            while not _is_order_ready(change_order['orderId'], hub.aps):
                print("Order provisioning did not finished")
                _confirm("Let's wait? [y/n]")
            activation = hub.aps.post('aps/2/resources', json=tenant, subscription=oa_subscription[0]['aps']['id'])
            print(f"Result of activation resource: {activation.json()}")
            connect_client = ConnectClient(
                api_key=migration_config['CONNECT_API_KEY'],
                endpoint=migration_config['CONNECT_API_ENDPOINT'],
                use_specs=False,
            )
            while True:
                request = _connect_purchase_request_pending(connect_client, subscription, migration_config['CONNECT_PRODUCT_ID'])
                if not request:
                    print("Order did not reached connect yet")
                    _confirm("Let's wait? [y/n]")
                break
            print(
                f'Approving request {request} with template '
                f'{migration_config["CONNECT_ACTIVATION_TEMPLATE"]}'
            )
            try:
                connect_client.requests[request].action('approve').post({
                    'template_id': migration_config['CONNECT_ACTIVATION_TEMPLATE']
                })
            except ClientError as error:
                print(f'Error while approving request {request}')
                print(f'Error: {error}')
                print('Please approve it manually')
            print(f"Migration over for subscription {subscription}")



def _populate_params(instance_settings, configuration_map):
    activationParams = []
    for key in configuration_map:
        value = _setting_from_settings(instance_settings, key)
        if not value:
            print(f"App instance has no key {key}, probably there is a missconfiguration or broken instance")
            exit(1)
        activationParams.append({
            "key": configuration_map[key],
            "value": value
        })
    return activationParams


def _get_new_tenant(activationParams, productID):
    return {
        "aps": {
            "type": f"http://aps.odin.com/app/{productID}/tenant/1"
        },
        "activationParams": activationParams
    }


def _load_mappings(mappings, aps):
    aps_mapping = {}
    for map in mappings:
        source = aps.get(
            f'aps/2/resources?implementing(http://www.odin.com/billing/Resource/1.3),eq(id,{map})'
        ).json()
        if source and len(source) == 1:
            destinations = []
            for dest in mappings[map]:
                destination = aps.get(
                    f'aps/2/resources?implementing(http://www.odin.com/billing/Resource/1.3),eq(id,{dest})'
                ).json()
                if not destination or len(destination) != 1:
                    print(f"Wrong mapping, resource {dest} does not exist")
                    exit(1)
                destinations.append(destination[0]['aps']['id'])
            aps_mapping[source[0]['aps']['id']] = destinations
    return aps_mapping


def _potential_plans(available_plans):
    counter = 0
    for plan in available_plans:
        if 'target' in plan and 'planId' in plan['target']:
            counter += 1
    return counter


def _select_new_plan(old_plan, available_plans):
    for plan in available_plans:
        if plan['target']['planId'] != old_plan:
            return plan['target']['planId']


def _setting_from_settings(settings, key):
    for setting in settings:
        if setting['name'] == key:
            return setting['value']
    return None


def _is_order_ready(order, aps):
    order = aps.get(f'aps/2/services/order-manager/orders/{order}').json()
    return True if order['provisioningStatus'] == 'COMPLETED' else False


def _connect_purchase_request_pending(client, subscriptionId, productId):
    r = R().asset.product.id.eq(f'{productId}')
    r &= R().asset.external_id.eq(f'{subscriptionId}')
    r &= R().type.eq('purchase')
    r &= R().status.oneof(['pending','inquiring', 'approved', 'failed', 'tiers_setup'])
    try:
        requests = client.requests.filter(r).all()
    except ClientError as error:
        print(
            f'Error when retriving data from connect, status code: {error.status_code}',
            f'Errors: {error.errors}'
        )
        return None
    if requests.count() != 1:
        return None
    if requests[0]['status'] != 'pending':
        print(
            f'Request created with id {requests[0]["id"]} is in status {requests[0]["status"]}.'
            'Check manually why is not pending'
        )
        return None
    return requests[0]['id']

def _confirm(prompt):
    while True:
        try:
            answer = strtobool(input(prompt))
            if not answer:
                sys.exit(1)

        except ValueError:
            continue
        except EOFError:
            sys.exit(1)
        else:
            break
    return answer


def _check_binding(check_config, get_config_info):
    state_not_initiated = "\tNot initiated"
    state_is_ready = "\thost: {}\n\tuser: {}"
    state_config_corrupted = "\tConfig file is corrupted: {}"

    if not check_config():
        return state_not_initiated

    try:
        info = get_config_info()
    except Exception as e:
        return state_config_corrupted.format(e)

    if info == NULL_CFG_INFO:
        return state_config_corrupted.format("binding attributes are not assigned")
    else:
        host, user = info
        return state_is_ready.format(host, user)


def _get_hub_info():
    if not os.path.exists(CFG_FILE_PATH):
        return NULL_CFG_INFO

    with open(CFG_FILE_PATH) as f:
        hub_cfg = json.load(f)

    host = "{}:{}".format(hub_cfg['host'], hub_cfg['port'])
    user = hub_cfg['user']
    return (host, user)


def main():
    try:
        log_entry = ("=============================\n{}\n".format(" ".join(sys.argv)))
        Logger(LOG_FILE).log(log_entry)
        fire.Fire(Migrator, name='migrator')
    except Exception as e:
        print("Error: {}".format(e))
        print(traceback.format_exc())
        sys.exit(1)


if __name__ == '__main__':
    main()