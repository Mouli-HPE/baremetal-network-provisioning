# Copyright (c) 2015 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from oslo_config import cfg
from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import uuidutils
import requests

from neutron import context as neutron_context

from baremetal_network_provisioning.common import constants as hp_const
from baremetal_network_provisioning.common import exceptions as hp_exec
from baremetal_network_provisioning.db import bm_nw_provision_db as db
from baremetal_network_provisioning.ml2 import network_provisioning_api as api


LOG = logging.getLogger(__name__)
hp_opts = [
    cfg.StrOpt('base_url',
               help=_("Base HTTP URL of  SDN controller")),
    cfg.StrOpt('auth_token',
               default='AuroraSdnToken37',
               help=_("Authentication token for SDN controller")),
    cfg.StrOpt('ca_cert',
               default=None,
               help=_("full path to the certificate file containing the"
                      "SDN Controller")),
    cfg.StrOpt('timeout',
               default=30,
               help=_("Timeout in seconds to wait for SDN HTTP request"
                      "completion.")),
]
cfg.CONF.register_opts(hp_opts, "default")


class HPNetworkProvisioningDriver(api.NetworkProvisioningApi):
    """Back-end mechanism driver implementation for bare

    metal provisioning.
    """

    def __init__(self):
        """initialize the network provision driver."""
        self.context = neutron_context.get_admin_context()
        self.conf = cfg.CONF
        self.base_url = self.conf.default.base_url
        self.auth_token = self.conf.default.auth_token
        self.ca_cert = self.conf.default.ca_cert
        self.verify_cert = False
        self.timeout = float(self.conf.default.get('timeout'))

    def create_port(self, port_dict):
        """create_port. This call makes the REST request to the external

        SDN controller for provision VLAN for the switch port where
        bare metal is connected.
        """
        LOG.debug("create_port port_dict %(port_dict)s",
                  {'port_dict': port_dict})
        lag_id = None
        switch_port_id = uuidutils.generate_uuid()
        switchport = port_dict['port']['switchports']
        switch_mac_id = switchport[0]['switch_id']
        rec_dict = {'id': switch_port_id,
                    'switch_id': switch_mac_id,
                    'port_name': switchport[0]['port_id'],
                    'lag_id': None}
        switch_url = self._frame_switch_url(switch_mac_id)
        try:
            resp = self._do_request('GET', switch_url, None)
            LOG.debug("response from SDN controller %(resp)s ",
                      {'resp': resp})
            resp.raise_for_status()
        except requests.exceptions.Timeout as e:
            LOG.error(" Request timed out in SDN controller : %s", e)
            raise hp_exec.HPNetProvisioningDriverError(msg="Timed Out"
                                                           "with SDN"
                                                           "controller: %s"
                                                           % e)
        except requests.exceptions.SSLError as e:
            LOG.error(" SSLError to SDN controller : %s", e)
            raise hp_exec.SslCertificateValidationError(msg=e)
        except Exception as e:
            LOG.error(" ConnectionFailed to SDN controller : %s", e)
            raise hp_exec.ConnectionFailed(msg=e)
        mapping_dict = {'neutron_port_id': port_dict['port']['id'],
                        'switch_port_id': switch_port_id,
                        'lag_id': lag_id,
                        'access_type': None,
                        'segmentation_id': None,
                        'bind_requested': False}
        session = self.context.session
        if resp.status_code == requests.codes.OK:
            with session.begin(subtransactions=True):
                db.add_hp_switch_port(self.context, rec_dict)
                db.add_hp_ironic_switch_port_mapping(self.context,
                                                     mapping_dict)
        else:
            LOG.error(" Given physical switch does not exists")
            raise hp_exec.HPNetProvisioningDriverError(msg="Failed to"
                                                       "communicate with"
                                                       "SDN Controller:")

    def bind_port_to_segment(self, port_dict):
        """bind_port_to_network. This call makes the REST request to the

        external SDN controller for provisioning VLAN for the switch port where
        bare metal is connected.
        """
        switchport = port_dict['port']['switchports']
        switch_mac_id = switchport[0]['switch_id']
        segmentation_id = port_dict['port']['segmentation_id']
        bind_dict = {'neutron_port_id': port_dict['port']['id'],
                     'access_type': hp_const.ACCESS,
                     'segmentation_id': segmentation_id,
                     'bind_requested': True,
                     }
        if switch_mac_id:
            put_url = self._frame_port_url(switch_mac_id)
        try:
            port_pay_load = self._get_port_pay_load(port_dict)
            LOG.debug("port_pay_load %(port_pay_load)s ",
                      {'port_pay_load': port_pay_load})
            resp = self._do_request('PUT', put_url, port_pay_load)
            resp.raise_for_status()
            if resp.status_code == 204:
                LOG.debug("PUT request for physicalInterfaces is succeeded")
                db.update_hp_ironic_swport_map_with_seg_id(self.context,
                                                           bind_dict)
                return hp_const.BIND_SUCCESS
            else:
                return hp_const.BIND_FAILURE
        except requests.exceptions.Timeout as e:
            LOG.error("Timed out in SDN controller : %s", e)
            raise hp_exec.HPNetProvisioningDriverError(msg="Timed Out"
                                                           "with SDN"
                                                           "controller: %s"
                                                           % e)
        except requests.exceptions.SSLError as e:
            LOG.error("SSLError to SDN controller : %s", e)
            raise hp_exec.SslCertificateValidationError(msg=e)
        except Exception as e:
            LOG.error("ConnectionFailed to SDN controller : %s", e)
            raise hp_exec.ConnectionFailed(msg=e)

    def update_port(self, port_dict):
        """update_port. This call makes the REST request to the external

        SDN controller for provision VLAN on switch port where bare metal
        is connected.
        """
        return

    def delete_port(self, port_id):
        """delete_port. This call makes the REST request to the external

        SDN controller for un provision VLAN for the switch port where
        bare metal is connected.
        """
        # TODO(Selvakumar) need to implement the REST call logic
        neutron_port_dict = {'neutron_port_id': port_id}
        switch_port_id = db.get_hp_ironic_swport_map_by_id(self.context,
                                                           neutron_port_dict)
        switch_port_dict = {'id': switch_port_id.switch_port_id}
        db.delete_hp_switch_port(self.context, switch_port_dict)

    def _do_request(self, method, urlpath, obj):
        """Send REST request to the SDN controller."""
        headers = {'Content-Type': 'application/json'}
        headers['X-Auth-Token'] = self.auth_token
        if not self.ca_cert:
            self.verify_cert = False
        else:
            self.verify_cert = self.ca_cert if self.ca_cert else True
        data = jsonutils.dumps(obj, indent=2) if obj else None
        response = requests.request(method, url=urlpath,
                                    headers=headers, data=data,
                                    verify=self.verify_cert,
                                    timeout=self.timeout)
        response.raise_for_status()
        return response

    def _frame_port_url(self, switch_id):
        """Frame the physical port URL for SDN controller."""
        switch_port_url = switch_id + '/physicalInterfaces/'
        url = self.base_url + '/' + switch_port_url
        return url

    def _frame_switch_url(self, switch_id):
        """Frame the physical switch URL for SDN controller."""
        url = self.base_url + '/' + switch_id
        return url

    def _get_port_pay_load(self, port_dict):
        """form  port payload for SDN controller REST request."""
        switchports = port_dict['port']['switchports']
        port_list = []
        bind_port_dict = port_dict.get('port')
        segmentation_id = bind_port_dict['segmentation_id']
        seg_id_list = []
        seg_id_list.append(str(segmentation_id))
        access_type = bind_port_dict['access_type']
        for switch_port in switchports:
            port_id = switch_port['port_id']
            res_port_dict = {'port': port_id,
                             'type': access_type,
                             'vids': seg_id_list}
            port_list.append(res_port_dict)
        return {'ports': port_list}