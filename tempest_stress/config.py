# Copyright 2012 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from oslo_config import cfg

from tempest import config  # noqa

stress_group = cfg.OptGroup(name='stress', title='Stress Test Options')

StressGroup = [
    cfg.StrOpt('nova_logdir',
               help='Directory containing log files on the compute nodes'),
    cfg.IntOpt('max_instances',
               default=16,
               help='Maximum number of instances to create during test.'),
    cfg.StrOpt('controller',
               help='Controller host.'),
    # new stress options
    cfg.StrOpt('target_controller',
               help='Controller host.'),
    cfg.StrOpt('target_ssh_user',
               help='ssh user.'),
    cfg.StrOpt('target_private_key_path',
               help='Path to private key.'),
    cfg.StrOpt('target_logfiles',
               help='regexp for list of log files.'),
    cfg.IntOpt('log_check_interval',
               default=60,
               help='time (in seconds) between log file error checks.'),
    cfg.IntOpt('default_thread_number_per_action',
               default=4,
               help='The number of threads created while stress test.'),
    cfg.BoolOpt('leave_dirty_stack',
                default=False,
                help='Prevent the cleaning (tearDownClass()) between'
                     ' each stress test run if an exception occurs'
                     ' during this run.'),
    cfg.BoolOpt('full_clean_stack',
                default=False,
                help='Allows a full cleaning process after a stress test.'
                     ' Caution : this cleanup will remove every objects of'
                     ' every project.')
]

class StressConfigPrivate(object):

    DEFAULT_CONFIG_FILE = "stress_tests.conf"

    def __init__(self):
        """Initialize a configuration from a conf directory and conf file."""
        super(StressConfigPrivate, self).__init__()

        # Environment variables override defaults.
        conf_file = os.environ.get('STRESS_TEST_CONFIG', self.DEFAULT_CONFIG_FILE)
        conf_dirs = list()
        conf_dir = os.path.dirname(os.path.dirname(__file__))
        conf_path = os.path.join(conf_dir, conf_file)
        if not os.path.isfile(conf_path):
            if os.environ.get('STRESS_TEST_CONFIG_DIR'):
                conf_dir = os.environ.get('STRESS_TEST_CONFIG_DIR')
                conf_path = os.path.join(conf_dir, conf_file)
            if not os.path.isfile(conf_path):
                raise Exception('Config file: %s could not be found'
                                % self.DEFAULT_CONFIG_FILE)
        LOG.info("Using tempest_stress config file %s" % conf_path)
        conf = cfg.CONF
        conf([], project='tempest_stress', default_config_files=[conf_path])
        assert False, conf_path
        conf.register_group(stress_group)
        group_name = stress_group.name
        for opt in StressGroup:
            conf.register_opt(opt, group=group_name)
        self.stress = cfg.CONF.stress
        conf.log_opt_values(LOG, std_logging.DEBUG)


class StressConfigProxy(object):
    _config = None

    def __getattr__(self, attr):
        if not self._config:
            self._config = StressConfigPrivate()

        return getattr(self._config, attr)

CONF = StressConfigProxy()
