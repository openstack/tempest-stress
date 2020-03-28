# Copyright 2013 Quanta Research Cambridge, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import multiprocessing
import os
import signal
import time

from oslo_log import log as logging
from oslo_utils import importutils
import six
from tempest import clients
from tempest.common import credentials_factory as credentials
from tempest.common.utils import data_utils
from tempest import config
from tempest import exceptions
from tempest.lib.common import cred_client
from tempest.lib.common import ssh

from tempest_stress import cleanup
from tempest_stress import config as stress_cfg

CONF = config.CONF
STRESS_CONF = stress_cfg.CONF

LOG = logging.getLogger(__name__)
processes = []


def do_ssh(command, host, ssh_user, ssh_key=None):
    ssh_client = ssh.Client(host, ssh_user, key_filename=ssh_key)
    try:
        return ssh_client.exec_command(command)
    except exceptions.SSHExecCommandFailed:
        LOG.error('do_ssh raise exception. command:%s, host:%s.'
                  % (command, host))
        return None


def _get_compute_nodes(controller, ssh_user, ssh_key=None):
    """Returns a list of active compute nodes.

    List is generated by running nova-manage on the controller.
    """
    nodes = []
    cmd = "nova-manage service list | grep ^nova-compute"
    output = do_ssh(cmd, controller, ssh_user, ssh_key)
    if not output:
        return nodes
    # For example: nova-compute xg11eth0 nova enabled :-) 2011-10-31 18:57:46
    # This is fragile but there is, at present, no other way to get this info.
    for line in output.split('\n'):
        words = line.split()
        if len(words) > 0 and words[4] == ":-)":
            nodes.append(words[1])
    return nodes


def _has_error_in_logs(logfiles, nodes, ssh_user, ssh_key=None,
                       stop_on_error=False):
    """Detect errors in nova log files on the controller and compute nodes."""
    grep = 'egrep "ERROR|TRACE" %s' % logfiles
    ret = False
    for node in nodes:
        errors = do_ssh(grep, node, ssh_user, ssh_key)
        if len(errors) > 0:
            LOG.error('%s: %s' % (node, errors))
            ret = True
            if stop_on_error:
                break
    return ret


def sigchld_handler(signalnum, frame):
    """Signal handler (only active if stop_on_error is True)."""
    for process in processes:
        if (not process['process'].is_alive() and
                process['process'].exitcode != 0):
            signal.signal(signalnum, signal.SIG_DFL)
            terminate_all_processes()
            break


def terminate_all_processes(check_interval=20):
    """Goes through the process list and terminates all child processes."""
    LOG.info("Stopping all processes.")
    for process in processes:
        if process['process'].is_alive():
            try:
                process['process'].terminate()
            except Exception:
                pass
    time.sleep(check_interval)
    for process in processes:
        if process['process'].is_alive():
            try:
                pid = process['process'].pid
                LOG.warning("Process %d hangs. Send SIGKILL." % pid)
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        process['process'].join()


def stress_openstack(tests, duration, max_runs=None, stop_on_error=False):
    """Workload driver. Executes an action function against a nova-cluster."""
    admin_manager = credentials.AdminManager()

    ssh_user = STRESS_CONF.stress.target_ssh_user
    ssh_key = STRESS_CONF.stress.target_private_key_path
    logfiles = STRESS_CONF.stress.target_logfiles
    log_check_interval = int(STRESS_CONF.stress.log_check_interval)
    default_thread_num = int(
        STRESS_CONF.stress.default_thread_number_per_action)
    if logfiles:
        controller = STRESS_CONF.stress.target_controller
        computes = _get_compute_nodes(controller, ssh_user, ssh_key)
        for node in computes:
            do_ssh("rm -f %s" % logfiles, node, ssh_user, ssh_key)
    skip = False
    for test in tests:
        for service in test.get('required_services', []):
            if not CONF.service_available.get(service):
                skip = True
                break
        if skip:
            break
        # TODO(andreaf) This has to be reworked to use the credential
        # provider interface. For now only tests marked as 'use_admin' will
        # work.
        if test.get('use_admin', False):
            manager = admin_manager
        else:
            raise NotImplementedError('Non admin tests are not supported')
        for p_number in range(test.get('threads', default_thread_num)):
            if test.get('use_isolated_tenants', False):
                username = data_utils.rand_name("stress_user")
                tenant_name = data_utils.rand_name("stress_tenant")
                password = "pass"
                if CONF.identity.auth_version == 'v2':
                    identity_client = admin_manager.identity_client
                    projects_client = admin_manager.tenants_client
                    roles_client = admin_manager.roles_client
                    users_client = admin_manager.users_client
                    domains_client = None
                else:
                    identity_client = admin_manager.identity_v3_client
                    projects_client = admin_manager.projects_client
                    roles_client = admin_manager.roles_v3_client
                    users_client = admin_manager.users_v3_client
                    domains_client = admin_manager.domains_client
                domain = (identity_client.auth_provider.credentials.
                          get('project_domain_name', 'Default'))
                credentials_client = cred_client.get_creds_client(
                    identity_client, projects_client, users_client,
                    roles_client, domains_client, project_domain_name=domain)
                project = credentials_client.create_project(
                    name=tenant_name, description=tenant_name)
                user = credentials_client.create_user(username, password,
                                                      project, "email")
                # Add roles specified in config file
                for conf_role in CONF.auth.tempest_roles:
                    credentials_client.assign_user_role(user, project,
                                                        conf_role)
                creds = credentials_client.get_credentials(user, project,
                                                           password)
                manager = clients.Manager(credentials=creds)

            test_obj = importutils.import_class(test['action'])
            test_run = test_obj(manager, max_runs, stop_on_error)

            kwargs = test.get('kwargs', {})
            test_run.setUp(**dict(six.iteritems(kwargs)))

            LOG.debug("calling Target Object %s" %
                      test_run.__class__.__name__)

            mp_manager = multiprocessing.Manager()
            shared_statistic = mp_manager.dict()
            shared_statistic['runs'] = 0
            shared_statistic['fails'] = 0

            p = multiprocessing.Process(target=test_run.execute,
                                        args=(shared_statistic,))

            process = {'process': p,
                       'p_number': p_number,
                       'action': test_run.action,
                       'statistic': shared_statistic}

            processes.append(process)
            p.start()
    if stop_on_error:
        # NOTE(mkoderer): only the parent should register the handler
        signal.signal(signal.SIGCHLD, sigchld_handler)
    end_time = time.time() + duration
    had_errors = False
    try:
        while True:
            if max_runs is None:
                remaining = end_time - time.time()
                if remaining <= 0:
                    break
            else:
                remaining = log_check_interval
                all_proc_term = True
                for process in processes:
                    if process['process'].is_alive():
                        all_proc_term = False
                        break
                if all_proc_term:
                    break

            time.sleep(min(remaining, log_check_interval))
            if stop_on_error:
                if any([True for proc in processes
                        if proc['statistic']['fails'] > 0]):
                    break

            if not logfiles:
                continue
            if _has_error_in_logs(logfiles, computes, ssh_user, ssh_key,
                                  stop_on_error):
                had_errors = True
                break
    except KeyboardInterrupt:
        LOG.warning("Interrupted, going to print statistics and exit ...")

    if stop_on_error:
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
    terminate_all_processes()

    sum_fails = 0
    sum_runs = 0

    LOG.info("Statistics (per process):")
    for process in processes:
        if process['statistic']['fails'] > 0:
            had_errors = True
        sum_runs += process['statistic']['runs']
        sum_fails += process['statistic']['fails']
        print("Process %d (%s): Run %d actions (%d failed)" % (
            process['p_number'],
            process['action'],
            process['statistic']['runs'],
            process['statistic']['fails']))
    print("Summary:")
    print("Run %d actions (%d failed)" % (sum_runs, sum_fails))

    if not had_errors and STRESS_CONF.stress.full_clean_stack:
        LOG.info("cleaning up")
        cleanup.cleanup()
    if had_errors:
        return 1
    else:
        return 0
